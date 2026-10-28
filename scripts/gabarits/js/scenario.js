// scenario.js — fermer une liaison (ou une route) et mesurer l'effet, en JavaScript pur.
//
// Reproduit src/jumeau_cirano/scenarios/resilience.py (tests/test_parite_js.py) :
//   les itinéraires qui empruntent une route fermée sont interdits ; le logit redistribue les camions
//   une paire privée de tout itinéraire prend le plus court chemin du réseau dégradé (tout-ou-rien),
//   sans transiter par un point frontière ; sans chemin, ses trajets ne sont pas desservis
//   Δ heures équivalentes (logsum, classement) ; Δ du meilleur itinéraire ; Δ camions-heures et -km
// Dépend de modele.js (ModeleJumeau) ; aucun accès au DOM.
"use strict";

const ScenarioJumeau = (() => {
  const MJ = typeof ModeleJumeau !== "undefined" ? ModeleJumeau : require("./modele.js");

  const parPaire = (M, x) => { const s = new Float64Array(M.N); for (let p = 0; p < M.P; p++) s[M.paire[p]] += x[p]; return s; };
  const minParPaire = (M, x) => { const m = new Float64Array(M.N).fill(Infinity); for (let p = 0; p < M.P; p++) if (x[p] < m[M.paire[p]]) m[M.paire[p]] = x[p]; return m; };

  // Graphe des routes : liste d'adjacence nœud → [(voisin, route)]
  function graphe(M) {
    if (M._adj) return M._adj;
    const adj = Array.from({ length: M.noeuds.id.length }, () => []);
    for (let r = 0; r < M.R; r++) { adj[M.routes.a[r]].push([M.routes.b[r], r]); adj[M.routes.b[r]].push([M.routes.a[r], r]); }
    return (M._adj = adj);
  }

  // Plus court chemin en minutes (Dijkstra) sans routes fermées ; un point frontière n'est jamais une étape
  function plusCourt(M, src, dst, fermees) {
    const adj = graphe(M), n = adj.length, dist = new Float64Array(n).fill(Infinity), pred = new Int32Array(n).fill(-1);
    const fait = new Uint8Array(n), front = M.noeuds.frontiere;
    dist[src] = 0;
    for (;;) {
      let u = -1, du = Infinity;
      for (let v = 0; v < n; v++) if (!fait[v] && dist[v] < du) { du = dist[v]; u = v; }   // ~200 nœuds : tableau suffit
      if (u < 0 || u === dst) break;
      fait[u] = 1;
      if (u !== src && front[u]) continue;
      for (const [v, r] of adj[u]) {
        if (fermees.has(r) || (front[v] && v !== dst)) continue;
        const d = du + M.routes.minutes[r];
        if (d < dist[v]) { dist[v] = d; pred[v] = r; }
      }
    }
    if (!isFinite(dist[dst])) return null;
    const routes = [];
    for (let v = dst; v !== src;) { const r = pred[v]; routes.push(r); v = M.routes.a[r] === v ? M.routes.b[r] : M.routes.a[r]; }
    let km = 0; for (const r of routes) km += M.routes.km[r];
    return { minutes: dist[dst], km, routes };
  }

  // Référence (sans fermeture) pour θ : à calculer une fois par θ
  function reference(M, th) {
    const c = MJ.couts(M, th), u = MJ.utilites(M, th, null, c), p = MJ.probabilites(M, th, null, u);
    return { th, couts: c, p, cRef: MJ.coutCompose(M, th, null, u), flux: MJ.fluxRoutes(M, th, p),
             minutesRef: parPaire(M, p.map((x, k) => x * M.minutes[k])), kmRef: parPaire(M, p.map((x, k) => x * M.kmChemin[k])),
             minutesMin: minParPaire(M, M.minutes), coutMin: minParPaire(M, c), servi: parPaire(M, p) };
  }

  // Impacts annuels (2025) de la fermeture simultanée des routes `fermees` (Set d'indices de routes)
  function fermer(M, ref, fermees, coutHoraire, valeurTonne) {
    const th = ref.th, g = th.croissance;
    const touche = new Uint8Array(M.Q);
    for (let q = 0; q < M.Q; q++) for (let j = M.seqDebut[q]; j < M.seqDebut[q + 1]; j++) if (fermees.has(M.seqRoutes[j])) { touche[q] = 1; break; }
    const masque = new Uint8Array(M.P);
    for (let p = 0; p < M.P; p++) masque[p] = touche[M.seq[p]];
    const u = MJ.utilites(M, th, masque, ref.couts), p = MJ.probabilites(M, th, masque, u), cNew = MJ.coutCompose(M, th, masque, u);
    const minutesNew = parPaire(M, p.map((x, k) => x * M.minutes[k])), kmNew = parPaire(M, p.map((x, k) => x * M.kmChemin[k]));
    const servi = parPaire(M, p), surLiaison = parPaire(M, ref.p.map((x, k) => x * masque[k]));
    const coutMinNew = minParPaire(M, ref.couts.map((x, k) => masque[k] ? Infinity : x));
    const flux = MJ.fluxRoutes(M, th, p);
    const dEq = new Float64Array(M.N), dBest = new Float64Array(M.N);
    for (let i = 0; i < M.N; i++) {
      dEq[i] = isFinite(cNew[i]) && isFinite(ref.cRef[i]) ? cNew[i] - ref.cRef[i] : 0;
      dBest[i] = isFinite(coutMinNew[i]) && isFinite(ref.coutMin[i]) ? coutMinNew[i] - ref.coutMin[i] : 0;
    }

    // Paires privées de tout itinéraire par CETTE fermeture : détour tout-ou-rien sur le réseau dégradé
    let nonDesservis = 0;
    const sansChemin = new Uint8Array(M.N);
    let aucun = true;
    for (let i = 0; i < M.N; i++) if (servi[i] < 0.5 && ref.servi[i] > 0.5) { sansChemin[i] = 1; aucun = false; }
    if (!aucun) {
      const meilleur = new Map(), cache = new Map(), vus = new Set();
      for (let k = 0; k < M.P; k++) {
        const i = M.paire[k];
        if (!sansChemin[i]) continue;
        const cle = M.entree[k] + ">" + M.sortie[k], combo = i + ":" + cle;
        if (vus.has(combo)) continue;
        vus.add(combo);
        if (!cache.has(cle)) cache.set(cle, plusCourt(M, M.entree[k], M.sortie[k], fermees));
        const ch = cache.get(cle);
        if (!ch) continue;
        const mn = ch.minutes + M.minAcces[k];
        if (!meilleur.has(i) || mn < meilleur.get(i).minutes) meilleur.set(i, { minutes: mn, km: ch.km + M.kmAcces[k], routes: ch.routes });
      }
      for (let i = 0; i < M.N; i++) {
        if (!sansChemin[i]) continue;
        const ch = meilleur.get(i), camions = g * M.n3[i];
        if (ch) {
          minutesNew[i] = ch.minutes; kmNew[i] = ch.km;
          dEq[i] = dBest[i] = Math.max(ch.minutes - ref.minutesMin[i], 0);
          for (const r of ch.routes) flux[r] += camions / 365;
        } else {
          nonDesservis += camions; minutesNew[i] = ref.minutesRef[i]; kmNew[i] = ref.kmRef[i];
        }
      }
    }

    let dh = 0, dkm = 0, deq = 0, dbest = 0, camLiaison = 0, tonnes = 0;
    for (let i = 0; i < M.N; i++) {
      const c = g * M.n3[i];
      dh += c * (minutesNew[i] - ref.minutesRef[i]); dkm += c * (kmNew[i] - ref.kmRef[i]);
      deq += c * dEq[i]; dbest += c * dBest[i]; camLiaison += c * surLiaison[i]; tonnes += g * M.t3[i] * surLiaison[i];
    }
    return {
      camions_sur_liaison: camLiaison, delta_heures_equivalentes: deq / 60, delta_heures_meilleur: dbest / 60,
      delta_camions_heures: dh / 60, delta_camions_km: dkm, trajets_non_desservis: nonDesservis,
      tonnes_sur_liaison: tonnes, valeur_sur_liaison: tonnes * valeurTonne, cout_perturbation: deq / 60 * coutHoraire,
      flux,
    };
  }

  return { reference, fermer, plusCourt };
})();

if (typeof module !== "undefined") module.exports = ScenarioJumeau;
