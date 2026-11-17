"""
blocs.py
========
Blocs spatiaux de 50 km : l'unité de découpage de toutes les validations du projet.

Deux sections voisines se ressemblent. Pour mesurer ce qu'un modèle sait prédire
ailleurs, des carrés entiers sont mis de côté (GroupKFold). La définition doit
être la même partout, sinon deux expériences ne comparent pas la même chose :
c'est celle de scripts/validation_spatiale_randomforest.py, reprise à la lettre.

    bloc = « ⌊x / 50 km⌋_⌊y / 50 km⌋ » du centroïde de la géométrie, en EPSG:32198
"""

from __future__ import annotations

import warnings

import geopandas as gpd
import numpy as np

CRS_BLOCS = "EPSG:32198"
BLOC_KM = 50.0


def centroides(geometries: gpd.GeoSeries) -> tuple[np.ndarray, np.ndarray]:
    """Coordonnées (x, y) en mètres, EPSG:32198, du centroïde de chaque géométrie."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")        # centroïde d'une géométrie en degrés : reprojetée juste avant
        centre = geometries.to_crs(CRS_BLOCS).centroid
    return centre.x.to_numpy(), centre.y.to_numpy()


def bloc_de(x: np.ndarray, y: np.ndarray, bloc_km: float = BLOC_KM) -> np.ndarray:
    """Identifiant de bloc de points en mètres (EPSG:32198) : « ⌊x / côté⌋_⌊y / côté⌋ »."""
    cote = bloc_km * 1000
    ix = np.floor(np.asarray(x) / cote).astype(int).astype(str)
    iy = np.floor(np.asarray(y) / cote).astype(int).astype(str)
    return np.char.add(np.char.add(ix, "_"), iy).astype(object)


def bloc_50km(geometries: gpd.GeoSeries, bloc_km: float = BLOC_KM) -> np.ndarray:
    """Identifiant du bloc de chaque géométrie (lignes, points), quel que soit son système de coordonnées."""
    return bloc_de(*centroides(geometries), bloc_km)
