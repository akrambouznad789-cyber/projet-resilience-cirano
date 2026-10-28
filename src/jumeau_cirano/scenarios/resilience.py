"""
resilience.py
=============
Fermer une liaison (ou une seule de ses routes) et mesurer l'effet, avec le modèle calé.

Pseudo-code
-----------
référence  : P(p) sans fermeture → coût composite, minutes et km attendus de chaque paire de lieux
fermeture  : les itinéraires qui empruntent une route fermée sont interdits (P = 0) ; le logit
             redistribue les camions sur les itinéraires restants de la même paire
paire sans itinéraire restant : plus court chemin sur le graphe des routes privé des routes fermées
             (tout-ou-rien) ; s'il n'y en a pas, la demande n'est pas desservie (coupure)
impacts (par an, en 2025) :
    Δ heures équivalentes = Σ camions × Δ coût composite (logsum)       → CLASSEMENT de criticité
        paires détournées en tout-ou-rien : minutes du détour − minutes du meilleur itinéraire de référence
    Δ camions-heures (temps moyen) et Δ camions-km                       → descriptifs
    trajets non desservis                                                → coupures, signalées à part
    tonnes et valeur des marchandises qui empruntaient la liaison (avant fermeture)
    coût de la perturbation = Δ heures équivalentes × coût horaire d'un camion

Pourquoi le logsum et pas le temps moyen : dans un logit, une partie des camions prend un
itinéraire plus lent que le meilleur. Fermer cet itinéraire fait BAISSER le temps moyen
(157 fermetures de route sur 263 au premier essai) alors que personne n'y gagne. Le coût
composite, lui, ne peut que monter quand une option disparaît.
"""

from __future__ import annotations

from dataclasses import dataclass

import networkx as nx
import numpy as np
import pandas as pd
import torch

from jumeau_cirano.modele.affectation import ModeleAffectation
from jumeau_cirano.modele.tenseurs import Artefact


@dataclass
class Contexte:
    """Ce qui ne change pas d'un scénario à l'autre."""

    art: Artefact
    modele: ModeleAffectation
    h: nx.DiGraph                  # graphe des routes (nœud → R:route → nœud), pour les détours
    frontieres: set[str]
    paires: pd.DataFrame           # n3, t3 (trajets et tonnes ENR par an)
    minutes: torch.Tensor          # P : minutes de chaque itinéraire (accès compris)
    km: torch.Tensor               # P : km de chaque itinéraire (accès compris)
    km_route: dict[str, float]
    p_ref: torch.Tensor            # P : probabilités sans fermeture
    c_ref: np.ndarray              # paires : coût composite sans fermeture (minutes équivalentes)
    minutes_min_ref: np.ndarray    # paires : minutes du plus rapide des itinéraires de référence

    @classmethod
    def creer(cls, art: Artefact, modele: ModeleAffectation, h: nx.DiGraph, frontieres: set[str],
              paires: pd.DataFrame) -> Contexte:
        d = art.donnees
        km_r = torch.as_tensor(art.routes["km"].to_numpy(dtype=float), dtype=torch.float64)
        km = torch.sparse.mm(d.chemin_route, km_r[:, None])[:, 0] + torch.as_tensor(
            art.chemins["km_acces"].to_numpy(dtype=float), dtype=torch.float64)
        minutes = d.composantes[:, 0] + d.minutes_acces
        with torch.no_grad():
            p_ref, c_ref = modele.probabilites(d), modele.cout_compose(d).numpy()
        minutes_min = torch.full((d.n_paires,), torch.inf, dtype=torch.float64).scatter_reduce(
            0, d.paire, minutes, reduce="amin").numpy()
        return cls(art, modele, h, frontieres, paires, minutes, km,
                   dict(zip(art.routes["ID_ROUTE"], art.routes["km"], strict=True)), p_ref, c_ref, minutes_min)


def par_paire(x: torch.Tensor, ctx: Contexte) -> np.ndarray:
    d = ctx.art.donnees
    return torch.zeros(d.n_paires, dtype=torch.float64).index_add(0, d.paire, x).numpy()


@torch.no_grad()
def fermer(ctx: Contexte, routes_fermees: set[str], cout_horaire: float, valeur_tonne: float) -> dict[str, float]:
    """Impacts annuels (2025) de la fermeture simultanée de `routes_fermees`."""
    d, art = ctx.art.donnees, ctx.art
    fermees = torch.as_tensor(art.routes["ID_ROUTE"].isin(routes_fermees).to_numpy(dtype=float), dtype=torch.float64)
    touche = torch.sparse.mm(d.chemin_route, fermees[:, None])[:, 0] > 0
    p = ctx.modele.probabilites(d, masque=touche)
    g = float(ctx.modele.theta()["croissance"])
    camions = g * ctx.paires["n3"].to_numpy()

    minutes_ref, km_ref = par_paire(ctx.p_ref * ctx.minutes, ctx), par_paire(ctx.p_ref * ctx.km, ctx)
    minutes_new, km_new = par_paire(p * ctx.minutes, ctx), par_paire(p * ctx.km, ctx)
    sans_chemin = (par_paire(p, ctx) < 0.5) & (par_paire(ctx.p_ref, ctx) > 0.5)   # privées par CETTE fermeture
    sur_liaison = par_paire(ctx.p_ref * touche, ctx)               # part de la paire qui empruntait la liaison
    c_new = ctx.modele.cout_compose(d, masque=touche).numpy()
    delta_eq = np.where(np.isfinite(c_new) & np.isfinite(ctx.c_ref), c_new - ctx.c_ref, 0.0)

    # Paires privées de tout itinéraire : détour par le plus court chemin sur le réseau dégradé
    non_desservis = 0.0
    if sans_chemin.any():
        h = ctx.h.subgraph([n for n in ctx.h.nodes if not (n.startswith("R:") and n[2:] in routes_fermees)])
        combos = art.chemins.loc[art.chemins["paire"].isin(np.flatnonzero(sans_chemin)),
                                 ["paire", "entree", "sortie", "minutes_acces", "km_acces"]].drop_duplicates(
            ["paire", "entree", "sortie"])
        cache: dict[tuple, tuple[float, float] | None] = {}
        meilleur: dict[int, tuple[float, float]] = {}
        for c in combos.itertuples():
            cle = (c.entree, c.sortie)
            if cle not in cache:
                vue = h.subgraph([n for n in h.nodes if n not in ctx.frontieres or n in cle])
                try:
                    chemin = nx.shortest_path(vue, c.entree, c.sortie, weight="minutes")
                    cache[cle] = (nx.path_weight(vue, chemin, "minutes"),
                                  sum(ctx.km_route[n[2:]] for n in chemin if isinstance(n, str) and n.startswith("R:")))
                except (nx.NetworkXNoPath, nx.NodeNotFound):
                    cache[cle] = None
            if cache[cle] is not None:
                mn, km = cache[cle][0] + c.minutes_acces, cache[cle][1] + c.km_acces
                if c.paire not in meilleur or mn < meilleur[c.paire][0]:
                    meilleur[c.paire] = (mn, km)
        for i in np.flatnonzero(sans_chemin):
            if i in meilleur:
                minutes_new[i], km_new[i] = meilleur[i]
                delta_eq[i] = max(meilleur[i][0] - ctx.minutes_min_ref[i], 0.0)   # détour ≥ réseau intact
            else:
                non_desservis += camions[i]
                minutes_new[i], km_new[i] = minutes_ref[i], km_ref[i]

    delta_h = float((camions * (minutes_new - minutes_ref)).sum() / 60)
    delta_eq_h = float((camions * delta_eq).sum() / 60)
    tonnes = float((g * ctx.paires["t3"].to_numpy() * sur_liaison).sum())
    return {"camions_sur_liaison": float((camions * sur_liaison).sum()),
            "delta_heures_equivalentes": delta_eq_h,
            "delta_camions_heures": delta_h, "delta_camions_km": float((camions * (km_new - km_ref)).sum()),
            "trajets_non_desservis": non_desservis, "tonnes_sur_liaison": tonnes,
            "valeur_sur_liaison": tonnes * valeur_tonne, "cout_perturbation": delta_eq_h * cout_horaire}


def toutes_les_fermetures(ctx: Contexte, cout_horaire: float, valeur_tonne: float) -> pd.DataFrame:
    """Chaque liaison fermée (toutes ses routes), puis chaque route d'une liaison qui en a plusieurs."""
    routes = ctx.art.routes
    lignes = []
    for arc, grp in routes.groupby("ID_ARC"):
        lignes.append({"scenario": "liaison", "ID_ARC": int(arc), "ID_ROUTE": "",
                       **fermer(ctx, set(grp["ID_ROUTE"]), cout_horaire, valeur_tonne)})
        if len(grp) > 1:
            for r in grp.itertuples():
                lignes.append({"scenario": "route", "ID_ARC": int(arc), "ID_ROUTE": r.ID_ROUTE,
                               **fermer(ctx, {r.ID_ROUTE}, cout_horaire, valeur_tonne)})
    return pd.DataFrame(lignes)
