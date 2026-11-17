"""Calage plus rapide : mêmes produits, même perte, mêmes gradients ; sous-artefact de l'échantillon cohérent.

La formulation de référence (`perte_coo`) reprend mot pour mot les produits creux du code d'avant
l'accélération (torch.sparse.mm sur des matrices COO). Tolérance de la phase 3 : écart relatif ≤ 1e-10.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import torch

from jumeau_cirano.api.synthetique import PARAMETRES, ecrire
from jumeau_cirano.calibration.entrainement import Selection, perte
from jumeau_cirano.calibration.pertes import EPS, rappel
from jumeau_cirano.config import Objectif
from jumeau_cirano.modele.affectation import ModeleAffectation, OperateurCreux, creux
from jumeau_cirano.modele.echantillon import echantillonner
from jumeau_cirano.modele.tenseurs import Artefact, charger_artefact

TOLERANCE = 1e-10
EQUILIBRE = Objectif(nom="équilibre", poids_comptages=1.0, poids_enquete=1.0)


@pytest.fixture(scope="module")
def artefact(tmp_path_factory) -> Path:
    return ecrire(tmp_path_factory.mktemp("paquet")) / "artefact"


def perte_coo(m: ModeleAffectation, art: Artefact, sel: Selection, lam: float, initiaux: dict) -> torch.Tensor:
    """Perte « équilibre » avec les produits COO d'avant l'accélération (référence)."""
    d, e, t = art.donnees, art.enquete, m.theta()
    p = m.probabilites(d)
    volume = t["croissance"] * d.n3[d.paire] * p / 365.0
    flux = torch.sparse.mm(d.chemin_route.t(), volume[:, None])[:, 0]
    y = torch.sparse.mm(d.route_section.t(), flux[:, None])[:, 0] + t["fond"][d.classe_section]
    yo, ys = art.y_obs, y[art.observees]
    terme = torch.where(yo > 0, yo * torch.log(yo / ys), torch.zeros_like(yo)) - (yo - ys)
    comptages = 2 * (sel.sections * terme).sum() / (sel.sections * yo).sum()
    num = torch.sparse.mm(e.numerateur, p[:, None])[:, 0]
    den = torch.sparse.mm(e.denominateur, p[:, None])[:, 0]
    eps = t["erreur_postes"]
    q = (eps + (1 - 2 * eps) * num / den.clamp(min=EPS)).clamp(EPS, 1 - EPS)
    w = (sel.groupes * e.n)[e.groupe]
    ll = e.observe * torch.log(q) + (1 - e.observe) * torch.log(1 - q)
    camions = torch.zeros_like(sel.groupes).index_add(0, e.groupe, torch.ones_like(q)).gt(0) * sel.groupes * e.n
    enquete = -(w * ll).sum() / camions.sum().clamp(min=EPS)
    return lam * rappel(m, initiaux) + comptages + enquete


def ecart_relatif(a: torch.Tensor, b: torch.Tensor) -> float:
    return float(((a - b).abs() / b.abs().clamp(min=1e-300)).max()) if a.numel() else 0.0


def test_operateur_creux_meme_produit_et_meme_gradient():
    rng = np.random.default_rng(0)
    lignes, colonnes = rng.integers(0, 300, 2000), rng.integers(0, 200, 2000)
    a = creux(lignes, colonnes, rng.choice([0.0, 1.0, 2.5], 2000), (300, 200))     # zéros explicites, doublons
    op = OperateurCreux(a)
    assert op.a.values().numel() < a._nnz() and op.forme == (300, 200)              # zéros retirés
    x = torch.tensor(rng.normal(size=200), dtype=torch.float64, requires_grad=True)
    g = torch.tensor(rng.normal(size=300), dtype=torch.float64)
    reference = torch.sparse.mm(a, x[:, None])[:, 0]
    assert torch.allclose(op(x), reference, rtol=TOLERANCE, atol=1e-13)
    (attendu,) = torch.autograd.grad(reference, x, g)
    (obtenu,) = torch.autograd.grad(op(x), x, g)
    assert torch.allclose(obtenu, attendu, rtol=TOLERANCE, atol=0)
    assert torch.autograd.gradcheck(op, (x,))


@pytest.mark.parametrize("decalage", [0.0, 0.3])
def test_perte_et_gradient_identiques_a_la_formulation_coo(artefact, decalage):
    art = charger_artefact(artefact)
    modeles = []
    for _ in range(2):
        m = ModeleAffectation.depuis_valeurs(PARAMETRES, art.frontieres)
        with torch.no_grad():                                  # un point quelconque, pas seulement θ calé
            for prm in m.parameters():
                prm.add_(decalage)
        modeles.append(m)
    initiaux = {k: v.detach().clone() * 1.1 for k, v in modeles[0].theta().items()}
    sel = Selection(torch.tensor([1.0, 2.0, 0.0, 1.0], dtype=torch.float64),
                    torch.ones(art.enquete.n.shape[0], dtype=torch.float64))
    nouvelle, _ = perte(modeles[0], art, sel, EQUILIBRE, 0.01, initiaux)
    ancienne = perte_coo(modeles[1], art, sel, 0.01, initiaux)
    assert ecart_relatif(nouvelle.detach(), ancienne.detach()) <= TOLERANCE
    nouvelle.backward()
    ancienne.backward()
    for (nom, a), (_, b) in zip(modeles[0].named_parameters(), modeles[1].named_parameters(), strict=True):
        assert torch.allclose(a.grad, b.grad, rtol=TOLERANCE, atol=1e-14), nom


def test_flux_et_sections_identiques(artefact):
    art = charger_artefact(artefact)
    d = art.donnees
    m = ModeleAffectation.depuis_valeurs(PARAMETRES, art.frontieres)
    p, flux, y = m(d)
    volume = m.theta()["croissance"] * d.n3[d.paire] * p / 365.0
    flux_coo = torch.sparse.mm(d.chemin_route.t(), volume[:, None])[:, 0]
    assert ecart_relatif(flux, flux_coo) <= TOLERANCE
    y_coo = torch.sparse.mm(d.route_section.t(), flux_coo[:, None])[:, 0] + m.theta()["fond"][d.classe_section]
    assert ecart_relatif(y, y_coo) <= TOLERANCE


def test_echantillon_garde_les_paires_entieres(artefact, tmp_path):
    """Routes 1-1 et 1-2 : seule la paire A–B (0) a tous ses itinéraires dedans ; A–C passe aussi par 2-x."""
    meta = echantillonner(artefact, tmp_path / "ech", {"1-1", "1-2"})
    assert (meta["routes"], meta["paires"], meta["chemins"], meta["groupes"]) == (2, 1, 2, 1)
    assert meta["postes_localises"] == 1 and meta["sections_mesurees"] == 2
    complet, ech = charger_artefact(artefact), charger_artefact(tmp_path / "ech")
    m = ModeleAffectation.depuis_valeurs(PARAMETRES, complet.frontieres)
    p_complet = m.probabilites(complet.donnees)
    p_ech = ModeleAffectation.depuis_valeurs(PARAMETRES, ech.frontieres).probabilites(ech.donnees)
    assert torch.allclose(p_ech, p_complet[:2])                       # même choix de route pour la paire gardée
    assert pd.read_parquet(tmp_path / "ech" / "chemins.parquet")["paire"].tolist() == [0, 0]
    assert ech.enquete.n.tolist() == [10.0] and len(ech.observees) == 2

    vide = echantillonner(artefact, tmp_path / "vide", {"1-1", "2-1"})  # chaque paire a une nationale
    assert vide["paires"] == 0 and vide["chemins"] == 0
