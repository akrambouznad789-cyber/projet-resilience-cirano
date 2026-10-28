"""
validation.py
=============
Mesurer honnêtement : plis spatiaux pour les sections (blocs de 50 km entiers
mis de côté, comme pour le RandomForest), plis par poste pour les camions
(un poste entier mis de côté), métriques, et deux bases de comparaison.

Bases de comparaison (tout-ou-rien ; seuls g et le fond local sont calés) :
    B0  le routage du Volet 2 : une route par liaison (la meilleure), chemin le plus court en km
    B1  le réseau à plusieurs routes, chemin le plus rapide
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import shapely
import torch
from sklearn.model_selection import GroupKFold

from jumeau_cirano.calibration.entrainement import Selection
from jumeau_cirano.calibration.pertes import nll_enquete
from jumeau_cirano.modele.affectation import ModeleAffectation, softmax_par_groupe
from jumeau_cirano.modele.tenseurs import Artefact


def plis_sections(art: Artefact, bloc_km: float, k: int) -> np.ndarray:
    """Pli de chaque section mesurée : blocs carrés de `bloc_km` km entiers (GroupKFold)."""
    centres = shapely.centroid(shapely.from_wkt(art.sections["geometry"].to_numpy()[art.observees]))
    x, y = shapely.get_x(centres), shapely.get_y(centres)
    blocs = (np.floor(x / (bloc_km * 1000)).astype(int) * 100000 + np.floor(y / (bloc_km * 1000)).astype(int))
    plis = np.empty(len(blocs), dtype=int)
    for i, (_, test) in enumerate(GroupKFold(k).split(blocs, groups=blocs)):
        plis[test] = i
    return plis


def plis_groupes(art: Artefact, k: int) -> np.ndarray:
    """Pli de chaque groupe de camions : un poste d'interception entier par pli (sinon, la paire de lieux)."""
    cle = np.array([str(p) if isinstance(p, str) else f"paire{q}" for p, q in zip(art.enquete.poste, art.enquete.paire,
                                                                                    strict=True)])
    plis = np.empty(len(cle), dtype=int)
    for i, (_, test) in enumerate(GroupKFold(k).split(cle, groups=cle)):
        plis[test] = i
    return plis


def selection_entrainement(plis_s: np.ndarray, plis_g: np.ndarray, pli: int) -> Selection:
    return Selection(torch.as_tensor((plis_s != pli).astype(float)), torch.as_tensor((plis_g != pli).astype(float)))


def chemin_principal(p: torch.Tensor, paire: torch.Tensor, n_paires: int) -> np.ndarray:
    """Indice du chemin le plus probable de chaque paire (−1 si la paire n'a aucun chemin)."""
    df = pd.DataFrame({"paire": paire.numpy(), "p": p.detach().numpy()}).reset_index()
    meilleur = df.sort_values("p", ascending=False).drop_duplicates("paire").set_index("paire")["index"]
    return meilleur.reindex(range(n_paires), fill_value=-1).to_numpy()


@torch.no_grad()
def metriques(modele: ModeleAffectation, art: Artefact, sections: np.ndarray, groupes: np.ndarray) -> dict[str, float]:
    """Métriques sur les sections et groupes sélectionnés (masques booléens)."""
    p, _, y = modele(art.donnees)
    ys, yo = y[art.observees][sections].numpy(), art.y_obs[sections].numpy()
    ly, lo = np.log1p(ys), np.log1p(yo)
    ecart = np.abs(ys - yo) / np.maximum(yo, 1)
    r = {"r2_log": float(1 - ((ly - lo) ** 2).sum() / ((lo - lo.mean()) ** 2).sum()),
         "erreur_mediane": float(np.median(ecart)), "part_30": float((ecart <= 0.3).mean()),
         "nll_enquete": float(nll_enquete(p, art.enquete, torch.as_tensor(groupes.astype(float))))}
    principal = chemin_principal(p, art.donnees.paire, art.donnees.n_paires)
    n = art.enquete.n.numpy()
    g = pd.DataFrame({"poste": art.enquete.poste, "paire": art.enquete.paire, "n": n})[groupes]
    au_qc = g[g["poste"].map(lambda x: isinstance(x, str))]
    passe = [principal[q] >= 0 and p_ in art.stations_chemin[principal[q]]
             for p_, q in zip(au_qc["poste"], au_qc["paire"], strict=True)]
    r["poste_retrouve"] = float((au_qc["n"] * np.array(passe)).sum() / au_qc["n"].sum())
    return r


class ModeleFixe(ModeleAffectation):
    """Base de comparaison : probabilités tout-ou-rien figées ; seuls g et le fond local se calent."""

    def __init__(self, p_fixe: torch.Tensor, initiaux: dict[str, float], fond: dict[str, float],
                 frontieres: list[str]) -> None:
        super().__init__(initiaux, fond, frontieres)
        self.p_fixe = p_fixe
        for n in ("mu", "beta_hors_autoroute", "beta_estime"):
            self.brut[n].requires_grad_(False)
        self.beta_frontiere.requires_grad_(False)

    def probabilites(self, d, masque=None) -> torch.Tensor:
        return self.p_fixe


def tout_ou_rien(art: Artefact, base: str) -> torch.Tensor:
    """P one-hot : B0 = plus court en km sur la meilleure route de chaque liaison ; B1 = plus rapide, toutes routes."""
    d = art.donnees
    if base == "B1":
        cout = d.composantes[:, 0] + d.minutes_acces
    else:
        km_route = torch.as_tensor(art.routes["km"].to_numpy(dtype=float), dtype=torch.float64)
        cout = torch.sparse.mm(d.chemin_route, km_route[:, None])[:, 0] + torch.as_tensor(
            art.chemins["km_acces"].to_numpy(dtype=float), dtype=torch.float64)
        alternative = torch.as_tensor((art.routes["rang"].to_numpy() > 1).astype(float), dtype=torch.float64)
        sur_alternative = torch.sparse.mm(d.chemin_route, alternative[:, None])[:, 0] > 0
        cout = torch.where(sur_alternative, cout + 1e6, cout)     # seulement si la paire n'a pas d'autre choix
    return softmax_par_groupe(-1e3 * cout, d.paire, d.n_paires).detach()
