"""Temps de parcours des liaisons du Module 1 (scripts/temps_parcours_liaisons.py), sur des tracés synthétiques.

Demande du directeur de recherche (2026-11-23) : un temps sur chaque arc. Il se mesure tranche par tranche sur
le tracé, avec les vitesses camion du Module 3 : classe du segment RTSS le plus proche (≤ 50 m), sinon rue
municipale près d'une municipalité, route hors RTSS au-delà, hors Québec dans une autre province ou un État.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString, Point, box

CHEMIN = Path(__file__).resolve().parents[1] / "scripts" / "temps_parcours_liaisons.py"
CRS = "EPSG:32198"
V_CLASSE = {"10": 90.0, "20": 80.0, "40": 70.0}
V_HORS_RTSS = {"MU": 40.0, "RU": 70.0, "HQ": 80.0}
RAYON_VILLE_M = 8000.0


@pytest.fixture(scope="module")
def tp():
    spec = importlib.util.spec_from_file_location("temps_parcours_liaisons", CHEMIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def monde():
    """Une autoroute (0 → 20 km) prolongée par une nationale (20 → 30 km), une ville, une « autre province »."""
    lignes = [LineString([(0, 0), (20_000, 0)]), LineString([(20_000, 0), (30_000, 0)])]
    rtss = gpd.GeoDataFrame({"cod_clasf_": ["10", "20"]}, geometry=lignes, crs=CRS)
    villes = gpd.GeoDataFrame(geometry=[Point(2_000, 600)], crs=CRS)
    ailleurs = box(200_000, -50_000, 300_000, 50_000)
    return rtss, villes, ailleurs


def temps(tp, monde, lignes: dict[int, LineString]) -> pd.DataFrame:
    rtss, villes, ailleurs = monde
    traces = gpd.GeoDataFrame({"ID_ARC": list(lignes)}, geometry=list(lignes.values()), crs=CRS)
    t = tp.tranches(traces)
    classe = tp.classer(t, rtss, ailleurs, villes, RAYON_VILLE_M)
    return tp.temps_par_liaison(t, classe, V_CLASSE, V_HORS_RTSS)


def test_un_trace_tout_en_autoroute(tp, monde):
    # 10 km à 10 m de l'autoroute : 10 / 90 h = 6,67 min
    r = temps(tp, monde, {1: LineString([(0, 10), (10_000, 10)])}).loc[1]
    assert r["temps_min"] == pytest.approx(10 / 90 * 60)
    assert r["vitesse_moy_kmh"] == pytest.approx(90)
    assert r["part_autoroute"] == pytest.approx(1)
    assert r["part_hors_rtss"] == pytest.approx(0)


def test_un_trace_sur_deux_classes(tp, monde):
    # 20 km d'autoroute à 90, puis 10 km de nationale à 80
    r = temps(tp, monde, {1: LineString([(0, 10), (30_000, 10)])}).loc[1]
    assert r["temps_min"] == pytest.approx(20 / 90 * 60 + 10 / 80 * 60)
    assert r["part_autoroute"] == pytest.approx(2 / 3)


def test_hors_rtss_selon_le_lieu(tp, monde):
    res = temps(tp, monde, {
        1: LineString([(0, 500), (4_000, 500)]),             # à 500 m du RTSS, près de la ville : rue municipale
        2: LineString([(100_000, 500), (104_000, 500)]),     # loin du RTSS et de toute ville : route hors RTSS
        3: LineString([(250_000, 0), (254_000, 0)]),         # dans une autre province : hors Québec
    })
    assert res.loc[1, "temps_min"] == pytest.approx(4 / 40 * 60)
    assert res.loc[2, "temps_min"] == pytest.approx(4 / 70 * 60)
    assert res.loc[3, "temps_min"] == pytest.approx(4 / 80 * 60)
    assert (res["part_hors_rtss"] == 1).all()
    assert res.loc[3, "part_hors_qc"] == pytest.approx(1)


def test_les_tranches_refont_le_trace(tp):
    traces = gpd.GeoDataFrame({"ID_ARC": [7]}, geometry=[LineString([(0, 0), (10_050, 0)])], crs=CRS)
    t = tp.tranches(traces)
    assert len(t) == 101
    assert t["longueur_m"].sum() == pytest.approx(10_050)
    assert t["longueur_m"].iloc[-1] == pytest.approx(50)
    assert t.geometry.iloc[-1].x == pytest.approx(10_025)            # milieu de la dernière tranche


def test_controler_refuse_un_trace_sans_temps_ou_un_temps_nul(tp, monde):
    traces = gpd.GeoDataFrame({"ID_ARC": [1, 2]}, geometry=[LineString([(0, 10), (10_000, 10)]),
                                                            LineString([(0, 10), (5_000, 10)])], crs=CRS)
    res = temps(tp, monde, {1: traces.geometry.iloc[0], 2: traces.geometry.iloc[1]})
    tp.controler(res, traces, 40, 90)                                  # cohérent : passe
    with pytest.raises(ValueError, match="sans temps"):
        tp.controler(res.drop(index=2), traces, 40, 90)
    nul = res.copy()
    nul.loc[1, "temps_min"] = 0.0
    with pytest.raises(ValueError, match="nul"):
        tp.controler(nul, traces, 40, 90)
    lent = res.copy()
    lent.loc[1, "vitesse_moy_kmh"] = 20.0
    with pytest.raises(ValueError, match="vitesses moyennes"):
        tp.controler(lent, traces, 40, 90)


def test_les_vitesses_sont_celles_du_module3(tp):
    v_classe, v_hors_rtss = tp.vitesses(tp.config_reseau())
    assert v_classe["10"] == 90 and v_classe["20"] == 80
    assert "MU" not in v_classe
    assert v_hors_rtss == {"MU": 40.0, "RU": v_classe["40"], "HQ": 80.0}
    assert np.isfinite(list(v_hors_rtss.values())).all()
