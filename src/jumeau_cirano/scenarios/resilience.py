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
    Δ heures du meilleur itinéraire = Σ camions × Δ min_p coût_p          → ordre de grandeur du détour
        même coût généralisé, sans la valeur de la diversité du logit ; mêmes détours tout-ou-rien
    Δ camions-heures (temps moyen) et Δ camions-km                       → descriptifs
    trajets non desservis                                                → coupures, signalées à part
    tonnes et valeur des marchandises qui empruntaient la liaison (avant fermeture)
    report de trafic : Δ camions par jour sur chaque route (détours compris)           → carte
    coût de la perturbation = Δ heures équivalentes × coût horaire d'un camion

Pourquoi le logsum et pas le temps moyen : dans un logit, une partie des camions prend un
itinéraire plus lent que le meilleur. Fermer cet itinéraire fait BAISSER le temps moyen
(157 fermetures de route sur 263 au premier essai) alors que personne n'y gagne. Le coût
composite, lui, ne peut que monter quand une option disparaît.

Pourquoi aussi le meilleur itinéraire : le logsum compte la valeur de la diversité. Perdre une
option sur deux à peu près équivalentes coûte ln 2/μ ≈ 9 min par camion sans aucun détour. Pour
la fermeture d'une seule route, il vaut ainsi en médiane 11 fois le temps réellement perdu. Le
Δ du meilleur itinéraire ne compte que le surcoût de la meilleure option restante.
"""

from __future__ import annotations

from dataclasses import dataclass

import networkx as nx
import numpy as np
import pandas as pd
import torch

from jumeau_cirano.modele.affectation import Donnees, ModeleAffectation
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
    couts: torch.Tensor            # P : coût généralisé de chaque itinéraire (minutes équivalentes)
    cout_min_ref: np.ndarray       # paires : coût du meilleur itinéraire sans fermeture
    flux_ref: np.ndarray           # R : camions par jour (2025) sur chaque route sans fermeture

    @classmethod
    def creer(cls, art: Artefact, modele: ModeleAffectation, h: nx.DiGraph, frontieres: set[str],
              paires: pd.DataFrame) -> Contexte:
        d = art.donnees
        km_r = torch.as_tensor(art.routes["km"].to_numpy(dtype=float), dtype=torch.float64)
        km = torch.sparse.mm(d.chemin_route, km_r[:, None])[:, 0] + torch.as_tensor(
            art.chemins["km_acces"].to_numpy(dtype=float), dtype=torch.float64)
        minutes = d.composantes[:, 0] + d.minutes_acces
        with torch.no_grad():
            p_ref, c_ref, couts = modele.probabilites(d), modele.cout_compose(d).numpy(), modele.couts(d)
            flux_ref = modele.flux_routes(d, p_ref).numpy()
        return cls(art, modele, h, frontieres, paires, minutes, km,
                   dict(zip(art.routes["ID_ROUTE"], art.routes["km"], strict=True)), p_ref, c_ref,
                   minimum_par_paire(minutes, d), couts, minimum_par_paire(couts, d), flux_ref)


def minimum_par_paire(x: torch.Tensor, d: Donnees) -> np.ndarray:
    """Plus petite valeur de x parmi les itinéraires de chaque paire (+∞ si aucun)."""
    return torch.full((d.n_paires,), torch.inf, dtype=torch.float64).scatter_reduce(
        0, d.paire, x, reduce="amin").numpy()


def par_paire(x: torch.Tensor, ctx: Contexte) -> np.ndarray:
    d = ctx.art.donnees
    return torch.zeros(d.n_paires, dtype=torch.float64).index_add(0, d.paire, x).numpy()


def fermer(ctx: Contexte, routes_fermees: set[str], cout_horaire: float, valeur_tonne: float) -> dict[str, float]:
    """Impacts annuels (2025) de la fermeture simultanée de `routes_fermees`."""
    return fermer_avec_flux(ctx, routes_fermees, cout_horaire, valeur_tonne, avec_flux=False)[0]


@torch.no_grad()
def fermer_avec_flux(ctx: Contexte, routes_fermees: set[str], cout_horaire: float, valeur_tonne: float,
                     avec_flux: bool = True) -> tuple[dict[str, float], np.ndarray | None]:
    """Impacts de la fermeture et camions par jour (2025) sur chaque route après report (détours compris)."""
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
    cout_min_new = minimum_par_paire(torch.where(touche, torch.full_like(ctx.couts, torch.inf), ctx.couts), d)
    delta_best = np.where(np.isfinite(cout_min_new) & np.isfinite(ctx.cout_min_ref),
                          cout_min_new - ctx.cout_min_ref, 0.0)

    # Paires privées de tout itinéraire : détour par le plus court chemin sur le réseau dégradé
    non_desservis = 0.0
    if sans_chemin.any():
        h = ctx.h.subgraph([n for n in ctx.h.nodes if not (n.startswith("R:") and n[2:] in routes_fermees)])
        combos = art.chemins.loc[art.chemins["paire"].isin(np.flatnonzero(sans_chemin)),
                                 ["paire", "entree", "sortie", "minutes_acces", "km_acces"]].drop_duplicates(
            ["paire", "entree", "sortie"])
        cache: dict[tuple, tuple[float, float, list[str]] | None] = {}
        meilleur: dict[int, tuple[float, float, list[str]]] = {}
        for c in combos.itertuples():
            cle = (c.entree, c.sortie)
            if cle not in cache:
                vue = h.subgraph([n for n in h.nodes if n not in ctx.frontieres or n in cle])
                try:
                    chemin = nx.shortest_path(vue, c.entree, c.sortie, weight="minutes")
                    routes = [n[2:] for n in chemin if isinstance(n, str) and n.startswith("R:")]
                    cache[cle] = (nx.path_weight(vue, chemin, "minutes"), sum(ctx.km_route[r] for r in routes), routes)
                except (nx.NetworkXNoPath, nx.NodeNotFound):
                    cache[cle] = None
            if cache[cle] is not None:
                mn, km = cache[cle][0] + c.minutes_acces, cache[cle][1] + c.km_acces
                if c.paire not in meilleur or mn < meilleur[c.paire][0]:
                    meilleur[c.paire] = (mn, km, cache[cle][2])
        for i in np.flatnonzero(sans_chemin):
            if i in meilleur:
                minutes_new[i], km_new[i] = meilleur[i][:2]
                delta_eq[i] = delta_best[i] = max(meilleur[i][0] - ctx.minutes_min_ref[i], 0.0)   # ≥ intact
            else:
                non_desservis += camions[i]
                minutes_new[i], km_new[i] = minutes_ref[i], km_ref[i]

    delta_h = float((camions * (minutes_new - minutes_ref)).sum() / 60)
    delta_eq_h = float((camions * delta_eq).sum() / 60)
    tonnes = float((g * ctx.paires["t3"].to_numpy() * sur_liaison).sum())
    flux = None
    if avec_flux:
        flux = ctx.modele.flux_routes(d, p).numpy().copy()
        if sans_chemin.any():
            i_route = {r: k for k, r in enumerate(art.routes["ID_ROUTE"])}
            for i, (_, _, routes) in meilleur.items():
                flux[[i_route[r] for r in routes]] += camions[i] / 365.0
    impacts = {"camions_sur_liaison": float((camions * sur_liaison).sum()),
            "delta_heures_equivalentes": delta_eq_h,
            "delta_heures_meilleur": float((camions * delta_best).sum() / 60),
            "delta_camions_heures": delta_h, "delta_camions_km": float((camions * (km_new - km_ref)).sum()),
            "trajets_non_desservis": non_desservis, "tonnes_sur_liaison": tonnes,
            "valeur_sur_liaison": tonnes * valeur_tonne, "cout_perturbation": delta_eq_h * cout_horaire}
    return impacts, flux


def toutes_les_fermetures(ctx: Contexte, cout_horaire: float, valeur_tonne: float,
                          seuil_report: float = 0.5) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Chaque liaison fermée (toutes ses routes), puis chaque route d'une liaison qui en a plusieurs.

    Renvoie les impacts (une ligne par scénario) et les reports de trafic (une ligne par scénario et par
    route dont le trafic change d'au moins `seuil_report` camions par jour).
    """
    routes = ctx.art.routes
    ids = routes["ID_ROUTE"].to_numpy()
    lignes, reports = [], []

    def scenario(nom: str, arc: int, route: str, fermees: set[str]) -> None:
        impacts, flux = fermer_avec_flux(ctx, fermees, cout_horaire, valeur_tonne)
        lignes.append({"scenario": nom, "ID_ARC": arc, "ID_ROUTE": route, **impacts})
        delta = flux - ctx.flux_ref
        k = np.flatnonzero(np.abs(delta) >= seuil_report)
        reports.append(pd.DataFrame({"scenario": nom, "ID_ARC": arc, "ID_ROUTE": route,
                                     "route": ids[k], "delta_camions_jour": delta[k]}))

    for arc, grp in routes.groupby("ID_ARC"):
        scenario("liaison", int(arc), "", set(grp["ID_ROUTE"]))
        if len(grp) > 1:
            for r in grp.itertuples():
                scenario("route", int(arc), r.ID_ROUTE, {r.ID_ROUTE})
    return pd.DataFrame(lignes), pd.concat(reports, ignore_index=True)
