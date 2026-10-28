"""Routes alternatives par liaison, sur un réseau synthétique dont on connaît la réponse.

    3 ──────────── 4         route parallèle à 500 m : 11 km à 90 km/h (≈ 1,1 × la meilleure)
    │              │
    1 ──────────── 2         autoroute directe : 10 km à 90 km/h
     \\            /
      ─── 5 ──────           grand détour par (5 km, -8 km) : ≈ 1,9 × la meilleure
"""

from __future__ import annotations

import networkx as nx
import pandas as pd
import pytest
import shapely

from jumeau_cirano.config import ParamAlternatives
from jumeau_cirano.reseau.alternatives import decrire_routes, routes_alternatives

P = ParamAlternatives(k=3, facteur_penalite=1.5, detour_max=1.3, recouvrement_max=0.6, exclusion_noeud_tiers_km=0.3)


@pytest.fixture
def reseau() -> tuple[nx.MultiGraph, pd.Series]:
    xy = {1: (0, 0), 2: (10000, 0), 3: (0, 500), 4: (10000, 500), 5: (5000, -8000)}
    g = nx.MultiGraph()
    for n, (x, y) in xy.items():
        g.add_node(n, x=x, y=y)
    g.add_node("L:A", x=0, y=0)
    g.add_node("L:B", x=10000, y=0)
    geoms = {}
    for k, (u, v, classe, route) in enumerate([(1, 2, "10", "00020"), (1, 3, "20", "00132"), (3, 4, "10", "00040"),
                                               (4, 2, "20", "00132"), (1, 5, "20", "00116"), (5, 2, "20", "00116")]):
        geoms[k] = shapely.LineString([xy[u], xy[v]])
        km = geoms[k].length / 1000
        g.add_edge(u, v, key=k, km=km, minutes=km / 90 * 60, classe=classe, route=route)
    g.add_edge("L:A", 1, key="acces:A:1", km=0.0, minutes=0.0, classe="AC", route="")
    g.add_edge("L:B", 2, key="acces:B:2", km=0.0, minutes=0.0, classe="AC", route="")
    return g, pd.Series(geoms)


def test_deux_routes_le_detour_est_rejete(reseau):
    g, geom = reseau
    routes = routes_alternatives(g, geom, "L:A", "L:B", P, tampon_m=100.0)
    assert len(routes) == 2
    assert routes[0]["km"] == pytest.approx(10.0) and routes[1]["km"] == pytest.approx(11.0)
    assert routes[1]["minutes"] / routes[0]["minutes"] == pytest.approx(1.1)


def test_route_qui_frole_une_autre_ville_est_rejetee(reseau):
    g, geom = reseau
    autre_ville = shapely.multipoints([(5000, 600)])          # à 100 m de la route parallèle
    routes = routes_alternatives(g, geom, "L:A", "L:B", P, tampon_m=100.0, noeuds_tiers=autre_ville)
    assert len(routes) == 1


def test_description_des_routes(reseau):
    g, geom = reseau
    routes = routes_alternatives(g, geom, "L:A", "L:B", P, tampon_m=100.0)
    d = decrire_routes(routes[1]["aretes"], g)
    assert d["km_autoroute"] == pytest.approx(10.0) and d["routes"] == "A-40 · R-132"   # 2 × 500 m de R-132
