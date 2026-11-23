"""Routes de repli du Module 3 : le temps et la part d'autoroute viennent du Module 1 (depuis le 2026-11-23).

Une liaison sans route sur le RTSS garde son tracé OSRM comme route unique. Son coût se lisait à 70 km/h, sans
km d'autoroute ; il reprend désormais ce que le Module 1 mesure sur ce même tracé (temps_parcours_liaisons.py).
"""

from __future__ import annotations

from types import SimpleNamespace

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString, Point

from jumeau_cirano.donnees.liaisons import charger_temps_module1
from jumeau_cirano.reseau.commande import VITESSE_REPLI_KMH, route_osrm

TRACE = LineString([(0, 0), (30_000, 0)])            # 30 km


def test_route_de_repli_avec_le_temps_du_module1():
    temps = pd.DataFrame({"temps_min": [25.0], "part_autoroute": [0.4]}, index=pd.Index([12], name="ID_ARC"))
    r = route_osrm(12, TRACE, temps)
    assert r["km"] == pytest.approx(30)
    assert r["minutes"] == pytest.approx(25)
    assert r["km_autoroute"] == pytest.approx(12)                  # 0,4 × 30 km
    assert r["temps_source"] == "module1"
    assert (r["ID_ROUTE"], r["rang"], r["source"]) == ("12-1", 1, "osrm")


def test_route_de_repli_sans_temps_du_module1():
    temps = pd.DataFrame({"temps_min": [np.nan], "part_autoroute": [np.nan]}, index=pd.Index([12], name="ID_ARC"))
    for t in (temps, temps.drop(index=12)):                          # temps manquant, ou liaison absente
        r = route_osrm(12, TRACE, t)
        assert r["minutes"] == pytest.approx(30 / VITESSE_REPLI_KMH * 60)
        assert np.isnan(r["km_autoroute"])
        assert r["temps_source"] == "70 km/h"


def test_charger_temps_module1(tmp_path):
    fichier = tmp_path / "graphe_routier_djma.gpkg"
    cfg = SimpleNamespace(entrees=SimpleNamespace(djma=str(fichier)))
    arcs = gpd.GeoDataFrame({"ID_ARC": [1, 2], "statut": ["ok", "ok"]}, geometry=[Point(0, 0), Point(1, 1)],
                            crs="EPSG:32198")
    arcs.to_file(fichier, driver="GPKG")
    with pytest.raises(ValueError, match="temps_parcours_liaisons"):
        charger_temps_module1(cfg)
    arcs.assign(temps_min=[10.0, 20.0], part_autoroute=[0.5, 0.0]).to_file(fichier, driver="GPKG")
    t = charger_temps_module1(cfg)
    assert list(t.columns) == ["temps_min", "part_autoroute"]
    assert t.loc[2, "temps_min"] == pytest.approx(20)
