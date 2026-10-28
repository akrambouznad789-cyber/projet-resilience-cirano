"""
affectation.py
==============
Modèle d'affectation des camions : logit à taille de chemin, entièrement
différentiable (PyTorch), donc calable par descente de gradient.

Paramètres :
    μ                 dispersion (1/minute) ; μ grand → tout-ou-rien sur le chemin le moins coûteux   (> 0)
    β_hors_autoroute  minutes équivalentes par km hors autoroute ; > 0 = préférence pour l'autoroute   (libre)
    β_estime          minutes par km de raccord ou d'accès estimé (routes absentes du RTSS)          (libre)
    β_frontiere[f]    minutes par passage au point frontière f (un par frontière : le CHOIX de la
                      frontière est identifiable, pas le nombre de passages, constant dans une paire) (libre)
    g                 croissance : camions 2025 par trajet ENR 2006-2007                              (> 0)
    b[c]              camions par jour absents de l'ENR (trafic local), par classe RTSS              (> 0)

Les paramètres positifs sont calés en échelle log (θ = exp(brut)) : gradient bien
conditionné et pas multiplicatifs. Les β sont libres : le signe est à apprendre.

Équations :
    coût_p   = Σ_r∈p (minutes_r + β_ha·km_hors_auto_r + β_e·km_estime_r) + minutes_accès_p + Σ_f β_f·passages_pf
    P(p|i)   = softmax sur les chemins de la paire i de ( −μ·coût_p + ln PS_p )
    flux_r   = g · Σ_p n3_i(p) · P(p|i) · δ_rp / 365                (camions par jour, 2025)
    ŷ_s      = Σ_r flux_r · δ_rs + b[classe_s]                     (camions par jour sur la section s)

Le même calcul existe en JavaScript (scripts/gabarits/js/modele.js) pour la page
locale ; un test vérifie que les deux donnent les mêmes flux.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import torch
from torch import nn

CLASSES = ["10", "20", "30", "40", "autre"]


def creux(lignes: np.ndarray, colonnes: np.ndarray, valeurs: np.ndarray, forme: tuple[int, int]) -> torch.Tensor:
    """Matrice creuse COO (float64)."""
    i = torch.as_tensor(np.vstack([lignes, colonnes]), dtype=torch.long)
    return torch.sparse_coo_tensor(i, torch.as_tensor(valeurs, dtype=torch.float64), forme).coalesce()


@dataclass
class Donnees:
    """Tout ce dont le modèle a besoin, sous forme de tenseurs (float64 : calibration stable)."""

    chemin_route: torch.Tensor          # P × R, creuse (1 si le chemin emprunte la route)
    composantes: torch.Tensor           # P × 3 : minutes, km hors autoroute, km estimés du chemin (précalculés)
    paire: torch.Tensor                 # P, indice de la paire de lieux de chaque chemin
    n3: torch.Tensor                    # nombre de paires : trajets ENR par an
    ln_ps: torch.Tensor                 # P : ln(taille de chemin)
    minutes_acces: torch.Tensor         # P
    frontieres: torch.Tensor            # P × F : passages par chaque point frontière (entrée + sortie)
    minutes: torch.Tensor               # R
    km_hors_auto: torch.Tensor          # R
    km_estime: torch.Tensor             # R
    route_section: torch.Tensor         # R × S, creuse
    classe_section: torch.Tensor        # S : indice dans CLASSES

    @property
    def n_paires(self) -> int:
        return int(self.n3.shape[0])


def softmax_par_groupe(u: torch.Tensor, groupe: torch.Tensor, n_groupes: int) -> torch.Tensor:
    """Softmax de u au sein de chaque groupe (chemins d'une même paire). Un groupe tout à −∞ donne 0."""
    m = torch.full((n_groupes,), -torch.inf, dtype=u.dtype).scatter_reduce(0, groupe, u, reduce="amax")
    m = torch.where(torch.isfinite(m), m, torch.zeros_like(m))
    e = torch.exp(u - m[groupe])
    s = torch.zeros(n_groupes, dtype=u.dtype).index_add(0, groupe, e)
    return e / torch.where(s > 0, s, torch.ones_like(s))[groupe]


class ModeleAffectation(nn.Module):
    """Logit à taille de chemin. μ, g et b en échelle log ; β libres."""

    POSITIFS = ["mu", "croissance"]
    LIBRES = ["beta_hors_autoroute", "beta_estime"]
    NOMS = POSITIFS[:1] + LIBRES + POSITIFS[1:]

    def __init__(self, initiaux: dict[str, float], fond: dict[str, float], frontieres: list[str]) -> None:
        super().__init__()
        brut = {n: np.log(initiaux[n]) for n in self.POSITIFS} | {n: float(initiaux[n]) for n in self.LIBRES}
        self.brut = nn.ParameterDict({n: nn.Parameter(torch.tensor(v, dtype=torch.float64)) for n, v in brut.items()})
        self.brut_fond = nn.Parameter(torch.tensor(np.log([fond[c] for c in CLASSES]), dtype=torch.float64))
        self.frontieres = list(frontieres)
        self.beta_frontiere = nn.Parameter(torch.full((len(frontieres),), float(initiaux["beta_frontiere"]),
                                                      dtype=torch.float64))

    def theta(self) -> dict[str, torch.Tensor]:
        """Paramètres dans leur unité."""
        t = {n: torch.exp(self.brut[n]) for n in self.POSITIFS} | {n: self.brut[n] for n in self.LIBRES}
        t["fond"] = torch.exp(self.brut_fond)
        t["beta_frontiere"] = self.beta_frontiere
        return t

    def couts(self, d: Donnees, t: dict[str, torch.Tensor] | None = None) -> torch.Tensor:
        """Coût généralisé de chaque chemin (minutes équivalentes)."""
        t = t or self.theta()
        poids = torch.stack([torch.ones((), dtype=torch.float64), t["beta_hors_autoroute"], t["beta_estime"]])
        return d.composantes @ poids + d.minutes_acces + d.frontieres @ t["beta_frontiere"]

    def probabilites(self, d: Donnees, masque: torch.Tensor | None = None) -> torch.Tensor:
        """P(chemin | paire). `masque` (P, booléen) : chemins interdits (route fermée)."""
        t = self.theta()
        u = -t["mu"] * self.couts(d, t) + d.ln_ps
        if masque is not None:
            u = torch.where(masque, torch.full_like(u, -torch.inf), u)
        return softmax_par_groupe(u, d.paire, d.n_paires)

    def flux_routes(self, d: Donnees, p: torch.Tensor) -> torch.Tensor:
        """Camions par jour (2025) sur chaque route."""
        volume = self.theta()["croissance"] * d.n3[d.paire] * p / 365.0
        return torch.sparse.mm(d.chemin_route.t(), volume[:, None])[:, 0]

    def camions_sections(self, d: Donnees, flux: torch.Tensor) -> torch.Tensor:
        """Camions par jour prédits sur chaque section (ENR affectée + fond local)."""
        return torch.sparse.mm(d.route_section.t(), flux[:, None])[:, 0] + self.theta()["fond"][d.classe_section]

    def forward(self, d: Donnees, masque: torch.Tensor | None = None) -> tuple[torch.Tensor, ...]:
        """(P par chemin, flux par route, camions par section)."""
        p = self.probabilites(d, masque)
        f = self.flux_routes(d, p)
        return p, f, self.camions_sections(d, f)

    def valeurs(self) -> dict[str, float]:
        """Paramètres calés, en nombres Python (pour MLflow, JSON, l'API)."""
        t = self.theta()
        v = {n: float(t[n]) for n in self.NOMS}
        v.update({f"fond_{c}": float(x) for c, x in zip(CLASSES, t["fond"], strict=True)})
        v.update({f"beta_frontiere_{f}": float(x) for f, x in zip(self.frontieres, t["beta_frontiere"], strict=True)})
        return v
