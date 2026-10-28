"""Modèle d'affectation sur un réseau synthétique dont on connaît la réponse.

    A ══ r0 (autoroute, 60 min) ══ B ══ r2 (autoroute, 30 min) ══ C
      ── r1 (nationale, 55 min, 50 km) ──   ── r3 (nationale, 32 min, 40 km) ──

Paires : A–B (r0 | r1), B–C (r2 | r3), A–C (4 combinaisons). Un poste d'enquête
sur chaque nationale (S1 sur r1, S3 sur r3).
"""

from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest
import torch

from jumeau_cirano.calibration.entrainement import Selection, entrainer
from jumeau_cirano.calibration.pertes import nll_enquete, probabilites_postes
from jumeau_cirano.config import Objectif, ParamCalibration
from jumeau_cirano.modele.affectation import Donnees, ModeleAffectation, creux
from jumeau_cirano.modele.tenseurs import Enquete, construire_enquete

CHEMINS = [(0, [0]), (0, [1]), (1, [2]), (1, [3]), (2, [0, 2]), (2, [0, 3]), (2, [1, 2]), (2, [1, 3])]
MINUTES = np.array([60.0, 55.0, 30.0, 32.0])
KM_HORS = np.array([0.0, 50.0, 0.0, 40.0])
POSTES_ROUTE = {1: "S1", 3: "S3"}
FOND = {"10": 100.0, "20": 50.0, "30": 1.0, "40": 1.0, "autre": 1.0}


def donnees(n3: tuple[float, float, float] = (365e3, 365e3, 365e3),
            chemins: list[tuple[int, list[int]]] = CHEMINS) -> Donnees:
    lignes = np.repeat(np.arange(len(chemins)), [len(r) for _, r in chemins])
    colonnes = np.concatenate([r for _, r in chemins])
    cr = creux(lignes, colonnes, np.ones(len(lignes)), (len(chemins), 4))
    par_route = torch.tensor(np.stack([MINUTES, KM_HORS, np.zeros(4)], axis=1))
    return Donnees(chemin_route=cr, composantes=torch.sparse.mm(cr, par_route),
                   paire=torch.tensor([p for p, _ in chemins]), n3=torch.tensor(n3, dtype=torch.float64),
                   ln_ps=torch.zeros(len(chemins), dtype=torch.float64),
                   minutes_acces=torch.zeros(len(chemins), dtype=torch.float64),
                   frontieres=torch.zeros((len(chemins), 1), dtype=torch.float64),
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
    """Les paramètres qui ont généré l'enquête sont retrouvés depuis un autre point de départ.

    ε est figé à sa vraie valeur (0) : avec deux postes, l'enquête ne donne que deux proportions
    indépendantes, trop peu pour μ, β et ε à la fois (ε a son propre test).
    """
    d = donnees()
    vrai = modele(mu=0.1, beta=0.2)
    enq = enquete_attendue(vrai, d)
    estime = modele(mu=0.03, beta=0.0)
    estime.brut_erreur.data.fill_(-40.0)
    estime.brut_erreur.requires_grad_(False)

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


def test_depuis_valeurs_reproduit_le_modele():
    """Paramètres exportés (JSON, API) → même modèle, mêmes probabilités."""
    m = modele(mu=0.07, beta=0.3, croissance=1.4)
    copie = ModeleAffectation.depuis_valeurs(m.valeurs(), m.frontieres)
    d = donnees()
    assert torch.allclose(copie.probabilites(d), m.probabilites(d))
    assert copie.valeurs() == pytest.approx(m.valeurs())


def test_resume_validation_exportable_en_json():
    from jumeau_cirano.calibration.commande import resume_validation

    cv = pd.DataFrame({"modele": ["M", "M", "B0", "B0"], "pli": [0, 1, 0, 1], "r2_log": [0.5, 0.7, 0.4, 0.6]})
    r = json.loads(json.dumps(resume_validation(cv)))
    assert r["M"]["r2_log"]["moyenne"] == pytest.approx(0.6) and set(r) == {"M", "B0"}


def test_erreur_nulle_reproduit_la_vraisemblance_sans_erreur():
    d, vrai = donnees(), modele()
    enq, p = enquete_attendue(vrai, d), vrai.probabilites(d)
    poids = torch.ones(len(CHEMINS), dtype=torch.float64)
    q = probabilites_postes(p, enq).clamp(1e-6, 1 - 1e-6)
    attendu = -((poids * enq.n)[enq.groupe] * (enq.observe * torch.log(q) + (1 - enq.observe) * torch.log(1 - q))).sum()
    assert float(nll_enquete(p, enq, poids, 0.0)) == pytest.approx(float(attendu / enq.n.sum()))


def test_recuperation_du_taux_d_erreur():
    """Une enquête dont 15 % des postes sont notés à tort : μ et β connus, ε est retrouvé."""
    d, vrai = donnees(), modele(mu=0.1, beta=0.2)
    p = vrai.probabilites(d).detach().numpy()
    stations = [frozenset(POSTES_ROUTE[r] for r in routes if r in POSTES_ROUTE) for _, routes in CHEMINS]
    lignes = []
    for k, (pa, _) in enumerate(CHEMINS):                          # chaque chemin × chaque combinaison d'erreurs
        for s1 in (False, True):
            for s3 in (False, True):
                note = {s for s, err in (("S1", s1), ("S3", s3)) if (s in stations[k]) != err}
                lignes.append({"paire": pa, "poste": None, "franchis": "|".join(sorted(note)),
                               "n": 1000 * p[k] * (0.15 if s1 else 0.85) * (0.15 if s3 else 0.85)})
    par_paire = pd.Series([pa for pa, _ in CHEMINS]).groupby(lambda i: CHEMINS[i][0]).indices
    enq = construire_enquete(pd.DataFrame(lignes), par_paire, stations)

    estime = modele(mu=0.1, beta=0.2)
    for n in ("mu", "beta_hors_autoroute"):
        estime.brut[n].requires_grad_(False)
    estime.beta_frontiere.requires_grad_(False)

    class Art:
        donnees, enquete = d, enq
        observees = np.array([], dtype=int)
        y_obs = torch.zeros(0, dtype=torch.float64)

    cal = ParamCalibration(objectif="", pas_adam=0.05, iterations_adam=300, iterations_lbfgs=50, plis=2, bloc_km=50,
                           bootstrap=0, graine=0, regularisation=0.0)
    entrainer(estime, Art, Selection(torch.zeros(0, dtype=torch.float64), torch.ones(len(lignes), dtype=torch.float64)),
              Objectif(nom="enquête", poids_comptages=0.0, poids_enquete=1.0), cal)
    assert estime.valeurs()["erreur_postes"] == pytest.approx(0.15, abs=0.002)
