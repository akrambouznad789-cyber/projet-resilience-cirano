"""
pertes.py
=========
Ce que la calibration cherche à minimiser. Chaque terme est sans dimension et de
l'ordre de 1, pour que les poids de l'objectif (configs/objectifs/*.yaml) aient
un sens : « autant d'importance aux comptages qu'à l'enquête ».

    perte = w_c · déviance_Poisson_relative(comptages) + w_e · NLL_enquête + λ · rappel
"""

from __future__ import annotations

import torch

from jumeau_cirano.modele.affectation import ModeleAffectation
from jumeau_cirano.modele.tenseurs import Enquete

EPS = 1e-6


def deviance_poisson(predit: torch.Tensor, observe: torch.Tensor, poids: torch.Tensor) -> torch.Tensor:
    """Déviance de Poisson rapportée au total observé : 2 Σ w (y ln(y/ŷ) − (y − ŷ)) / Σ w y.

    Adaptée à des comptes : une erreur de 100 camions pèse plus sur une section à 200 qu'à 5 000.
    """
    y = observe
    terme = torch.where(y > 0, y * torch.log(y / predit), torch.zeros_like(y)) - (y - predit)
    return 2 * (poids * terme).sum() / (poids * y).sum()


def probabilites_postes(p: torch.Tensor, e: Enquete) -> torch.Tensor:
    """q_ks : probabilité de franchir le poste s, sachant le passage au poste d'interception (une par ligne)."""
    num = torch.sparse.mm(e.numerateur, p[:, None])[:, 0]
    den = torch.sparse.mm(e.denominateur, p[:, None])[:, 0]
    return num / den.clamp(min=EPS)


def nll_enquete(p: torch.Tensor, e: Enquete, poids: torch.Tensor, erreur: torch.Tensor | float = 0.0) -> torch.Tensor:
    """− log-vraisemblance composite moyenne par camion enquêté (poids : par groupe).

    `erreur` (ε) : probabilité qu'un poste soit noté à tort, franchi ou non (poste mal localisé sur le
    réseau, déclaration erronée) ; P(noté franchi) = ε + (1 − 2ε)·q.
    """
    q = (erreur + (1 - 2 * erreur) * probabilites_postes(p, e)).clamp(EPS, 1 - EPS)
    w = (poids * e.n)[e.groupe]
    ll = e.observe * torch.log(q) + (1 - e.observe) * torch.log(1 - q)
    camions = torch.zeros_like(poids).index_add(0, e.groupe, torch.ones_like(q)).gt(0) * poids * e.n
    return -(w * ll).sum() / camions.sum().clamp(min=EPS)


def rappel(modele: ModeleAffectation, initiaux: dict[str, torch.Tensor], echelle_beta: float = 10.0) -> torch.Tensor:
    """Garde près de leur valeur de départ les paramètres que les données informent peu.

    Σ (ln θ − ln θ₀)² pour les paramètres positifs (μ, g, b), Σ ((β − β₀) / 10)² pour les β (minutes/km).
    """
    t = modele.theta()
    positifs = [*modele.POSITIFS, "fond"]
    r = sum(((torch.log(t[n]) - torch.log(initiaux[n])) ** 2).sum() for n in positifs)
    return r + sum((((t[n] - initiaux[n]) / echelle_beta) ** 2).sum() for n in [*modele.LIBRES, "beta_frontiere"])
