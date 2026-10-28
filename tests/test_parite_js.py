"""Parité JavaScript / Python : node exécute modele.js et scenario.js sur le réseau synthétique de
test_modele.py, avec accès, tailles de chemin et un détour tout-ou-rien ; écart < 1e-6 partout."""

from __future__ import annotations

import dataclasses
import json
import math
import shutil
import subprocess
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import torch

from jumeau_cirano.config import RACINE
from jumeau_cirano.interface.moteur import donnees_moteur, theta_js
from jumeau_cirano.modele.affectation import ModeleAffectation
from jumeau_cirano.reseau.chemins import graphe_des_routes
from jumeau_cirano.scenarios.resilience import Contexte, fermer_avec_flux
from test_modele import FOND, donnees
from test_resilience import BOUTS, LIAISONS, ROUTES

NODE = shutil.which("node")
# A–C n'a qu'un itinéraire (r0 + r2) : fermer r0 force un détour par le graphe (r1 + r2)
CHEMINS = [(0, [0]), (0, [1]), (1, [2]), (1, [3]), (2, [0, 2])]
FERMETURES = [{"1-1"}, {"1-2"}, {"1-1", "1-2"}, {"2-1"}, {"1-1", "2-1"}, set()]


def contexte() -> Contexte:
    d = donnees((365.0, 200.0, 120.0), CHEMINS)
    d = dataclasses.replace(d, ln_ps=torch.tensor([0.0, -0.2, 0.0, -0.1, 0.0], dtype=torch.float64),
                            minutes_acces=torch.tensor([0.0, 0.0, 5.0, 5.0, 3.0], dtype=torch.float64))
    chemins = pd.DataFrame({"paire": [p for p, _ in CHEMINS], "entree": [BOUTS[p][0] for p, _ in CHEMINS],
                            "sortie": [BOUTS[p][1] for p, _ in CHEMINS], "minutes_acces": d.minutes_acces.numpy(),
                            "km_acces": [0.0, 0.0, 4.0, 4.0, 2.5]})
    art = SimpleNamespace(donnees=d, routes=ROUTES, chemins=chemins, observees=np.array([0, 1, 3]),
                          y_obs=torch.tensor([300.0, 60.0, 9.0], dtype=torch.float64))
    init = {"mu": 0.08, "beta_hors_autoroute": 0.3, "beta_estime": 0.0, "beta_frontiere": 0.0, "croissance": 1.4}
    modele = ModeleAffectation(init, FOND, ["F1"])
    return Contexte.creer(art, modele, graphe_des_routes(ROUTES, LIAISONS), set(),
                          pd.DataFrame({"n3": [365.0, 200.0, 120.0], "t3": [3650.0, 2000.0, 1200.0]}))


def proche(a, b, tol: float = 1e-6) -> bool:
    a = np.where(np.asarray(a, dtype=object) == None, np.inf, a).astype(float)  # noqa: E711 (null JSON = ∞)
    return bool(np.allclose(a, np.asarray(b, dtype=float), rtol=tol, atol=tol))


@pytest.mark.skipif(NODE is None, reason="node absent")
def test_modele_et_scenarios_identiques_en_javascript(tmp_path):
    ctx = contexte()
    ids = ROUTES["ID_ROUTE"].tolist()
    fixture = {"donnees": donnees_moteur(ctx, LIAISONS, reels="f8"),
               "theta": theta_js(ctx.modele.valeurs(), ctx.modele.frontieres),
               "fermetures": [sorted(ids.index(r) for r in f) for f in FERMETURES]}
    (tmp_path / "fixture.json").write_text(json.dumps(fixture), encoding="utf-8")
    res = subprocess.run([NODE, str(RACINE / "tests" / "parite.js"), str(tmp_path / "fixture.json"),
                          str(RACINE / "scripts" / "gabarits" / "js")], capture_output=True, text=True, check=True)
    js = json.loads(res.stdout)

    d = ctx.art.donnees
    with torch.no_grad():
        p, flux, sections = ctx.modele(d)
    assert proche(js["p"], p.numpy()) and proche(js["flux"], flux.numpy()) and proche(js["sections"], sections.numpy())
    assert proche(js["cout_compose"], ctx.c_ref)
    ys, yo = sections.numpy()[[0, 1, 3]], np.array([300.0, 60.0, 9.0])
    r2 = 1 - ((np.log1p(ys) - np.log1p(yo)) ** 2).sum() / ((np.log1p(yo) - np.log1p(yo).mean()) ** 2).sum()
    assert js["metriques"]["r2_log"] == pytest.approx(r2, abs=1e-9)

    for f, r_js in zip(FERMETURES, js["fermetures"], strict=True):
        impacts, flux_py = fermer_avec_flux(ctx, f, cout_horaire=90.0, valeur_tonne=1.0)
        assert proche(r_js["flux"], flux_py), f
        for k, v in impacts.items():
            assert math.isclose(r_js[k], v, rel_tol=1e-6, abs_tol=1e-6), (f, k, r_js[k], v)
    # Le détour a bien eu lieu : A–C passe par r1 + r2 quand r0 ferme
    detour = js["fermetures"][0]
    assert detour["trajets_non_desservis"] == 0 and detour["flux"][1] > ctx.flux_ref[1]
