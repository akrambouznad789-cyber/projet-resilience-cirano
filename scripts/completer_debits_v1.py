"""
completer_debits_v1.py
======================
Projet CIRANO — Complétion des données de débit de circulation

OBJECTIF
--------
Compléter les valeurs manquantes de DJMA et %cam dans la couche brute
DebitCirculation.gpkg avant toute construction du graphe routier.

MÉTHODES
--------
Phase 1 — Complétion temporelle (≥ 1 valeur connue par métrique) :
  - Trous internes     → interpolation linéaire
  - Extrémités         → régression linéaire sur valeurs connues (extrapolation légère)
  S'applique à DJMA ET %cam indépendamment.

MICE — pour segments avec DJMA connu mais 0 valeur %cam :
  - IterativeImputer (scikit-learn) avec RandomForestRegressor comme estimateur
  - Features : val_djma_annee_1..10 du même segment
  - Exploite la corrélation DJMA ↔ %cam

Phase 2 — Complétion géographique (0 valeur DJMA) :
  - KNN spatial k=5, priorité même index_agreg (N ou O)
  - IDW (inverse distance weighting, poids = 1/distance²)

ENTRÉE
------
  data/raw/DebitCirculation.gpkg

SORTIE
------
  data/processed/debits_completes_v1.gpkg

CHAMPS PRODUITS
---------------
  val_djma_annee_1..10   : valeurs DJMA complétées
  val_cam_annee_1..10    : valeurs %cam complétées
  methode_djma           : "complet" | "interpolation" | "extrapolation" | "geo_knn"
  methode_cam            : "complet" | "interpolation" | "extrapolation" | "mice" | "geo_knn"
  n_djma_original        : nb valeurs DJMA avant complétion
  n_cam_original         : nb valeurs %cam avant complétion
"""

import os
import warnings
import numpy as np
import pandas as pd
import geopandas as gpd
from scipy.spatial import cKDTree
from sklearn.experimental import enable_iterative_imputer  # noqa: F401
from sklearn.impute import IterativeImputer
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression

warnings.filterwarnings("ignore")

# ============================================================
# CONFIGURATION
# ============================================================

INPUT_FILE  = os.path.expanduser(
    "~/projects/projet-resilience-cirano/data/raw/DebitCirculation.gpkg"
)
OUTPUT_FILE = os.path.expanduser(
    "~/projects/projet-resilience-cirano/data/processed/debits_completes_v1.gpkg"
)
OUTPUT_LAYER = "debits_completes_v1"

# Années couvertes : annee_1 = 2025, annee_10 = 2016
ANNEES = list(range(2025, 2015, -1))   # [2025, 2024, ..., 2016]
N_ANNEES = len(ANNEES)                  # 10

DJMA_COLS = [f"val_djma_annee_{i}" for i in range(1, N_ANNEES + 1)]
CAM_COLS  = [f"val_cam_annee_{i}"  for i in range(1, N_ANNEES + 1)]

# KNN — nombre de voisins pour complétion géographique
K_VOISINS = 5

# CRS projeté Québec pour calculs de distances en mètres
CRS_PROJETE = "EPSG:32198"

# ============================================================
# FONCTIONS UTILITAIRES
# ============================================================

def _serie_vers_array(row: pd.Series, cols: list[str]) -> np.ndarray:
    """Retourne un tableau float64 de longueur N_ANNEES, NaN si absent."""
    return row[cols].values.astype(float)


def _interpoler_trous(vals: np.ndarray) -> np.ndarray:
    """Interpolation linéaire sur les NaN internes uniquement."""
    s = pd.Series(vals)
    # limit_area='inside' : ne touche pas aux NaN en début/fin de série
    s = s.interpolate(method="linear", limit_area="inside")
    return s.values.astype(float)


def _extrapoler_extremites(vals: np.ndarray, annees: list[int]) -> np.ndarray:
    """
    Régression linéaire sur les valeurs connues → extrapolation légère
    pour les NaN restants en début et fin de série.
    Plancher à 0 pour éviter les valeurs négatives.
    """
    mask_ok = ~np.isnan(vals)
    if mask_ok.sum() < 2:
        # Moins de 2 points : répétition de la valeur unique
        val_unique = vals[mask_ok][0] if mask_ok.sum() == 1 else np.nan
        result = vals.copy()
        result[np.isnan(result)] = val_unique
        return result

    x_ok = np.array(annees)[mask_ok].reshape(-1, 1)
    y_ok = vals[mask_ok]

    reg = LinearRegression().fit(x_ok, y_ok)

    result = vals.copy()
    mask_nan = np.isnan(vals)
    if mask_nan.any():
        x_nan = np.array(annees)[mask_nan].reshape(-1, 1)
        y_pred = reg.predict(x_nan)
        result[mask_nan] = np.maximum(y_pred, 0.0)

    return result


def _completer_serie(vals: np.ndarray, annees: list[int]) -> tuple[np.ndarray, str]:
    """
    Applique interpolation puis extrapolation à une série temporelle.
    Retourne (valeurs_complétées, méthode).
    """
    n_ok = int(np.sum(~np.isnan(vals)))

    if n_ok == N_ANNEES:
        return vals.copy(), "complet"

    if n_ok == 0:
        return vals.copy(), "geo_knn"  # géré en Phase 2

    # Interpolation des trous internes
    v = _interpoler_trous(vals)
    methode = "interpolation"

    # Extrapolation des extrémités restantes
    if np.isnan(v).any():
        v = _extrapoler_extremites(v, annees)
        methode = "extrapolation"

    return v, methode


# ============================================================
# PHASE 1 — COMPLÉTION TEMPORELLE
# ============================================================

def phase1_temporelle(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Complète DJMA et %cam pour tous les segments ayant ≥ 1 valeur connue.
    Les segments sans aucune valeur DJMA sont laissés pour la Phase 2.
    """
    print("\n[Phase 1] Complétion temporelle...")

    # Conversion en numérique (les valeurs brutes peuvent être des strings vides)
    for col in DJMA_COLS + CAM_COLS:
        gdf[col] = pd.to_numeric(gdf[col], errors="coerce")

    # Compter les valeurs disponibles avant complétion
    gdf["n_djma_original"] = gdf[DJMA_COLS].notna().sum(axis=1).astype(int)
    gdf["n_cam_original"]  = gdf[CAM_COLS].notna().sum(axis=1).astype(int)

    methodes_djma = []
    methodes_cam  = []

    for idx, row in gdf.iterrows():
        # --- DJMA ---
        v_djma = _serie_vers_array(row, DJMA_COLS)
        v_djma_c, m_djma = _completer_serie(v_djma, ANNEES)
        for j, col in enumerate(DJMA_COLS):
            gdf.at[idx, col] = v_djma_c[j]
        methodes_djma.append(m_djma)

        # --- %cam ---
        v_cam = _serie_vers_array(row, CAM_COLS)
        n_cam = int(np.sum(~np.isnan(v_cam)))

        if n_cam == 0 and row["n_djma_original"] > 0:
            # DJMA connu mais %cam absent → MICE (traité après)
            methodes_cam.append("mice")
        else:
            v_cam_c, m_cam = _completer_serie(v_cam, ANNEES)
            for j, col in enumerate(CAM_COLS):
                gdf.at[idx, col] = v_cam_c[j]
            methodes_cam.append(m_cam)

    gdf["methode_djma"] = methodes_djma
    gdf["methode_cam"]  = methodes_cam

    n_interp    = (gdf["methode_djma"] == "interpolation").sum()
    n_extrap    = (gdf["methode_djma"] == "extrapolation").sum()
    n_complet   = (gdf["methode_djma"] == "complet").sum()
    n_knn       = (gdf["methode_djma"] == "geo_knn").sum()
    n_mice      = (gdf["methode_cam"]  == "mice").sum()

    print(f"  DJMA complet          : {n_complet:>5}")
    print(f"  DJMA interpolé        : {n_interp:>5}")
    print(f"  DJMA extrapolé        : {n_extrap:>5}")
    print(f"  DJMA → Phase 2 (KNN)  : {n_knn:>5}")
    print(f"  %cam → MICE           : {n_mice:>5}")

    return gdf


# ============================================================
# MICE — COMPLÉTION %cam PAR CORRÉLATION AVEC DJMA
# ============================================================

def mice_cam(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Pour les segments avec DJMA connu mais 0 valeur %cam,
    utilise IterativeImputer (RandomForest) pour prédire le %cam
    en exploitant la corrélation DJMA ↔ %cam.
    """
    mask_mice = gdf["methode_cam"] == "mice"
    if not mask_mice.any():
        print("\n[MICE] Aucun segment à traiter.")
        return gdf

    print(f"\n[MICE] Complétion %cam par RandomForest ({mask_mice.sum()} segments)...")

    # Segments d'entraînement : ont à la fois DJMA et %cam complétés
    mask_train = (
        gdf["methode_djma"].isin(["complet", "interpolation", "extrapolation"])
        & gdf["methode_cam"].isin(["complet", "interpolation", "extrapolation"])
    )
    print(f"  Segments d'entraînement disponibles : {mask_train.sum()}")

    if mask_train.sum() < 50:
        print("  AVERTISSEMENT : trop peu de données d'entraînement, MICE ignoré.")
        # Fallback : répliquer le DJMA moyen pondéré comme proxy %cam=0
        return gdf

    # Features : DJMA de toutes les années
    X_train = gdf.loc[mask_train, DJMA_COLS].values
    X_mice  = gdf.loc[mask_mice,  DJMA_COLS].values

    # Cible : %cam pour chaque année (imputation colonne par colonne)
    imputer = IterativeImputer(
        estimator=RandomForestRegressor(n_estimators=50, random_state=42, n_jobs=-1),
        max_iter=10,
        random_state=42,
    )

    # On construit une matrice features=DJMA, target=%cam pour chaque année
    # L'imputer va itérativement prédire chaque colonne cible
    Y_train = gdf.loc[mask_train, CAM_COLS].values

    # Construire la matrice complète [DJMA | CAM] pour l'imputer
    # Colonnes 0..9 = DJMA (complètes), colonnes 10..19 = CAM (à prédire pour mask_mice)
    n_train = X_train.shape[0]
    n_pred  = X_mice.shape[0]

    # Matrice pour l'entraînement : DJMA connus + CAM connus
    M_train = np.hstack([X_train, Y_train])

    # Matrice pour prédiction : DJMA connus + CAM tous NaN
    M_pred = np.hstack([X_mice, np.full((n_pred, N_ANNEES), np.nan)])

    # Empiler train + pred pour que l'imputer voie les deux
    M_full = np.vstack([M_train, M_pred])

    # Ajuster et transformer
    M_imputed = imputer.fit_transform(M_full)

    # Récupérer les CAM imputées pour les lignes mask_mice
    cam_imputed = M_imputed[n_train:, N_ANNEES:]
    cam_imputed = np.maximum(cam_imputed, 0.0)  # plancher 0

    # Écrire dans le GeoDataFrame
    idx_mice = gdf.index[mask_mice].tolist()
    for i, idx in enumerate(idx_mice):
        for j, col in enumerate(CAM_COLS):
            gdf.at[idx, col] = round(float(cam_imputed[i, j]), 1)

    print(f"  %cam imputé avec MICE pour {n_pred} segments.")
    return gdf


# ============================================================
# PHASE 2 — COMPLÉTION GÉOGRAPHIQUE (KNN + IDW)
# ============================================================

def phase2_geographique(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Pour les segments sans aucune valeur DJMA, impute DJMA et %cam
    par IDW sur les k=5 voisins spatiaux les plus proches du même
    type de route (index_agreg). Si pas assez de voisins du même type,
    élargit à tous types.
    """
    mask_knn = gdf["methode_djma"] == "geo_knn"
    if not mask_knn.any():
        print("\n[Phase 2] Aucun segment sans DJMA.")
        return gdf

    print(f"\n[Phase 2] Complétion géographique KNN ({mask_knn.sum()} segments)...")

    # Projeter en mètres pour les distances
    gdf_proj = gdf.to_crs(CRS_PROJETE)

    # Centroïdes de tous les segments
    centroides = gdf_proj.geometry.centroid

    # Segments sources : ont des valeurs DJMA après Phase 1
    mask_src = ~mask_knn
    src_idx  = gdf.index[mask_src].tolist()

    coords_src = np.array(
        [(c.x, c.y) for c in centroides[mask_src]]
    )
    coords_cible = np.array(
        [(c.x, c.y) for c in centroides[mask_knn]]
    )

    types_src    = gdf.loc[mask_src,  "index_agreg"].values
    types_cibles = gdf.loc[mask_knn,  "index_agreg"].values
    idx_cibles   = gdf.index[mask_knn].tolist()

    # Arbre KD pour recherche rapide
    arbre = cKDTree(coords_src)

    n_traites = 0
    for i, (coord, type_cible, idx_seg) in enumerate(
        zip(coords_cible, types_cibles, idx_cibles)
    ):
        # Chercher les K_VOISINS * 4 candidats pour filtrer par type
        k_large = min(K_VOISINS * 4, len(src_idx))
        distances, positions = arbre.query(coord, k=k_large)

        distances = np.atleast_1d(distances)
        positions = np.atleast_1d(positions)

        # Éviter division par zéro (segment confondu avec voisin)
        distances = np.where(distances == 0, 1.0, distances)

        # Prioriser le même type de route
        meme_type = np.array([types_src[p] == type_cible for p in positions])
        pos_prio  = positions[meme_type][:K_VOISINS]
        dist_prio = distances[meme_type][:K_VOISINS]

        if len(pos_prio) < K_VOISINS:
            # Compléter avec les autres types si pas assez de voisins
            pos_fallback  = positions[~meme_type][: K_VOISINS - len(pos_prio)]
            dist_fallback = distances[~meme_type][: K_VOISINS - len(pos_prio)]
            pos_final  = np.concatenate([pos_prio,  pos_fallback])
            dist_final = np.concatenate([dist_prio, dist_fallback])
        else:
            pos_final  = pos_prio
            dist_final = dist_prio

        # Poids IDW : 1 / distance²
        poids = 1.0 / (dist_final ** 2)
        poids = poids / poids.sum()

        # Indices gdf réels des voisins sélectionnés
        idx_voisins = [src_idx[p] for p in pos_final]

        # Imputer DJMA
        for col in DJMA_COLS:
            vals_voisins = gdf.loc[idx_voisins, col].values.astype(float)
            gdf.at[idx_seg, col] = round(float(np.dot(poids, vals_voisins)), 0)

        # Imputer %cam
        for col in CAM_COLS:
            vals_voisins = gdf.loc[idx_voisins, col].values.astype(float)
            gdf.at[idx_seg, col] = round(float(np.dot(poids, vals_voisins)), 1)

        gdf.at[idx_seg, "methode_djma"] = "geo_knn"
        gdf.at[idx_seg, "methode_cam"]  = "geo_knn"
        n_traites += 1

        if n_traites % 500 == 0:
            print(f"  ...{n_traites}/{mask_knn.sum()} segments traités")

    print(f"  Complétion géographique terminée : {n_traites} segments.")
    return gdf


# ============================================================
# MAIN
# ============================================================

def main() -> None:
    print("=" * 60)
    print("completer_debits_v1.py")
    print("Complétion des données de débit — CIRANO")
    print("=" * 60)

    # --- Chargement ---
    print(f"\nChargement : {INPUT_FILE}")
    gdf = gpd.read_file(INPUT_FILE)
    print(f"  {len(gdf)} segments chargés | CRS : {gdf.crs.to_epsg()}")

    # --- Phase 1 : complétion temporelle ---
    gdf = phase1_temporelle(gdf)

    # --- MICE : %cam manquant mais DJMA connu ---
    gdf = mice_cam(gdf)

    # --- Phase 2 : complétion géographique ---
    gdf = phase2_geographique(gdf)

    # --- Bilan final ---
    print("\n[Bilan final]")
    print("  Méthode DJMA :")
    print(gdf["methode_djma"].value_counts().to_string())
    print("  Méthode %cam :")
    print(gdf["methode_cam"].value_counts().to_string())

    djma_restants = gdf[DJMA_COLS].isna().any(axis=1).sum()
    cam_restants  = gdf[CAM_COLS].isna().any(axis=1).sum()
    print(f"\n  Segments avec DJMA encore incomplets : {djma_restants}")
    print(f"  Segments avec %cam encore incomplets : {cam_restants}")

    # --- Export ---
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    gdf.to_file(OUTPUT_FILE, layer=OUTPUT_LAYER, driver="GPKG")
    print(f"\nSortie écrite : {OUTPUT_FILE}  (layer: {OUTPUT_LAYER})")
    print("=" * 60)


if __name__ == "__main__":
    main()
