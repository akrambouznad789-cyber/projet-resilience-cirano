// moteur_canvas.js — moteur de carte de secours sans WebGL (Canvas 2D), extrait du gabarit du Volet 2.
// Les gabarits des Volets 1 et 2 en gardent leur propre copie ; la page du jumeau (Volet 3) inclut ce fichier.
// Reproduit la petite partie de l'API MapLibre dont la page a besoin (sources
// GeoJSON, couches fill/line/circle, expressions, états, survol, Marker,
// contrôles). Il prend le relais quand le navigateur n'a pas WebGL.
const MoteurCanvas = (() => {
  const TUILE = 512, LAT_MAX = 85.05;
  const lngX = lng => (lng + 180) / 360;
  const latY = lat => { const s = Math.sin(Math.max(-LAT_MAX, Math.min(LAT_MAX, lat)) * Math.PI / 180); return 0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI); };
  const xLng = x => x * 360 - 180;
  const yLat = y => 360 / Math.PI * Math.atan(Math.exp((0.5 - y) * 2 * Math.PI)) - 90;
  const enLngLat = c => Array.isArray(c) ? c : [c.lng ?? c.lon, c.lat];
  const borner = (v, a, b) => Math.max(a, Math.min(b, v));

  // Expressions MapLibre utilisées par la page
  function evaluer(e, f, etat, zoom) {
    if (!Array.isArray(e)) return e;
    const v = x => evaluer(x, f, etat, zoom);
    switch (e[0]) {
      case "get": return f.properties[e[1]];
      case "feature-state": return etat ? etat[e[1]] : undefined;
      case "zoom": return zoom;
      case "coalesce": for (let i = 1; i < e.length; i++) { const r = v(e[i]); if (r != null) return r; } return null;
      case "match": { const x = v(e[1]); for (let i = 2; i < e.length - 1; i += 2) if (e[i] === x) return v(e[i + 1]); return v(e[e.length - 1]); }
      case "case": for (let i = 1; i < e.length - 1; i += 2) if (v(e[i])) return v(e[i + 1]); return v(e[e.length - 1]);
      case "step": { const x = v(e[1]); let r = v(e[2]); for (let i = 3; i < e.length; i += 2) { if (x >= e[i]) r = v(e[i + 1]); else break; } return r; }
      case "interpolate": {
        const x = v(e[2]);
        if (x <= e[3]) return v(e[4]);
        for (let i = 3; i < e.length - 2; i += 2) {
          if (x <= e[i + 2]) { const t = (x - e[i]) / (e[i + 2] - e[i]), a = v(e[i + 1]); return a + (v(e[i + 3]) - a) * t; }
        }
        return v(e[e.length - 1]);
      }
      case "==": return v(e[1]) === v(e[2]);
      case "<": return v(e[1]) < v(e[2]);
      case "*": return v(e[1]) * v(e[2]);
      case "+": return v(e[1]) + v(e[2]);
      case "sqrt": return Math.sqrt(v(e[1]));
      default: return e;
    }
  }

  function preparer(donnees, promoteId) {
    return donnees.features.map((f, i) => {
      const g = f.geometry, o = { properties: f.properties || {}, _id: promoteId ? (f.properties || {})[promoteId] : i, x0: Infinity, y0: Infinity, x1: -Infinity, y1: -Infinity };
      const proj = coords => {
        const a = new Float64Array(coords.length * 2);
        coords.forEach(([lng, lat], k) => {
          const x = lngX(lng), y = latY(lat); a[2 * k] = x; a[2 * k + 1] = y;
          if (x < o.x0) o.x0 = x; if (x > o.x1) o.x1 = x; if (y < o.y0) o.y0 = y; if (y > o.y1) o.y1 = y;
        });
        return a;
      };
      if (g.type === "Point") o.pt = proj([g.coordinates]);
      else if (g.type === "LineString") o.lignes = [proj(g.coordinates)];
      else if (g.type === "MultiLineString") o.lignes = g.coordinates.map(proj);
      else if (g.type === "Polygon") o.polys = [g.coordinates.map(proj)];
      else if (g.type === "MultiPolygon") o.polys = g.coordinates.map(p => p.map(proj));
      return o;
    });
  }

  function distSegment(px, py, ax, ay, bx, by) {
    const dx = bx - ax, dy = by - ay, l2 = dx * dx + dy * dy;
    const t = l2 ? borner(((px - ax) * dx + (py - ay) * dy) / l2, 0, 1) : 0;
    return Math.hypot(px - ax - t * dx, py - ay - t * dy);
  }

  class Carte {
    constructor(o) {
      this._cont = typeof o.container === "string" ? document.getElementById(o.container) : o.container;
      this._canvas = Object.assign(document.createElement("canvas"), { className: "mc-canvas" });
      this._cont.appendChild(this._canvas);
      this._ctx = this._canvas.getContext("2d");
      this._sources = {}; this._couches = []; this._etats = {}; this._ecoute = {}; this._marqueurs = []; this._coins = {};
      this._centre = [-71, 47]; this._zoom = 4;
      this._fond = (o.style?.layers || []).find(c => c.type === "background")?.paint?.["background-color"] || "#fff";
      this._redim();
      if (o.bounds) this.fitBounds(o.bounds, { ...(o.fitBoundsOptions || {}), duration: 0 });
      new ResizeObserver(() => { this._redim(); this._planifier(false); }).observe(this._cont);
      this._souris();
      setTimeout(() => { this._emettre("load", {}); this._planifier(false); }, 0);
    }
    // --- API utilisée par la page ---
    on(type, fn) { (this._ecoute[type] ||= []).push(fn); return this; }
    addSource(id, def) { this._sources[id] = { promoteId: def.promoteId, features: preparer(def.data, def.promoteId), version: 0 }; }
    getSource(id) { return { setData: d => { const s = this._sources[id]; s.features = preparer(d, s.promoteId); s.version++; this._planifier(false); } }; }
    addLayer(def) { this._couches.push({ ...def, layout: { ...(def.layout || {}) }, paint: { ...(def.paint || {}) }, filter: def.filter || null }); this._planifier(false); }
    getLayer(id) { return this._couches.find(c => c.id === id); }
    setLayoutProperty(id, p, v) { this.getLayer(id).layout[p] = v; this._planifier(false); }
    getLayoutProperty(id, p) { return this.getLayer(id).layout[p]; }
    setPaintProperty(id, p, v) { this.getLayer(id).paint[p] = v; this._planifier(false); }
    setFilter(id, f) { this.getLayer(id).filter = f || null; this._planifier(false); }
    setFeatureState(ref, etat) { const m = (this._etats[ref.source] ||= new Map()); m.set(ref.id, { ...(m.get(ref.id) || {}), ...etat }); this._planifier(false); }
    getCanvas() { return this._canvas; }
    getCenter() { return { lng: this._centre[0], lat: this._centre[1] }; }
    getZoom() { return this._zoom; }
    getBearing() { return 0; }
    getPitch() { return 0; }
    resize() { this._redim(); this._planifier(false); }
    project(ll) { const [lng, lat] = enLngLat(ll), W = TUILE * 2 ** this._zoom; return { x: (lngX(lng) - lngX(this._centre[0])) * W + this._l / 2, y: (latY(lat) - latY(this._centre[1])) * W + this._h / 2 }; }
    unproject(p) { const W = TUILE * 2 ** this._zoom; return { lng: xLng(lngX(this._centre[0]) + (p.x - this._l / 2) / W), lat: yLat(latY(this._centre[1]) + (p.y - this._h / 2) / W) }; }
    jumpTo(o) { this._fixer(o.center ? enLngLat(o.center) : this._centre, o.zoom ?? this._zoom, false); }
    flyTo(o) { this._aller(o.center ? enLngLat(o.center) : this._centre, o.zoom ?? this._zoom, o.duration ?? 600); }
    fitBounds(b, o = {}) {
      if (this._l <= 0 || this._h <= 0) { this._attente = [b, o]; return; }
      const p = o.padding ?? 0, xa = lngX(b[0][0]), xb = lngX(b[1][0]), ya = latY(b[0][1]), yb = latY(b[1][1]);
      const z = Math.log2(Math.min((this._l - 2 * p) / (Math.abs(xb - xa) * TUILE || 1), (this._h - 2 * p) / (Math.abs(yb - ya) * TUILE || 1)));
      this._aller([xLng((xa + xb) / 2), yLat((ya + yb) / 2)], borner(Math.min(z, o.maxZoom ?? 14), 1, 14), o.duration ?? 500);
    }
    addControl(c, coin) { c._ajouter(this, this._coin(coin)); }
    queryRenderedFeatures(p, o = {}) {
      const res = [], W = TUILE * 2 ** this._zoom, ox = this._l / 2 - lngX(this._centre[0]) * W, oy = this._h / 2 - latY(this._centre[1]) * W;
      for (const id of o.layers || this._couches.map(c => c.id)) {
        const c = this.getLayer(id), s = c && this._sources[c.source];
        if (!s || c.layout.visibility === "none") continue;
        const etats = this._etats[c.source];
        for (const f of s.features) {
          if (c.filter && !evaluer(c.filter, f, null, this._zoom)) continue;
          const etat = etats?.get(f._id);
          if (c.type === "circle" && f.pt) {
            const r = evaluer(c.paint["circle-radius"] ?? 5, f, etat, this._zoom) + 2;
            if (Math.hypot(f.pt[0] * W + ox - p.x, f.pt[1] * W + oy - p.y) <= r) res.push({ layer: { id }, properties: f.properties, source: c.source });
          } else if (c.type === "line" && f.lignes) {
            const tol = Math.max(1, evaluer(c.paint["line-width"] ?? 1, f, etat, this._zoom)) / 2 + 3;
            if (p.x < f.x0 * W + ox - tol || p.x > f.x1 * W + ox + tol || p.y < f.y0 * W + oy - tol || p.y > f.y1 * W + oy + tol) continue;
            let touche = false;
            for (const l of f.lignes) {
              for (let i = 0; i < l.length - 2 && !touche; i += 2)
                touche = distSegment(p.x, p.y, l[i] * W + ox, l[i + 1] * W + oy, l[i + 2] * W + ox, l[i + 3] * W + oy) <= tol;
              if (touche) break;
            }
            if (touche) res.push({ layer: { id }, properties: f.properties, source: c.source });
          }
        }
      }
      return res;
    }
    // --- Rendu ---
    _redim() {
      const r = this._cont.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
      this._l = r.width; this._h = r.height; this._dpr = dpr;
      this._canvas.width = Math.max(1, Math.round(r.width * dpr)); this._canvas.height = Math.max(1, Math.round(r.height * dpr));
      if (this._attente && this._l > 0) { const [b, o] = this._attente; this._attente = null; this.fitBounds(b, { ...o, duration: 0 }); }
    }
    _planifier(rapide) {
      this._complet = this._complet || !rapide;
      if (!this._raf) this._raf = requestAnimationFrame(() => { this._raf = 0; const complet = this._complet; this._complet = false; this._dessiner(!complet); });
      if (rapide) { clearTimeout(this._fin); this._fin = setTimeout(() => this._planifier(false), 220); }
    }
    _dessiner(rapide) {
      const ctx = this._ctx, l = this._l, h = this._h, z = this._zoom, W = TUILE * 2 ** z;
      const ox = l / 2 - lngX(this._centre[0]) * W, oy = h / 2 - latY(this._centre[1]) * W;
      const vx0 = -ox / W, vx1 = (l - ox) / W, vy0 = -oy / W, vy1 = (h - oy) / W, marge = 20 / W;
      ctx.setTransform(this._dpr, 0, 0, this._dpr, 0, 0);
      ctx.globalAlpha = 1; ctx.fillStyle = this._fond; ctx.fillRect(0, 0, l, h);
      ctx.lineCap = "round"; ctx.lineJoin = "round";
      const trace = arr => { for (let i = 0; i < arr.length; i += 2) { const X = arr[i] * W + ox, Y = arr[i + 1] * W + oy; i ? ctx.lineTo(X, Y) : ctx.moveTo(X, Y); } };
      for (const c of this._couches) {
        const s = this._sources[c.source];
        if (!s || c.layout.visibility === "none") continue;
        if (rapide && s.features.length > 4000) continue;           // couches denses redessinées à l'arrêt
        let feats = s.features;
        const cle = c.layout["line-sort-key"];
        if (cle) {
          if (c._triVersion !== s.version || c._triSource !== s) { c._tri = [...feats].sort((a, b) => evaluer(cle, a, null, z) - evaluer(cle, b, null, z)); c._triVersion = s.version; c._triSource = s; }
          feats = c._tri;
        }
        const etats = this._etats[c.source], pa = c.paint;
        for (const f of feats) {
          if (f.x1 < vx0 - marge || f.x0 > vx1 + marge || f.y1 < vy0 - marge || f.y0 > vy1 + marge) continue;
          if (c.filter && !evaluer(c.filter, f, null, z)) continue;
          const etat = etats?.get(f._id);
          if (c.type === "fill" && f.polys) {
            ctx.globalAlpha = evaluer(pa["fill-opacity"] ?? 1, f, etat, z);
            ctx.fillStyle = evaluer(pa["fill-color"] ?? "#000", f, etat, z);
            ctx.beginPath(); for (const poly of f.polys) for (const anneau of poly) { trace(anneau); ctx.closePath(); }
            ctx.fill("evenodd");
          } else if (c.type === "line" && (f.lignes || f.polys)) {          // une couche line trace aussi le contour des polygones
            const op = evaluer(pa["line-opacity"] ?? 1, f, etat, z), lw = evaluer(pa["line-width"] ?? 1, f, etat, z);
            if (!(op > 0) || !(lw > 0)) continue;
            ctx.globalAlpha = op; ctx.lineWidth = lw; ctx.strokeStyle = evaluer(pa["line-color"] ?? "#000", f, etat, z);
            ctx.beginPath(); for (const ligne of (f.lignes || f.polys.flat())) trace(ligne); ctx.stroke();
          } else if (c.type === "circle" && f.pt) {
            const r = evaluer(pa["circle-radius"] ?? 5, f, etat, z), sw = evaluer(pa["circle-stroke-width"] ?? 0, f, etat, z);
            if (!(r > 0)) continue;
            ctx.beginPath(); ctx.arc(f.pt[0] * W + ox, f.pt[1] * W + oy, r, 0, 2 * Math.PI);
            ctx.globalAlpha = evaluer(pa["circle-opacity"] ?? 1, f, etat, z);
            ctx.fillStyle = evaluer(pa["circle-color"] ?? "#000", f, etat, z); ctx.fill();
            if (sw > 0) { ctx.globalAlpha = 1; ctx.lineWidth = sw; ctx.strokeStyle = evaluer(pa["circle-stroke-color"] ?? "#fff", f, etat, z); ctx.stroke(); }
          }
        }
      }
      ctx.globalAlpha = 1;
      this._marqueurs.forEach(m => m._placer());
      this._emettre("render", {});
    }
    _fixer(c, z, rapide) {
      this._centre = [c[0], borner(c[1], -LAT_MAX, LAT_MAX)]; this._zoom = borner(z, 1, 14);
      this._emettre("move", {});
      this._planifier(rapide);
    }
    _aller(c, z, duree) {
      cancelAnimationFrame(this._anim);
      if (!duree) { this._fixer(c, z, false); return; }
      const x0 = lngX(this._centre[0]), y0 = latY(this._centre[1]), z0 = this._zoom, x1 = lngX(c[0]), y1 = latY(c[1]), t0 = performance.now();
      const pas = t => {
        const k = Math.min(1, (t - t0) / duree), e = k < 0.5 ? 2 * k * k : 1 - (-2 * k + 2) ** 2 / 2;
        this._fixer([xLng(x0 + (x1 - x0) * e), yLat(y0 + (y1 - y0) * e)], z0 + (z - z0) * e, k < 1);
        if (k < 1) this._anim = requestAnimationFrame(pas);
      };
      this._anim = requestAnimationFrame(pas);
    }
    _zoomer(dz, p) {
      const W0 = TUILE * 2 ** this._zoom, mx = lngX(this._centre[0]) + (p.x - this._l / 2) / W0, my = latY(this._centre[1]) + (p.y - this._h / 2) / W0;
      const z = borner(this._zoom + dz, 1, 14), W = TUILE * 2 ** z;
      this._fixer([xLng(mx - (p.x - this._l / 2) / W), yLat(my - (p.y - this._h / 2) / W)], z, true);
    }
    _emettre(type, e) { (this._ecoute[type] || []).forEach(fn => fn(e)); }
    _souris() {
      const cv = this._canvas;
      let glisse = null;
      const point = ev => { const r = this._cont.getBoundingClientRect(); return { x: ev.clientX - r.left, y: ev.clientY - r.top }; };
      cv.addEventListener("pointerdown", ev => {
        if (ev.button !== 0) return;
        cv.setPointerCapture(ev.pointerId);
        glisse = { p: point(ev), x: lngX(this._centre[0]), y: latY(this._centre[1]), bouge: false };
      });
      cv.addEventListener("pointermove", ev => {
        const p = point(ev);
        if (glisse) {
          const dx = p.x - glisse.p.x, dy = p.y - glisse.p.y;
          if (Math.abs(dx) + Math.abs(dy) > 3) glisse.bouge = true;
          if (glisse.bouge) {
            const W = TUILE * 2 ** this._zoom;
            this._fixer([xLng(glisse.x - dx / W), yLat(glisse.y - dy / W)], this._zoom, true);
            cv.style.cursor = "grabbing";
            return;
          }
        }
        if (!this._survol) this._survol = requestAnimationFrame(() => { this._survol = 0; this._emettre("mousemove", { point: p, lngLat: this.unproject(p) }); });
      });
      cv.addEventListener("pointerup", ev => {
        const g = glisse; glisse = null;
        if (g && !g.bouge) this._emettre("click", { point: point(ev), lngLat: this.unproject(point(ev)) });
        if (g?.bouge) cv.style.cursor = "";
      });
      cv.addEventListener("pointerleave", () => { if (!glisse) this._emettre("mouseout", {}); });
      cv.addEventListener("wheel", ev => { ev.preventDefault(); this._zoomer(-ev.deltaY * (ev.deltaMode ? 0.05 : 0.002), point(ev)); }, { passive: false });
      cv.addEventListener("dblclick", ev => this._zoomer(1, point(ev)));
    }
    _coin(nom) {
      if (!this._coins[nom]) this._cont.appendChild(this._coins[nom] = Object.assign(document.createElement("div"), { className: "mc-coin mc-" + nom }));
      return this._coins[nom];
    }
  }

  class NavigationControl {
    _ajouter(carte, coin) {
      const d = Object.assign(document.createElement("div"), { className: "mc-nav" });
      [["+", 1, "Zoom avant"], ["−", -1, "Zoom arrière"]].forEach(([t, dz, lib]) => {
        const b = Object.assign(document.createElement("button"), { type: "button", textContent: t, title: lib });
        b.setAttribute("aria-label", lib);
        b.addEventListener("click", () => carte._aller(carte._centre, carte._zoom + dz, 250));
        d.appendChild(b);
      });
      coin.appendChild(d);
    }
  }
  class ScaleControl {
    _ajouter(carte, coin) {
      const d = Object.assign(document.createElement("div"), { className: "mc-echelle" });
      coin.appendChild(d);
      const maj = () => {
        const mpp = 40075016.686 * Math.cos(carte._centre[1] * Math.PI / 180) / (TUILE * 2 ** carte._zoom), m = mpp * 100;
        const p = 10 ** Math.floor(Math.log10(m)), dist = m / p >= 5 ? 5 * p : m / p >= 2 ? 2 * p : p;
        d.style.width = Math.round(dist / mpp) + "px";
        d.textContent = dist >= 1000 ? dist / 1000 + " km" : dist + " m";
      };
      carte.on("render", maj);
    }
  }
  class AttributionControl {
    constructor(o = {}) { this._texte = o.customAttribution || ""; }
    _ajouter(carte, coin) { coin.appendChild(Object.assign(document.createElement("div"), { className: "mc-attrib", textContent: this._texte })); }
  }
  class Marker {
    constructor(o = {}) { this._el = o.element || document.createElement("div"); this._ancre = o.anchor || "center"; }
    setLngLat(ll) { this._ll = enLngLat(ll); return this; }
    addTo(carte) {
      this._carte = carte;
      Object.assign(this._el.style, { position: "absolute", left: "0", top: "0", zIndex: 2 });
      carte._cont.appendChild(this._el); carte._marqueurs.push(this); this._placer();
      return this;
    }
    getElement() { return this._el; }
    _placer() {
      const p = this._carte.project(this._ll);
      this._el.style.transform = `translate(${p.x}px, ${p.y}px) translate(${this._ancre === "left" ? "0" : "-50%"}, -50%)`;
    }
  }
  return { Map: Carte, NavigationControl, ScaleControl, AttributionControl, Marker };
})();

function webglDisponible() {
  try { const c = document.createElement("canvas"); return !!(c.getContext("webgl2") || c.getContext("webgl")); }
  catch (e) { return false; }
}
