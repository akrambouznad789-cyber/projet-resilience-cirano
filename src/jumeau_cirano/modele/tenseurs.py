"""
tenseurs.py
===========
Artefact (Parquet) → tenseurs du modèle : incidences creuses, cibles de comptage
et matrices de la vraisemblance de l'enquête.

Vraisemblance de l'enquête : composite, poste par poste. Pour un groupe k de
camions (même paire de lieux, même poste d'interception i_k, mêmes postes
québécois franchis O_k) et chaque poste s qu'un de ses chemins peut franchir :

    q_ks = P(franchir s | passer par i_k) = Σ_p P(p)·c_kp·1[s ∈ S_p] / Σ_p P(p)·c_kp
    ln L = Σ_k n_k Σ_s [ o_ks ln q_ks + (1 − o_ks) ln(1 − q_ks) ],   o_ks = 1[s ∈ O_k]

    S_p  = postes situés sur le chemin p
    c_kp = 1 si p passe par le poste d'interception (on ne voit un camion que s'il y
           passe : correction de l'échantillonnage) ; 1 pour un camion intercepté hors Québec

Seules comptent les lignes (k, s) informatives : 0 < q_ks < 1 possible, c.-à-d. s
sur certains chemins conformes à l'interception mais pas sur tous.

Pourquoi composite : deux versions ont été essayées et écartées, chiffres à l'appui
(tests/test_modele.py) : (1) un crédit partiel de Jaccard entre O_k et S_p biaisait
μ de +4 % et β de −5 % ; (2) la correspondance exacte O_k = S_p, sans biais, ne
gardait que 34 % des camions (un seul poste mal placé invalide tout le camion).
La vraisemblance composite est convergente, retrouve les vrais paramètres et
utilise tous les camions ; un poste mal placé ne coûte que sa propre ligne.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from jumeau_cirano.modele.affectation import CLASSES, Donnees, creux


@dataclass
class Enquete:
    """Lignes (groupe k, poste s) informatives de la vraisemblance composite, et attributs des groupes."""

    numerateur: torch.Tensor     # L × P creuse : c_kp · 1[s ∈ S_p]
    denominateur: torch.Tensor   # L × P creuse : c_kp
    observe: torch.Tensor        # L : 1 si le camion a franchi s
    groupe: torch.Tensor         # L : indice du groupe de la ligne
    n: torch.Tensor              # G : camions enquêtés du groupe
    poste: np.ndarray            # G : poste d'interception (ou None)
    paire: np.ndarray            # G : indice de paire


@dataclass
class Artefact:
    donnees: Donnees
    enquete: Enquete
    routes: pd.DataFrame
    chemins: pd.DataFrame
    sections: pd.DataFrame       # sections portées par au moins une route, dans l'ordre des tenseurs
    observees: np.ndarray        # indices (dans `sections`) des sections mesurées en 2025
    y_obs: torch.Tensor          # camions par jour mesurés sur ces sections
    stations_chemin: list[frozenset]
    frontieres: list[str]


def construire_enquete(groupes: pd.DataFrame, par_paire: dict, stations: list[frozenset]) -> Enquete:
    """Lignes (k, s) informatives : s franchi par certains chemins conformes à l'interception, pas par tous."""
    lig, col, num, den, obs, grp = [], [], [], [], [], []
    for k, g in enumerate(groupes.itertuples()):
        observes = frozenset(s for s in str(g.franchis).split("|") if s)
        conformes = [p for p in par_paire.get(g.paire, [])
                     if g.poste is None or pd.isna(g.poste) or g.poste in stations[p]]
        if not conformes:
            continue
        candidats = frozenset().union(*(stations[p] for p in conformes)) - {g.poste}
        for s in sorted(candidats):
            sur = [p for p in conformes if s in stations[p]]
            if len(sur) == len(conformes):                 # franchi par tous : aucune information
                continue
            ligne = len(obs)
            for p in conformes:
                lig.append(ligne)
                col.append(p)
                den.append(1.0)
                num.append(float(s in stations[p]))
            obs.append(float(s in observes))
            grp.append(k)
    forme = (len(obs), len(stations))
    lig, col = np.array(lig, dtype=int), np.array(col, dtype=int)
    return Enquete(creux(lig, col, np.array(num), forme), creux(lig, col, np.array(den), forme),
                   torch.tensor(obs, dtype=torch.float64), torch.tensor(grp, dtype=torch.long),
                   torch.tensor(groupes["n"].to_numpy(dtype=float), dtype=torch.float64),
                   groupes["poste"].to_numpy(), groupes["paire"].to_numpy())


def charger_artefact(dossier: Path) -> Artefact:
    """Lit l'artefact et construit les tenseurs."""
    routes = pd.read_parquet(dossier / "routes.parquet").reset_index(drop=True)
    chemins = pd.read_parquet(dossier / "chemins.parquet").reset_index(drop=True)
    paires = pd.read_parquet(dossier / "paires.parquet")
    rs = pd.read_parquet(dossier / "routes_sections.parquet")
    sections = pd.read_parquet(dossier / "sections.parquet")
    postes = pd.read_parquet(dossier / "postes.parquet")
    groupes = pd.read_parquet(dossier / "groupes.parquet")

    i_route = {r: i for i, r in enumerate(routes["ID_ROUTE"])}
    listes = chemins["routes"].str.split("|")
    lignes = np.repeat(np.arange(len(chemins)), listes.map(len).to_numpy())
    colonnes = np.array([i_route[r] for ch in listes for r in ch])
    chemin_route = creux(lignes, colonnes, np.ones(len(lignes)), (len(chemins), len(routes)))

    sections = sections[sections["ide_sectn_trafc"].isin(rs["ide_sectn_trafc"])].reset_index(drop=True)
    i_sec = {s: i for i, s in enumerate(sections["ide_sectn_trafc"])}
    rs = rs[rs["ID_ROUTE"].isin(i_route)]
    route_section = creux(rs["ID_ROUTE"].map(i_route).to_numpy(), rs["ide_sectn_trafc"].map(i_sec).to_numpy(),
                          np.ones(len(rs)), (len(routes), len(sections)))
    classe = sections["classe"].where(sections["classe"].isin(CLASSES[:-1]), "autre").map(CLASSES.index)

    km_auto = routes["km_autoroute"].fillna(0.0)
    km_estime = routes["km_estime"].fillna(0.0)
    f64 = lambda x: torch.as_tensor(np.array(x, dtype=float), dtype=torch.float64)  # noqa: E731
    noms_frontieres = sorted({n for n in pd.concat([chemins["entree"], chemins["sortie"]]) if n.startswith("F")})
    passages = pd.DataFrame({f: (chemins["entree"] == f).astype(float) + (chemins["sortie"] == f).astype(float)
                             for f in noms_frontieres})
    par_route = torch.stack([f64(routes["minutes"]), f64((routes["km"] - km_auto - km_estime).clip(lower=0)),
                             f64(km_estime)], dim=1)
    d = Donnees(
        chemin_route=chemin_route, composantes=torch.sparse.mm(chemin_route, par_route),
        paire=torch.as_tensor(chemins["paire"].to_numpy(), dtype=torch.long),
        n3=f64(paires["n3"]), ln_ps=f64(np.log(chemins["ps"].clip(lower=1e-6))),
        minutes_acces=f64(chemins["minutes_acces"]),
        frontieres=f64(passages.to_numpy()),
        minutes=f64(routes["minutes"]), km_hors_auto=f64((routes["km"] - km_auto - km_estime).clip(lower=0)),
        km_estime=f64(km_estime), route_section=route_section,
        classe_section=torch.as_tensor(classe.to_numpy(), dtype=torch.long),
    )

    # Postes situés sur chaque chemin
    poste_route = postes.groupby("ID_ROUTE")["code"].apply(frozenset).to_dict()
    stations = [frozenset().union(*(poste_route.get(r, frozenset()) for r in ch)) for ch in listes]

    # Lignes de la vraisemblance composite
    par_paire = chemins.groupby("paire").indices
    enquete = construire_enquete(groupes, par_paire, stations)

    observees = np.flatnonzero(sections["observee"].to_numpy())
    return Artefact(d, enquete, routes, chemins, sections, observees,
                    f64(sections["camions_obs"].to_numpy()[observees]), stations, noms_frontieres)
