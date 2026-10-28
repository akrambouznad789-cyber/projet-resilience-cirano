"""
alternatives.py
===============
Plusieurs routes par liaison : entre deux villes voisines, les camions n'ont pas
qu'un seul choix (A-20 ou R-132 le long du fleuve, A-40 ou R-138…).

Méthode des pénalités : on prend le chemin le plus rapide, on rend ses liens
`facteur_penalite` fois plus chers, on cherche le suivant, et ainsi de suite.
Une route candidate est gardée si :
- son temps RÉEL (sans pénalité) ≤ `detour_max` × celui de la meilleure route ;
- au plus `recouvrement_max` de sa longueur longe (à moins du tampon) une route déjà gardée ;
- elle ne passe pas à moins de `exclusion_noeud_tiers_km` d'un autre nœud du graphe
  des liaisons (sinon c'est un autre chemin du graphe, A → C → B, pas une alternative).

Pseudo-code
-----------
penalite = {}
tant que moins de k routes et moins de 3k essais :
    chemin = plus court chemin (minutes × penalite), sans transit par une autre ville
    si nouveau et conforme aux trois règles : garder
    penalite[liens du chemin] ×= facteur_penalite
"""

from __future__ import annotations

import networkx as nx
import numpy as np
import pandas as pd
import shapely

from jumeau_cirano.config import ParamAlternatives
from jumeau_cirano.reseau.controle import geometrie, recouvrement


def poids_penalise(origine: str, destination: str, penalite: dict) -> callable:
    """Minutes × pénalité de chaque lien ; interdit le transit par les accès d'autres villes."""
    permis = {origine, destination}

    def poids(u, v, aretes: dict) -> float | None:
        if (isinstance(u, str) and u not in permis) or (isinstance(v, str) and v not in permis):
            return None
        return min(d["minutes"] * penalite.get(k, 1.0) for k, d in aretes.items())

    return poids


def aretes_penalisees(g: nx.MultiGraph, noeuds: list, penalite: dict) -> list[tuple]:
    """Arêtes (u, v, clé) du chemin, en choisissant entre parallèles avec la même pénalité."""
    return [(u, v, min(g[u][v], key=lambda k: g[u][v][k]["minutes"] * penalite.get(k, 1.0)))
            for u, v in zip(noeuds[:-1], noeuds[1:], strict=True)]


def routes_alternatives(
    g: nx.MultiGraph,
    geom_lien: pd.Series,
    origine: str,
    destination: str,
    p: ParamAlternatives,
    tampon_m: float,
    noeuds_tiers: shapely.Geometry | None = None,
) -> list[dict]:
    """Jusqu'à `p.k` routes entre deux nœuds d'accès, de la plus rapide à la moins rapide.

    `noeuds_tiers` : multipoint des autres nœuds du graphe des liaisons (même CRS).
    Chaque route : aretes, minutes, km, geometry (liens RTSS, sans les accès).
    """
    penalite: dict = {}
    gardees: list[dict] = []
    vus: set[tuple] = set()
    for _ in range(3 * p.k):
        try:
            noeuds = nx.shortest_path(g, origine, destination, weight=poids_penalise(origine, destination, penalite))
        except nx.NetworkXNoPath:
            break
        aretes = aretes_penalisees(g, noeuds, penalite)
        for _, _, k in aretes:
            penalite[k] = penalite.get(k, 1.0) * p.facteur_penalite
        signature = tuple(k for _, _, k in aretes)
        if signature in vus:
            continue
        vus.add(signature)
        route = {"aretes": aretes, "minutes": sum(g.edges[a]["minutes"] for a in aretes),
                 "km": sum(g.edges[a]["km"] for a in aretes), "geometry": geometrie(aretes, geom_lien)}
        if gardees:
            if route["minutes"] > p.detour_max * gardees[0]["minutes"]:
                continue
            if any(recouvrement(route["geometry"], r["geometry"], tampon_m) > p.recouvrement_max for r in gardees):
                continue
            if noeuds_tiers is not None and not noeuds_tiers.is_empty:
                if shapely.distance(route["geometry"], noeuds_tiers) < p.exclusion_noeud_tiers_km * 1000:
                    continue
        gardees.append(route)
        if len(gardees) >= p.k:
            break
    return gardees


def decrire_routes(aretes: list[tuple], g: nx.MultiGraph) -> dict:
    """km sur autoroute, km estimés (raccords et accès) et numéros de route principaux (ex. « A-20 · R-132 »)."""
    km = pd.DataFrame([{"km": g.edges[a]["km"], "classe": g.edges[a]["classe"], "route": g.edges[a]["route"]}
                       for a in aretes])
    estime = km["classe"].isin(["MU", "AC"])
    numerotees = km[~estime & km["route"].str.fullmatch(r"\d{5}") & (km["route"].str[:2] == "00")]
    par_route = numerotees.groupby("route").agg(km=("km", "sum"), classe=("classe", lambda c: c.mode().iat[0]))
    par_route = par_route[par_route["km"] >= 1.0].sort_values("km", ascending=False).head(3)
    noms = [f"{'A' if r.classe == '10' else 'R'}-{int(i)}" for i, r in par_route.iterrows()]
    return {"km_autoroute": float(km.loc[km["classe"] == "10", "km"].sum()),
            "km_estime": float(km.loc[estime, "km"].sum()),
            "routes": " · ".join(noms)}


def noeuds_tiers_de(points: pd.Series, a: str, b: str) -> shapely.Geometry:
    """Multipoint des nœuds du graphe des liaisons autres que `a` et `b` (index = ID)."""
    return shapely.multipoints(np.array([(p.x, p.y) for i, p in points.items() if i not in (a, b)]))
