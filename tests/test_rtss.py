"""Réparation de la topologie RTSS sur des lignes synthétiques."""

from __future__ import annotations

import geopandas as gpd
import networkx as nx
import numpy as np
import pytest
from shapely.geometry import LineString

from jumeau_cirano.reseau.rtss import (
    composante_principale,
    construire_graphe,
    noder,
    raccorder_traversees,
    rattacher,
)

VITESSES = {"10": 90.0, "20": 60.0}


def lignes(*geoms: LineString, classes: list[str] | None = None) -> gpd.GeoDataFrame:
    n = len(geoms)
    return gpd.GeoDataFrame({
        "num_rts": [f"L{i}" for i in range(n)], "num_route": ["00020"] * n,
        "cod_clasf_": classes or ["10"] * n, "des_clasf_": ["Autoroute"] * n,
        "cod_sous_r": ["0"] * n, "cod_cote_c": ["C"] * n, "nom_sous_r": [""] * n,
    }, geometry=list(geoms), crs="EPSG:32198")


@pytest.fixture
def cas() -> gpd.GeoDataFrame:
    return lignes(
        LineString([(0, 0), (100, 0)]),      # A : route principale
        LineString([(50, 0.5), (50, 50)]),   # B : jonction en T, s'arrête à 0,5 m de A
        LineString([(80, -30), (80, 30)]),   # C : passe au-dessus de A (pont), aucune extrémité proche
        LineString([(100.4, 0), (200, 0)]),  # D : prolonge A, extrémités à 0,4 m
        LineString([(0, 60), (0, 100)]),     # E : isolée, trop loin pour être raccordée
    )


def test_jonction_en_t_coupe_la_ligne(cas):
    liens, noeuds = noder(cas, tol_noeud=1.0, tol_jonction=25.0)
    morceaux_a = liens[liens["num_rts"] == "L0"]
    assert len(morceaux_a) == 2                                   # A coupée en 50
    assert sorted(morceaux_a["longueur_m"].round(1)) == [50.0, 50.0]
    g = construire_graphe(liens, noeuds, VITESSES)
    b = liens[liens["num_rts"] == "L1"].iloc[0]
    assert nx.has_path(g, int(b.u), int(morceaux_a.iloc[0].u))


def test_pont_sans_noeud(cas):
    liens, noeuds = noder(cas, tol_noeud=1.0, tol_jonction=25.0)
    assert (liens["num_rts"] == "L2").sum() == 1                  # C n'est pas coupée
    g = construire_graphe(liens, noeuds, VITESSES)
    c = liens[liens["num_rts"] == "L2"].iloc[0]
    a = liens[liens["num_rts"] == "L0"].iloc[0]
    assert not nx.has_path(g, int(c.u), int(a.u))                 # pas de carrefour sous le pont


def test_extremites_confondues_et_isolee(cas):
    liens, noeuds = noder(cas, tol_noeud=1.0, tol_jonction=25.0)
    g = composante_principale(construire_graphe(liens, noeuds, VITESSES))
    # A, B et D forment la composante principale : 4 liens (A en deux morceaux)
    assert g.number_of_edges() == 4
    assert "L4" not in {liens.loc[k, "num_rts"] for _, _, k in g.edges(keys=True)}


def test_minutes_selon_la_classe():
    liens, noeuds = noder(lignes(LineString([(0, 0), (1000, 0)]), LineString([(1000, 0), (2000, 0)]),
                                 classes=["10", "20"]), 1.0, 25.0)
    g = construire_graphe(liens, noeuds, VITESSES)
    minutes = sorted(round(d["minutes"], 3) for _, _, d in g.edges(data=True))
    assert minutes == [round(1 / 90 * 60, 3), round(1 / 60 * 60, 3)]


def test_rattacher_au_noeud_le_plus_proche(cas):
    liens, noeuds = noder(cas, 1.0, 25.0)
    g = construire_graphe(liens, noeuds, VITESSES)
    ids, d = rattacher(np.array([[199.0, 3.0]]), g)
    assert g.nodes[int(ids[0])]["x"] == pytest.approx(200.0) and d[0] == pytest.approx(np.hypot(1, 3))


def test_raccord_municipal_meme_route_seulement():
    gdf = lignes(LineString([(0, 0), (1000, 0)]), LineString([(4000, 0), (5000, 0)]),  # 00020 : trou de 3 km
                 LineString([(0, 500), (1000, 500)]), LineString([(9000, 500), (9500, 500)]))  # 00116 : 8 km
    gdf.loc[2:, "num_route"] = "00116"
    liens, noeuds = noder(gdf, 1.0, 25.0)
    liens, noeuds = raccorder_traversees(liens, noeuds, max_km=6.0, facteur_detour=1.3)
    raccords = liens[liens["raccord"]]
    assert len(raccords) == 1 and raccords.iloc[0]["num_route"] == "00020"
    assert raccords.iloc[0]["longueur_m"] == pytest.approx(3000 * 1.3)
    g = construire_graphe(liens, noeuds, {**VITESSES, "MU": 40.0})
    assert nx.has_path(g, int(liens.iloc[0].u), int(liens.iloc[1].v))


def test_carrefour_en_croix():
    # X s'arrête des deux côtés de Y (ses deux bouts se touchent) : Y doit être coupée au carrefour
    gdf = lignes(LineString([(0, 0), (100, 0)]),                                  # Y
                 LineString([(50, -50), (50, 0.5)]), LineString([(50, 0.5), (50, 50)]))  # X en deux morceaux
    liens, noeuds = noder(gdf, tol_noeud=1.0, tol_jonction=25.0, tol_croisement=2.0)
    assert (liens["num_rts"] == "L0").sum() == 2
    g = construire_graphe(liens, noeuds, VITESSES)
    assert nx.is_connected(g) and g.number_of_nodes() == 5


def test_raccord_ignore_les_chaussees_deja_reliees():
    # Route 00138 à chaussées séparées (G et D, reliées entre elles), puis un trou de 900 m
    gdf = lignes(LineString([(0, 0), (1000, 0)]),                    # chaussée G
                 LineString([(0, 30), (1000, 30)]),                  # chaussée D, 30 m à côté
                 LineString([(0, 0), (0, 30)]),                      # liaison G–D au départ
                 LineString([(1900, 15), (3000, 15)]))               # suite de la route après le trou
    gdf["num_route"] = "00138"
    liens, noeuds = noder(gdf, 1.0, 25.0)
    liens, noeuds = raccorder_traversees(liens, noeuds, max_km=6.0, facteur_detour=1.3)
    raccords = liens[liens["raccord"]]
    assert len(raccords) == 1                                        # pas de raccord G ↔ D
    assert raccords.iloc[0].geometry.length == pytest.approx(np.hypot(900, 15), abs=1)
