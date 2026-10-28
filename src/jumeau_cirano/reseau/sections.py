"""
sections.py
===========
Sections de comptage portées par chaque route, puis DJMA m1 à m4 par route.

Une section appartient à une route si au moins `recouvrement_min` de sa longueur
longe (à moins de `tampon_m`) les liens de cette route qui portent LE MÊME
numéro de route que la section. Le numéro de route vient du code RTSS de la
section : une section de la R-132 ne peut pas être prise par l'A-30 qui la
croise ou la longe. Les routes OSRM (une par liaison) reprennent les sections
retenues par le Volet 1.

Pseudo-code
-----------
paires section–lien : même numéro de route, section à moins du tampon du lien
                      → longueur de la section dans le tampon du lien
section ∈ route     : Σ (longueurs dans les tampons des liens de la route) ≥ recouvrement_min × longueur
djma_par_route()    : m1 moyenne, m2 pondérée par longueur, m3 par classe (Autoroute 4, Nationale 3,
                      Régionale/Collectrice 2, autres 1), m4 80e percentile par rang (valeur réelle)
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

POIDS_CLASSE = {"10": 4, "20": 3, "30": 2, "40": 2}
PERCENTILE_M4 = 0.80


def paires_section_lien(sections: gpd.GeoDataFrame, liens: gpd.GeoDataFrame, tampon_m: float) -> pd.DataFrame:
    """Longueur (m) de chaque section dans le tampon de chaque lien de même numéro de route."""
    ls, ll = shapely.STRtree(liens.geometry.to_numpy()).query(sections.geometry.to_numpy(), predicate="dwithin",
                                                               distance=tampon_m)
    meme = sections["num_route"].to_numpy()[ls] == liens["num_route"].to_numpy()[ll]
    ls, ll = ls[meme], ll[meme]
    tampons = shapely.buffer(liens.geometry.to_numpy()[ll], tampon_m)
    longueur = shapely.length(shapely.intersection(sections.geometry.to_numpy()[ls], tampons))
    return pd.DataFrame({"ide_sectn_trafc": sections["ide_sectn_trafc"].to_numpy()[ls],
                         "id_lien": liens["id_lien"].to_numpy()[ll], "longueur_m": longueur})


def rattacher_sections(paires: pd.DataFrame, routes_liens: pd.DataFrame, longueur_section: pd.Series,
                       recouvrement_min: float) -> pd.DataFrame:
    """(ID_ROUTE, ide_sectn_trafc, part) pour les sections qui longent leur route sur au moins `recouvrement_min`."""
    m = routes_liens.merge(paires, on="id_lien")
    m = m.groupby(["ID_ROUTE", "ide_sectn_trafc"], as_index=False)["longueur_m"].sum()
    m["part"] = np.minimum(m["longueur_m"] / m["ide_sectn_trafc"].map(longueur_section), 1.0)
    return m.loc[m["part"] >= recouvrement_min, ["ID_ROUTE", "ide_sectn_trafc", "part"]].reset_index(drop=True)


def djma_par_route(routes_sections: pd.DataFrame, sections: pd.DataFrame) -> pd.DataFrame:
    """DJMA, % camions et camions par jour de chaque route, par les 4 méthodes du Volet 1."""
    v = routes_sections.merge(sections[["ide_sectn_trafc", "djma", "pct_cam", "classe", "longueur_m"]],
                              on="ide_sectn_trafc").dropna(subset=["djma"])
    v["poids_classe"] = v["classe"].map(POIDS_CLASSE).fillna(1)

    def moyenne(d: pd.DataFrame, colonne: str, poids: str | None) -> float:
        d = d.dropna(subset=[colonne])
        if d.empty:
            return np.nan
        return float(np.average(d[colonne], weights=d[poids] if poids else None))

    def methodes(d: pd.DataFrame) -> pd.Series:
        r = {"n_sections": len(d)}
        for m, poids in ((1, None), (2, "longueur_m"), (3, "poids_classe")):
            r[f"djma_m{m}"], r[f"pct_cam_m{m}"] = moyenne(d, "djma", poids), moyenne(d, "pct_cam", poids)
        tri = d.sort_values("djma", kind="stable")
        rang = min(max(round(PERCENTILE_M4 * len(tri)), 1), len(tri))
        r["djma_m4"], r["pct_cam_m4"] = tri["djma"].iat[rang - 1], tri["pct_cam"].iat[rang - 1]
        return pd.Series(r)

    res = v.groupby("ID_ROUTE").apply(methodes, include_groups=False).reset_index()
    for m in (1, 2, 3, 4):
        res[f"camions_m{m}"] = res[f"djma_m{m}"] * res[f"pct_cam_m{m}"] / 100
    return res
