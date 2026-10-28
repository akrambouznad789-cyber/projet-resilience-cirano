"""
liaisons.py
===========
Graphe des liaisons du Volet 1 : 307 liaisons entre 229 nœuds (villes, nœuds
intermédiaires, points frontière), avec le statut DJMA de chaque liaison.

Le fichier source n'a pas de système de coordonnées déclaré (les coordonnées
sont en EPSG:32198) et certains noms sont mal encodés (« MontrÃ©al ») : les
deux sont corrigés ici, une seule fois.
"""

from __future__ import annotations

import geopandas as gpd
import pandas as pd

from jumeau_cirano.config import ConfigReseau, chemin_absolu


def reparer_nom(texte: object) -> object:
    """Corrige un nom encodé deux fois (UTF-8 lu en Latin-1) ; laisse le reste intact."""
    if not isinstance(texte, str):
        return texte
    try:
        return texte.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return texte


def charger_noeuds(cfg: ConfigReseau) -> gpd.GeoDataFrame:
    """Nœuds du graphe des liaisons : ID, NOM, TYPE, géométrie (EPSG:32198)."""
    noeuds = gpd.read_file(chemin_absolu(cfg.entrees.liaisons), layer="noeuds")
    noeuds = noeuds.set_crs(cfg.crs, allow_override=True)
    noeuds["NOM"] = noeuds["NOM"].map(reparer_nom)
    return noeuds[["ID", "NOM", "TYPE", "geometry"]]


def charger_liaisons(cfg: ConfigReseau) -> pd.DataFrame:
    """Liaisons : ID_ARC, ID_A, ID_B, VILLE_A, VILLE_B, statut DJMA (ok, aucun_djma, hors_quebec)."""
    arcs = gpd.read_file(chemin_absolu(cfg.entrees.djma), ignore_geometry=True,
                         columns=["ID_ARC", "ID_A", "ID_B", "VILLE_A", "VILLE_B", "statut"])
    for c in ("VILLE_A", "VILLE_B"):
        arcs[c] = arcs[c].map(reparer_nom)
    return arcs


def charger_traces_osrm(cfg: ConfigReseau) -> gpd.GeoSeries:
    """Tracé OSRM actuel de chaque liaison (une route par liaison), indexé par ID_ARC."""
    traces = gpd.read_file(chemin_absolu(cfg.entrees.traces_osrm), layer="trace_osrm")
    traces = traces.drop_duplicates("ID_ARC").set_index("ID_ARC")
    return traces.to_crs(cfg.crs).geometry
