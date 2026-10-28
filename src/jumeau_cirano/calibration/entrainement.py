"""
entrainement.py
===============
Calibration par descente de gradient : Adam (exploration robuste), puis L-BFGS
(convergence fine ; 10 paramètres seulement). Les observations portent des
poids : 0 = hors entraînement (validation croisée), entiers = tirage bootstrap.
"""

from __future__ import annotations

from dataclasses import dataclass

import torch

from jumeau_cirano.calibration.pertes import deviance_poisson, nll_enquete, rappel
from jumeau_cirano.config import Objectif, ParamCalibration
from jumeau_cirano.modele.affectation import ModeleAffectation
from jumeau_cirano.modele.tenseurs import Artefact


@dataclass
class Selection:
    """Poids des observations : sections mesurées (dans l'ordre de art.observees) et groupes de camions."""

    sections: torch.Tensor
    groupes: torch.Tensor

    @classmethod
    def tout(cls, art: Artefact) -> Selection:
        return cls(torch.ones(len(art.observees), dtype=torch.float64),
                   torch.ones(art.enquete.n.shape[0], dtype=torch.float64))


def perte(modele: ModeleAffectation, art: Artefact, sel: Selection, objectif: Objectif, lam: float,
          initiaux: dict[str, torch.Tensor]) -> tuple[torch.Tensor, dict[str, float]]:
    """Perte totale et ses termes (pour le suivi)."""
    p, _, y = modele(art.donnees)
    total = lam * rappel(modele, initiaux)
    parts = {}
    if objectif.poids_comptages > 0:
        c = deviance_poisson(y[art.observees], art.y_obs, sel.sections)
        total = total + objectif.poids_comptages * c
        parts["comptages"] = float(c.detach())
    if objectif.poids_enquete > 0:
        e = nll_enquete(p, art.enquete, sel.groupes)
        total = total + objectif.poids_enquete * e
        parts["enquete"] = float(e.detach())
    return total, parts


def entrainer(modele: ModeleAffectation, art: Artefact, sel: Selection, objectif: Objectif, cal: ParamCalibration,
              iterations_adam: int | None = None, iterations_lbfgs: int | None = None) -> list[dict]:
    """Cale `modele` en place ; renvoie l'historique (perte et termes) pour la courbe d'apprentissage."""
    initiaux = {k: v.detach().clone() for k, v in modele.theta().items()}
    params = [p for p in modele.parameters() if p.requires_grad]
    historique = []
    opt = torch.optim.Adam(params, lr=cal.pas_adam)
    for _ in range(cal.iterations_adam if iterations_adam is None else iterations_adam):
        opt.zero_grad()
        total, parts = perte(modele, art, sel, objectif, cal.regularisation, initiaux)
        total.backward()
        opt.step()
        historique.append({"perte": float(total.detach()), **parts})
    n_lbfgs = cal.iterations_lbfgs if iterations_lbfgs is None else iterations_lbfgs
    if n_lbfgs:
        lbfgs = torch.optim.LBFGS(params, max_iter=n_lbfgs, line_search_fn="strong_wolfe")

        def fermeture() -> torch.Tensor:
            lbfgs.zero_grad()
            total, parts = perte(modele, art, sel, objectif, cal.regularisation, initiaux)
            total.backward()
            historique.append({"perte": float(total.detach()), **parts})
            return total

        lbfgs.step(fermeture)
    return historique
