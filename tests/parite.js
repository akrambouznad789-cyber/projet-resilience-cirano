// Exécuté par tests/test_parite_js.py : node tests/parite.js <fixture.json> <dossier des modules>
"use strict";
const fs = require("fs");
const path = require("path");
const MJ = require(path.join(process.argv[3], "modele.js"));
const SJ = require(path.join(process.argv[3], "scenario.js"));

const fx = JSON.parse(fs.readFileSync(process.argv[2], "utf-8"));
const M = MJ.preparer(fx.donnees);
const a = MJ.affecter(M, fx.theta);
const ref = SJ.reference(M, fx.theta);
const liste = x => Array.from(x, v => (isFinite(v) ? v : null));
const sortie = {
  p: liste(a.p), flux: liste(a.flux), sections: liste(a.sections), cout_compose: liste(ref.cRef),
  metriques: a.metriques,
  fermetures: fx.fermetures.map(f => { const r = SJ.fermer(M, ref, new Set(f), 90, 1); r.flux = liste(r.flux); return r; }),
};
process.stdout.write(JSON.stringify(sortie));
