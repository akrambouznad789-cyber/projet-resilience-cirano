"""Scénarios de fermeture sur le réseau synthétique de test_modele.py (réponses calculables à la main)."""

from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import torch

from jumeau_cirano.reseau.chemins import graphe_des_routes
from jumeau_cirano.scenarios.resilience import Contexte, fermer
from test_modele import CHEMINS, MINUTES, donnees, modele

ROUTES = pd.DataFrame({"ID_ROUTE": ["1-1", "1-2", "2-1", "2-2"], "ID_ARC": [1, 1, 2, 2],
                       "minutes": MINUTES, "km": [100.0, 50.0, 50.0, 40.0]})
LIAISONS = pd.DataFrame({"ID_ARC": [1, 2], "ID_A": ["A", "B"], "ID_B": ["B", "C"]})
BOUTS = {0: ("A", "B"), 1: ("B", "C"), 2: ("A", "C")}


def contexte(n3: tuple[float, float, float]) -> Contexte:
    d = donnees(n3)
    chemins = pd.DataFrame({"paire": [p for p, _ in CHEMINS], "entree": [BOUTS[p][0] for p, _ in CHEMINS],
                            "sortie": [BOUTS[p][1] for p, _ in CHEMINS], "minutes_acces": 0.0, "km_acces": 0.0})
    art = SimpleNamespace(donnees=d, routes=ROUTES, chemins=chemins)
    paires = pd.DataFrame({"n3": n3, "t3": [10 * x for x in n3]})
    return Contexte.creer(art, modele(mu=0.1, beta=0.2, croissance=1.0), graphe_des_routes(ROUTES, LIAISONS),
                          set(), paires)


def test_fermer_l_autoroute_reporte_sur_la_nationale():
    ctx = contexte((365.0, 0.0, 0.0))                          # 1 trajet/jour A–B
    p0 = float(ctx.p_ref[0])                                   # part de l'autoroute avant fermeture
    r = fermer(ctx, {"1-1"}, cout_horaire=90.0, valeur_tonne=1.0)
    attendu_h = 365 * (55 - (p0 * 60 + (1 - p0) * 55)) / 60   # tout passe à 55 min
    assert r["delta_camions_heures"] == pytest.approx(attendu_h)
    # La nationale est plus rapide (55 min) mais plus coûteuse (55 + 0,2 × 50 km = 65) : le temps moyen
    # BAISSE, le coût composite monte. C'est lui qui classe les liaisons.
    assert attendu_h < 0
    c_ref = -math.log(math.exp(-0.1 * 60) + math.exp(-0.1 * 65)) / 0.1
    assert r["delta_heures_equivalentes"] == pytest.approx(365 * (65 - c_ref) / 60)
    assert r["cout_perturbation"] == pytest.approx(90 * r["delta_heures_equivalentes"])
    assert r["camions_sur_liaison"] == pytest.approx(365 * p0)
    assert r["tonnes_sur_liaison"] == pytest.approx(3650 * p0) and r["trajets_non_desservis"] == 0


def test_coupure_totale_demande_non_desservie():
    ctx = contexte((365.0, 365.0, 365.0))
    r = fermer(ctx, {"1-1", "1-2"}, cout_horaire=90.0, valeur_tonne=1.0)
    assert r["trajets_non_desservis"] == pytest.approx(2 * 365)   # A–B et A–C : A n'est plus relié
    assert r["delta_camions_heures"] == pytest.approx(0.0)        # B–C n'est pas touchée
    assert r["delta_heures_equivalentes"] == pytest.approx(0.0)


def test_un_seul_itineraire_restant_puis_coupure():
    """A–C : fermer r0 et r2 ne laisse que r1 + r3 (coût 65 + 40) ; fermer aussi r1 isole A."""
    ctx = contexte((0.0, 0.0, 365.0))
    r = fermer(ctx, {"1-1", "2-1", "1-2"}, cout_horaire=90.0, valeur_tonne=1.0)
    assert r["trajets_non_desservis"] == pytest.approx(365.0)     # A isolé
    r = fermer(ctx, {"1-1", "2-1"}, cout_horaire=90.0, valeur_tonne=1.0)
    c_ref = -math.log(sum(math.exp(-0.1 * c) for c in (90, 60 + 40, 65 + 30, 65 + 40))) / 0.1
    assert r["delta_heures_equivalentes"] == pytest.approx(365 * (65 + 40 - c_ref) / 60)


def test_fermer_rien_ne_change_rien():
    ctx = contexte((365.0, 365.0, 365.0))
    r = fermer(ctx, set(), cout_horaire=90.0, valeur_tonne=1.0)
    assert np.isclose(r["delta_camions_heures"], 0) and np.isclose(r["delta_camions_km"], 0)
    assert torch.allclose(ctx.p_ref, ctx.modele.probabilites(ctx.art.donnees).detach())
