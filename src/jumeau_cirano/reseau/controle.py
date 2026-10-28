"""
controle.py
===========
Point de contrôle du graphe RTSS, liaison par liaison, contre la route OSRM actuelle.

Deux questions distinctes :

1. Topologie (réussi / échoué) : le réseau permet-il d'aller de A à B sans détour
   aberrant ? On compare le plus court chemin EN KM sur le RTSS au tracé OSRM :
   ratio_km = km RTSS / km OSRM. Mesuré en km, ce test ne dépend d'aucune
   hypothèse de vitesse. Il porte sur les liaisons interurbaines (tracé OSRM ≥
   `seuil_interurbain_km`) : le RTSS ne contient pas les rues municipales, si
   bien que les liaisons urbaines courtes sont rapportées à part.
2. Accord de route (information) : le chemin le plus RAPIDE suit-il la route
   d'OSRM ? recouvrement = part de ce chemin à moins de `tampon_recouvrement_m`
   du tracé OSRM. Il dépend des vitesses supposées ; c'est le modèle calé et ses
   routes alternatives qui traiteront le choix de route.

Les liaisons dont le tracé sort du Québec (`hors_quebec`) ne sont pas comptées.
"""

from __future__ import annotations

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
import shapely

from jumeau_cirano.config import ParamControle
from jumeau_cirano.reseau.rtss import poids_sans_transit


def aretes_du_chemin(g: nx.MultiGraph, noeuds: list, poids: str) -> list[tuple]:
    """Arêtes (u, v, clé) d'un chemin de nœuds : entre deux nœuds, l'arête parallèle la moins chère."""
    aretes = []
    for u, v in zip(noeuds[:-1], noeuds[1:], strict=True):
        cle = min(g[u][v], key=lambda k: g[u][v][k][poids])
        aretes.append((u, v, cle))
    return aretes


def geometrie(aretes: list[tuple], geom_lien: pd.Series) -> shapely.Geometry:
    """Géométrie d'un chemin : union des liens RTSS empruntés (indexés par id_lien), sans les accès."""
    return shapely.union_all(geom_lien.loc[[k for _, _, k in aretes if not isinstance(k, str)]].to_numpy())


def recouvrement(chemin: shapely.Geometry, reference: shapely.Geometry, tampon_m: float) -> float:
    """Part de la longueur de `chemin` à moins de `tampon_m` de `reference`."""
    if chemin.length == 0:
        return 0.0
    return float(shapely.intersection(chemin, shapely.buffer(reference, tampon_m)).length / chemin.length)


def controler(
    g: nx.MultiGraph,
    liens: gpd.GeoDataFrame,
    liaisons: pd.DataFrame,
    traces: gpd.GeoSeries,
    p: ParamControle,
) -> gpd.GeoDataFrame:
    """Une ligne par liaison : km OSRM, km du plus court chemin RTSS, ratio_km, chemin le plus
    rapide (minutes, géométrie, recouvrement) et verdicts.

    `liaisons` : ID_ARC, statut, noeud_a, noeud_b (nœuds d'accès du graphe RTSS) ;
    `traces` : tracé OSRM indexé par ID_ARC (même CRS que `liens`).
    """
    geom_lien = liens.set_index("id_lien").geometry
    lignes = []
    for r in liaisons.itertuples():
        trace = traces.get(r.ID_ARC)
        km_osrm = trace.length / 1000 if trace is not None else np.nan
        ligne = {"ID_ARC": r.ID_ARC, "statut": r.statut, "km_osrm": km_osrm,
                 "type": "interurbaine" if km_osrm >= p.seuil_interurbain_km else "urbaine",
                 "km_rtss": np.nan, "ratio_km": np.nan, "km_rapide": np.nan, "minutes_rapide": np.nan,
                 "recouvrement": np.nan, "topologie": "sans_chemin", "accord_route": False}
        par_km = poids_sans_transit(r.noeud_a, r.noeud_b, "km")
        par_minutes = poids_sans_transit(r.noeud_a, r.noeud_b, "minutes")
        try:
            km = nx.shortest_path_length(g, r.noeud_a, r.noeud_b, weight=par_km)
            noeuds = nx.shortest_path(g, r.noeud_a, r.noeud_b, weight=par_minutes)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            lignes.append({**ligne, "geometry": None})
            continue
        aretes = aretes_du_chemin(g, noeuds, "minutes")
        chemin = geometrie(aretes, geom_lien)
        ligne["km_rtss"] = km
        ligne["km_rapide"] = sum(g.edges[a]["km"] for a in aretes)
        ligne["minutes_rapide"] = sum(g.edges[a]["minutes"] for a in aretes)
        if km_osrm > 0:
            ligne["ratio_km"] = km / km_osrm
            ligne["topologie"] = "ok" if ligne["ratio_km"] <= p.ratio_longueur_max else "detour"
            ligne["recouvrement"] = recouvrement(chemin, trace, p.tampon_recouvrement_m)
            ligne["accord_route"] = bool(ligne["recouvrement"] >= p.recouvrement_min)
        lignes.append({**ligne, "geometry": chemin})
    return gpd.GeoDataFrame(lignes, geometry="geometry", crs=liens.crs)


def synthese(resultat: pd.DataFrame, p: ParamControle) -> dict:
    """Chiffres du contrôle, hors liaisons qui sortent du Québec."""
    qc = resultat[resultat["statut"] != "hors_quebec"]
    inter, urb = qc[qc["type"] == "interurbaine"], qc[qc["type"] == "urbaine"]

    def part(d: pd.DataFrame) -> float:
        return float((d["topologie"] == "ok").mean()) if len(d) else 0.0

    return {
        "liaisons": int(len(qc)),
        "interurbaines": int(len(inter)),
        "ok_interurbaines": int((inter["topologie"] == "ok").sum()),
        "part_ok_interurbaines": part(inter),
        "urbaines": int(len(urb)),
        "part_ok_urbaines": part(urb),
        "sans_chemin": int((qc["topologie"] == "sans_chemin").sum()),
        "ratio_km_median": float(inter["ratio_km"].median()),
        "accord_route": float(qc["accord_route"].mean()) if len(qc) else 0.0,
        "recouvrement_median": float(qc["recouvrement"].median()),
        "passe": part(inter) >= p.part_liaisons_min,
    }
