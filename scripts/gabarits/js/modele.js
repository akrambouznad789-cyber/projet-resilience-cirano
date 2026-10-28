// modele.js — modèle d'affectation des camions (logit à taille de chemin), en JavaScript pur.
//
// Reproduit exactement src/jumeau_cirano/modele/affectation.py (tests/test_parite_js.py : écart < 1e-6).
// Aucune dépendance, aucun accès au DOM : réutilisable tel quel dans l'application web du jumeau officiel.
//
//   coût_p = Σ_r∈p (minutes_r + β_ha·km_hors_auto_r + β_e·km_estime_r) + minutes_accès_p + Σ_f β_f·passages_pf
//   P(p|i) = softmax sur les itinéraires de la paire i de ( −μ·coût_p + ln PS_p )
//   flux_r = g · Σ_p n3_i(p) · P(p|i) · δ_rp / 365          (camions par jour, 2025)
//   ŷ_s    = Σ_r flux_r · δ_rs + fond[classe_s]             (camions par jour sur la section s)
//
// Données : celles de src/jumeau_cirano/interface/moteur.py (tableaux en base64).
// θ : { mu, beta_hors_autoroute, beta_estime, croissance, fond: [5], beta_frontiere: [F] }
"use strict";

const ModeleJumeau = (() => {
  const TYPES = { f4: Float32Array, f8: Float64Array, i4: Int32Array, u2: Uint16Array, u1: Uint8Array };

  function decoder(t) {
    const bin = typeof atob === "function" ? atob(t.b64) : Buffer.from(t.b64, "base64").toString("binary");
    const octets = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) octets[i] = bin.charCodeAt(i);
    const brut = new TYPES[t.type](octets.buffer);
    return (t.type === "f4" || t.type === "f8") ? Float64Array.from(brut) : brut;   // calculs en double
  }

  // Décode une fois et précalcule ce qui ne dépend pas de θ.
  function preparer(D) {
    const R = D.routes.id.length, M = {
      R, P: D.chemins.paire.n, N: D.paires.n3.n, S: D.sections.n, classes: D.classes, frontieres: D.frontieres,
      routes: { id: D.routes.id, arc: D.routes.arc, a: Int32Array.from(D.routes.a), b: Int32Array.from(D.routes.b),
                minutes: decoder(D.routes.minutes), kmHa: decoder(D.routes.km_hors_auto),
                kmEst: decoder(D.routes.km_estime), km: decoder(D.routes.km) },
      seqDebut: decoder(D.sequences.debut), seqRoutes: decoder(D.sequences.routes),
      paire: decoder(D.chemins.paire), seq: decoder(D.chemins.seq),
      entree: decoder(D.chemins.entree), sortie: decoder(D.chemins.sortie),
      minAcces: decoder(D.chemins.minutes_acces), kmAcces: decoder(D.chemins.km_acces), lnPs: decoder(D.chemins.ln_ps),
      n3: decoder(D.paires.n3), t3: decoder(D.paires.t3),
      rsRoute: decoder(D.sections.route), rsSection: decoder(D.sections.section),
      classe: decoder(D.sections.classe), observees: decoder(D.sections.observees), yObs: decoder(D.sections.y_obs),
      noeuds: D.noeuds,
    };
    M.Q = M.seqDebut.length - 1;
    // Composantes de chaque séquence (somme sur ses routes) : minutes, km hors autoroute, km estimés, km
    M.seqMin = new Float64Array(M.Q); M.seqHa = new Float64Array(M.Q); M.seqEst = new Float64Array(M.Q); M.seqKm = new Float64Array(M.Q);
    for (let q = 0; q < M.Q; q++) {
      for (let j = M.seqDebut[q]; j < M.seqDebut[q + 1]; j++) {
        const r = M.seqRoutes[j];
        M.seqMin[q] += M.routes.minutes[r]; M.seqHa[q] += M.routes.kmHa[r];
        M.seqEst[q] += M.routes.kmEst[r]; M.seqKm[q] += M.routes.km[r];
      }
    }
    // Point frontière de chaque nœud (indice dans θ.beta_frontiere, ou −1)
    const iF = new Map(D.frontieres.map((f, k) => [f, k]));
    M.frontNoeud = Int32Array.from(D.noeuds.id, n => iF.has(n) ? iF.get(n) : -1);
    // Minutes et km de chaque itinéraire (accès compris), indépendants de θ
    M.minutes = new Float64Array(M.P); M.kmChemin = new Float64Array(M.P);
    for (let p = 0; p < M.P; p++) { M.minutes[p] = M.seqMin[M.seq[p]] + M.minAcces[p]; M.kmChemin[p] = M.seqKm[M.seq[p]] + M.kmAcces[p]; }
    return M;
  }

  // Coût généralisé de chaque itinéraire (minutes équivalentes)
  function couts(M, th) {
    const c = new Float64Array(M.P), bf = th.beta_frontiere;
    for (let p = 0; p < M.P; p++) {
      const q = M.seq[p], fe = M.frontNoeud[M.entree[p]], fs = M.frontNoeud[M.sortie[p]];
      c[p] = M.seqMin[q] + th.beta_hors_autoroute * M.seqHa[q] + th.beta_estime * M.seqEst[q] + M.minAcces[p]
           + (fe >= 0 ? bf[fe] : 0) + (fs >= 0 ? bf[fs] : 0);
    }
    return c;
  }

  // −μ·coût + ln PS ; −∞ pour les itinéraires interdits (masque[p] = 1 : route fermée)
  function utilites(M, th, masque, c = couts(M, th)) {
    const u = new Float64Array(M.P);
    for (let p = 0; p < M.P; p++) u[p] = (masque && masque[p]) ? -Infinity : -th.mu * c[p] + M.lnPs[p];
    return u;
  }

  // Maximum par paire (stabilité numérique du softmax et du logsum)
  function maxParPaire(M, u) {
    const m = new Float64Array(M.N).fill(-Infinity);
    for (let p = 0; p < M.P; p++) if (u[p] > m[M.paire[p]]) m[M.paire[p]] = u[p];
    return m;
  }

  // P(itinéraire | paire) ; une paire sans itinéraire permis a des probabilités nulles
  function probabilites(M, th, masque, u = utilites(M, th, masque)) {
    const m = maxParPaire(M, u), s = new Float64Array(M.N), e = new Float64Array(M.P);
    for (let p = 0; p < M.P; p++) {
      const i = M.paire[p], mi = isFinite(m[i]) ? m[i] : 0;
      e[p] = Math.exp(u[p] - mi); s[i] += e[p];
    }
    for (let p = 0; p < M.P; p++) { const si = s[M.paire[p]]; e[p] = si > 0 ? e[p] / si : 0; }
    return e;
  }

  // Coût composite de chaque paire (logsum, minutes équivalentes) ; +∞ sans itinéraire
  function coutCompose(M, th, masque, u = utilites(M, th, masque)) {
    const m = maxParPaire(M, u), s = new Float64Array(M.N), c = new Float64Array(M.N);
    for (let p = 0; p < M.P; p++) { const i = M.paire[p], mi = isFinite(m[i]) ? m[i] : 0; s[i] += Math.exp(u[p] - mi); }
    for (let i = 0; i < M.N; i++) {
      const mi = isFinite(m[i]) ? m[i] : 0;
      c[i] = s[i] > 0 ? -(mi + Math.log(s[i])) / th.mu : Infinity;
    }
    return c;
  }

  // Camions par jour (2025) sur chaque route
  function fluxRoutes(M, th, p) {
    const parSeq = new Float64Array(M.Q), f = new Float64Array(M.R), g = th.croissance / 365;
    for (let k = 0; k < M.P; k++) parSeq[M.seq[k]] += g * M.n3[M.paire[k]] * p[k];
    for (let q = 0; q < M.Q; q++) {
      const v = parSeq[q];
      if (v) for (let j = M.seqDebut[q]; j < M.seqDebut[q + 1]; j++) f[M.seqRoutes[j]] += v;
    }
    return f;
  }

  // Camions par jour prédits sur chaque section (ENR affectée + fond local par classe)
  function camionsSections(M, th, flux) {
    const y = new Float64Array(M.S);
    for (let s = 0; s < M.S; s++) y[s] = th.fond[M.classe[s]];
    for (let k = 0; k < M.rsRoute.length; k++) y[M.rsSection[k]] += flux[M.rsRoute[k]];
    return y;
  }

  // Comparaison aux sections mesurées : R² en log, erreur relative médiane, part à ±30 %
  function metriques(M, y) {
    const n = M.observees.length;
    if (!n) return null;
    const lo = new Float64Array(n), lp = new Float64Array(n), err = new Float64Array(n);
    let moy = 0;
    for (let k = 0; k < n; k++) {
      const yp = y[M.observees[k]], yo = M.yObs[k];
      lo[k] = Math.log1p(yo); lp[k] = Math.log1p(yp); err[k] = Math.abs(yp - yo) / Math.max(yo, 1); moy += lo[k] / n;
    }
    let sr = 0, st = 0, p30 = 0;
    for (let k = 0; k < n; k++) { sr += (lo[k] - lp[k]) ** 2; st += (lo[k] - moy) ** 2; if (err[k] <= 0.3) p30++; }
    const tri = Array.from(err).sort((a, b) => a - b);
    const med = n % 2 ? tri[(n - 1) / 2] : (tri[n / 2 - 1] + tri[n / 2]) / 2;
    return { r2_log: 1 - sr / st, erreur_mediane: med, part_30: p30 / n };
  }

  // Tout le calcul d'un θ : probabilités, flux par route, camions par section, métriques
  function affecter(M, th, masque) {
    const c = couts(M, th), u = utilites(M, th, masque, c), p = probabilites(M, th, masque, u);
    const flux = fluxRoutes(M, th, p), sections = camionsSections(M, th, flux);
    return { couts: c, utilites: u, p, flux, sections, metriques: metriques(M, sections) };
  }

  return { decoder, preparer, couts, utilites, probabilites, coutCompose, fluxRoutes, camionsSections, metriques, affecter };
})();

if (typeof module !== "undefined") module.exports = ModeleJumeau;
