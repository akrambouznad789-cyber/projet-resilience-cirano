"""Modèle d'affectation sur un réseau synthétique dont on connaît la réponse.

    A ══ r0 (autoroute, 60 min) ══ B ══ r2 (autoroute, 30 min) ══ C
      ── r1 (nationale, 55 min, 50 km) ──   ── r3 (nationale, 32 min, 40 km) ──

Paires : A–B (r0 | r1), B–C (r2 | r3), A–C (4 combinaisons). Un poste d'enquête
sur chaque nationale (S1 sur r1, S3 sur r3).
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
import torch

from jumeau_cirano.calibration.entrainement import Selection, entrainer
from jumeau_cirano.calibration.pertes import nll_enquete
from jumeau_cirano.config import Objectif, ParamCalibration
from jumeau_cirano.modele.affectation import Donnees, ModeleAffectation, creux
from jumeau_cirano.modele.tenseurs import Enquete, construire_enquete

CHEMINS = [(0, [0]), (0, [1]), (1, [2]), (1, [3]), (2, [0, 2]), (2, [0, 3]), (2, [1, 2]), (2, [1, 3])]
MINUTES = np.array([60.0, 55.0, 30.0, 32.0])
KM_HORS = np.array([0.0, 50.0, 0.0, 40.0])
POSTES_ROUTE = {1: "S1", 3: "S3"}
FOND = {"10": 100.0, "20": 50.0, "30": 1.0, "40": 1.0, "autre": 1.0}


def donnees(n3: tuple[float, float, float] = (365e3, 365e3, 365e3)) -> Donnees:
    lignes = np.repeat(np.arange(len(CHEMINS)), [len(r) for _, r in CHEMINS])
    colonnes = np.concatenate([r for _, r in CHEMINS])
    cr = creux(lignes, colonnes, np.ones(len(lignes)), (len(CHEMINS), 4))
    par_route = torch.tensor(np.stack([MINUTES, KM_HORS, np.zeros(4)], axis=1))
    return Donnees(chemin_route=cr, composantes=torch.sparse.mm(cr, par_route),
                   paire=torch.tensor([p for p, _ in CHEMINS]), n3=torch.tensor(n3, dtype=torch.float64),
                   ln_ps=torch.zeros(len(CHEMINS), dtype=torch.float64),
                   minutes_acces=torch.zeros(len(CHEMINS), dtype=torch.float64),
                   frontieres=torch.zeros((len(CHEMINS), 1), dtype=torch.float64),
                   minutes=torch.tensor(MINUTES), km_hors_auto=torch.tensor(KM_HORS),
                   km_estime=torch.zeros(4, dtype=torch.float64),
                   route_section=creux(np.arange(4), np.arange(4), np.ones(4), (4, 4)),
                   classe_section=torch.tensor([0, 1, 0, 1]))


def modele(mu: float = 0.1, beta: float = 0.2, croissance: float = 1.0) -> ModeleAffectation:
    init = {"mu": mu, "beta_hors_autoroute": beta, "beta_estime": 0.0, "beta_frontiere": 0.0, "croissance": croissance}
    return ModeleAffectation(init, FOND, ["F1"])


def test_partage_logit_formule_fermee():
    p = modele(mu=0.1, beta=0.2).probabilites(donnees())
    delta = (55 + 0.2 * 50) - 60                                   # coût(r1) − coût(r0)
    assert float(p[0]) == pytest.approx(1 / (1 + math.exp(-0.1 * delta)))
    assert float(p[0] + p[1]) == pytest.approx(1.0) and float(p[4:].sum()) == pytest.approx(1.0)


def test_conservation_de_la_demande():
    d = donnees(n3=(0.0, 0.0, 365e3))                              # seulement A–C : 1 000 camions/jour
    _, flux, sections = modele(croissance=2.0)(d)
    assert float(flux[0] + flux[1]) == pytest.approx(2000.0)        # tout passe par r0 ou r1
    assert float(flux[2] + flux[3]) == pytest.approx(2000.0)
    assert float(sections[0]) == pytest.approx(float(flux[0]) + 100.0)   # + fond autoroute


def test_gradients_exacts():
    d = donnees()
    m = modele()

    def sortie(brut_mu, brut_beta):
        m.brut["mu"].data, m.brut["beta_hors_autoroute"].data = brut_mu.detach(), brut_beta.detach()
        t = m.theta()
        t["mu"], t["beta_hors_autoroute"] = torch.exp(brut_mu), brut_beta
        poids = torch.stack([torch.ones((), dtype=torch.float64), t["beta_hors_autoroute"], t["beta_estime"]])
        from jumeau_cirano.modele.affectation import softmax_par_groupe
        p = softmax_par_groupe(-t["mu"] * (d.composantes @ poids), d.paire, d.n_paires)
        return (p * torch.arange(len(p), dtype=torch.float64)).sum()

    entrees = (torch.tensor(np.log(0.1), dtype=torch.float64, requires_grad=True),
               torch.tensor(0.2, dtype=torch.float64, requires_grad=True))
    assert torch.autograd.gradcheck(sortie, entrees)


def enquete_attendue(vrai: ModeleAffectation, d: Donnees, camions_par_paire: float = 1000.0) -> Enquete:
    """Groupes de camions exactement conformes au modèle vrai : un groupe par chemin, effectif = n · P(p)."""
    p = vrai.probabilites(d).detach().numpy()
    stations = [frozenset(POSTES_ROUTE[r] for r in routes if r in POSTES_ROUTE) for _, routes in CHEMINS]
    groupes = pd.DataFrame({"paire": [pa for pa, _ in CHEMINS], "poste": [None] * len(CHEMINS),
                            "franchis": ["|".join(sorted(s)) for s in stations], "n": camions_par_paire * p})
    par_paire = pd.Series([pa for pa, _ in CHEMINS]).groupby(lambda i: CHEMINS[i][0]).indices
    return construire_enquete(groupes, par_paire, stations)


def test_recuperation_des_parametres():
    """Les paramètres qui ont généré l'enquête sont retrouvés depuis un autre point de départ."""
    d = donnees()
    vrai = modele(mu=0.1, beta=0.2)
    enq = enquete_attendue(vrai, d)
    estime = modele(mu=0.03, beta=0.0)

    class Art:                                                     # le strict nécessaire de modele.tenseurs.Artefact
        donnees, enquete = d, enq
        observees = np.array([], dtype=int)
        y_obs = torch.zeros(0, dtype=torch.float64)

    cal = ParamCalibration(objectif="", pas_adam=0.05, iterations_adam=300, iterations_lbfgs=50, plis=2, bloc_km=50,
                           bootstrap=0, graine=0, regularisation=0.0)
    objectif = Objectif(nom="enquête", poids_comptages=0.0, poids_enquete=1.0)
    sel = Selection(torch.zeros(0, dtype=torch.float64), torch.ones(len(CHEMINS), dtype=torch.float64))
    entrainer(estime, Art, sel, objectif, cal)
    v = estime.valeurs()
    assert v["mu"] == pytest.approx(0.1, rel=0.005)
    assert v["beta_hors_autoroute"] == pytest.approx(0.2, rel=0.005)
    assert float(nll_enquete(estime.probabilites(d), enq, sel.groupes)) <= float(
        nll_enquete(modele(mu=0.03, beta=0.0).probabilites(d), enq, sel.groupes))
