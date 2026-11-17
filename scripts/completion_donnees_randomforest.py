"""
completion_donnees_randomforest.py
===================================
Projet CIRANO — Complétion des données de débit de circulation

DIFFÉRENCE PAR RAPPORT À V1
-----------------------------
V1 complète uniquement les colonnes numériques structurées (val_djma_annee_X,
val_cam_annee_X). Les champs texte originaux (annee_en_cours à annee10) restaient
inchangés, ce qui rendait la validation visuelle dans QGIS impossible.

V2 reconstruit également les champs texte annee_en_cours à annee10 avec les
valeurs DJMA et %cam complétées, en conservant DJME et DJMH tels quels.
Format maintenu : "YYYY DJMA:XXXX / DJME:XXXX / DJMH:XXXX / %cam:XX.X"

ÉVOLUTION PAR RAPPORT À V2
---------------------------
Extrapolation aux extrémités : une régression globale unique sur toute la plage
connue pouvait imposer la même pente aux deux bords même quand leurs tendances
locales divergent. Remplacée par une régression LOCALE par bord (fenêtre des
FENETRE_GRADIENT points connus les plus proches), pour que chaque bord prolonge
son propre gradient plutôt que celui de l'autre.

MICE enrichi : le RandomForestRegressor n'utilisait que le profil DJMA comme
prédicteur (R² = 0,037, quasi aucun signal). Ajout de features supplémentaires
(construire_features_supplementaires) : débits de pointe DJME/DJMH, profil
horaire (30e heure), type de route (jointure spatiale la plus proche avec
ReseauRoutier_RTSS) et localisation géographique — R² = 0,306 en validation
croisée. Ces features restent internes au modèle, DJME/DJMH ne sont pas
modifiés dans la sortie.

BORNAGE DE L'EXTRAPOLATION (2026-11-17)
---------------------------------------
Prolongée sur jusqu'à 8 ans à partir de 2 ou 3 années connues, la droite du
gradient local pouvait tomber à 0 (53 sections recevaient un % de camions de 0,
7 un DJMA de 0, alors que le MTQ y avait mesuré des valeurs positives) ou
s'envoler au-dessus de tout ce qui avait été mesuré. Une valeur extrapolée est
désormais bornée à l'intervalle [min, max] des valeurs mesurées sur la section :
la tendance locale est gardée tant qu'elle reste dans cet intervalle. L'ancienne
règle (plancher à 0 seulement) reste disponible : _extrapoler_extremites_detail(
..., borner=False). controler_completion() vérifie ces règles avant l'écriture.

Le même jour : une section sans DJMA publié mais avec un % de camions publié
(455 sections) garde sa propre série de % camions ; la phase 2 n'emprunte plus
aux voisines que son DJMA (avant : 788 valeurs publiées remplacées).

MÉTHODES
---------------------------
Phase 1 — Complétion temporelle (≥ 1 valeur connue par métrique) :
  - Trous internes     → interpolation linéaire
  - Extrémités         → régression linéaire locale par bord (gradient propre à chaque bord),
                         bornée à l'intervalle des valeurs mesurées sur la section
  S'applique à DJMA ET %cam indépendamment.

MICE — pour segments avec DJMA connu mais 0 valeur %cam :
  - IterativeImputer (scikit-learn) avec RandomForestRegressor
  - Features : DJMA + DJME/DJMH + 30e heure + type de route (RTSS) + centroïde

Phase 2 — Complétion géographique (0 valeur DJMA) :
  - KNN spatial k=5, priorité même index_agreg (N ou O)
  - IDW (inverse distance weighting, poids = 1/distance²)
  - %cam emprunté aux voisines seulement si la section n'a aucun %cam publié

ENTRÉE
------
  data/raw/DebitCirculation.gpkg

SORTIE
------
  data/processed/debits_completes.gpkg
  --echantillon : sections de l'emprise de l'échantillon (configs/reseau.yaml)
                  → outputs/module1/echantillon/debits_completes.gpkg

CHAMPS PRODUITS
---------------
  annee_en_cours .. annee10  : champs texte reconstruits avec DJMA et %cam complétés
  val_djma_annee_1..10       : valeurs DJMA complétées (numériques)
  val_cam_annee_1..10        : valeurs %cam complétées (numériques)
  methode_djma               : "complet" | "interpolation" | "extrapolation" | "geo_knn"
  methode_cam                : "complet" | "interpolation" | "extrapolation" | "mice" | "geo_knn"
  n_djma_original            : nb valeurs DJMA avant complétion
  n_cam_original             : nb valeurs %cam avant complétion
  n_bornees_djma             : nb années DJMA extrapolées ramenées dans l'intervalle mesuré
  n_bornees_cam              : nb années %cam extrapolées ramenées dans l'intervalle mesuré
"""

import re
import warnings
from pathlib import Path

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

RACINE      = Path(__file__).resolve().parent.parent
INPUT_FILE  = RACINE / "data" / "raw" / "DebitCirculation.gpkg"
RTSS_FILE   = RACINE / "data" / "raw" / "ReseauRoutier_RTSS.gpkg"
OUTPUT_FILE = RACINE / "data" / "processed" / "debits_completes.gpkg"
OUTPUT_LAYER = "debits_completes"
SORTIE_ECHANTILLON    = RACINE / "outputs" / "module1" / "echantillon" / "debits_completes.gpkg"
FICHIER_CONFIG_RESEAU = RACINE / "configs" / "reseau.yaml"   # emprise de l'échantillon (clé « echantillon »)

# Années couvertes : annee_1 = 2025, annee_10 = 2016
ANNEES   = list(range(2025, 2015, -1))   # [2025, 2024, ..., 2016]
N_ANNEES = len(ANNEES)                    # 10

DJMA_COLS = [f"val_djma_annee_{i}" for i in range(1, N_ANNEES + 1)]
CAM_COLS  = [f"val_cam_annee_{i}"  for i in range(1, N_ANNEES + 1)]

# Features supplémentaires pour le RandomForest de mice_cam (au-delà du seul DJMA) :
# débits de pointe, profil horaire, type de route et localisation géographique.
DJME_COLS = [f"val_djme_annee_{i}" for i in range(1, N_ANNEES + 1)]
DJMH_COLS = [f"val_djmh_annee_{i}" for i in range(1, N_ANNEES + 1)]
COL_30E   = "val_30e_heure"

CATEGORIES_ROUTE = ["Autoroute", "Nationale", "Régionale", "Collectrice", "Autre"]
ROUTE_DUMMY_COLS = [f"route_{c}" for c in CATEGORIES_ROUTE]
COLS_CENTROIDE   = ["x_centroide", "y_centroide"]

FEATURES_SUPPLEMENTAIRES = DJME_COLS + DJMH_COLS + [COL_30E] + ROUTE_DUMMY_COLS + COLS_CENTROIDE

# Champs texte à reconstruire : annee_en_cours (idx 1) + annee2..annee10 (idx 2..10)
ANNEE_CHAMPS = ["annee_en_cours"] + [f"annee{i}" for i in range(2, N_ANNEES + 1)]

K_VOISINS   = 5
CRS_PROJETE = "EPSG:32198"

FENETRE_GRADIENT = 3   # nb de points connus utilisés pour la pente locale à chaque bord


# ============================================================
# FONCTIONS UTILITAIRES — COMPLÉTION NUMÉRIQUE
# ============================================================

def _serie_vers_array(row: pd.Series, cols: list[str]) -> np.ndarray:
    return row[cols].values.astype(float)


def _interpoler_trous(vals: np.ndarray) -> np.ndarray:
    s = pd.Series(vals)
    s = s.interpolate(method="linear", limit_area="inside")
    return s.values.astype(float)


def _extrapoler_extremites_detail(vals: np.ndarray, annees: list[int],
                                  borner: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """
    Extrapole les valeurs manquantes aux extrémités temporelles.

    Chaque bord prolonge son propre gradient LOCAL (régression sur les
    FENETRE_GRADIENT points connus les plus proches de ce bord), plutôt
    qu'une régression unique sur toute la plage connue : si la tendance
    diffère entre le début et la fin de la plage connue, un bord ne doit
    pas hériter du gradient de l'autre.

    borner=True (règle en vigueur) : chaque valeur extrapolée est ramenée dans
    l'intervalle [min, max] des valeurs connues de la section. Prolongée sur
    plusieurs années à partir de 2 ou 3 points, une droite peut sinon tomber à 0
    ou s'envoler. borner=False reproduit l'ancienne règle (plancher à 0 seul).

    Retourne la série complétée et le masque des années bornées (même ordre que
    `annees`).
    """
    mask_ok = ~np.isnan(vals)
    bornees = np.zeros(len(vals), dtype=bool)
    if mask_ok.sum() < 2:
        val_unique = vals[mask_ok][0] if mask_ok.sum() == 1 else np.nan
        result = vals.copy()
        result[np.isnan(result)] = val_unique
        return result, bornees

    annees_arr = np.array(annees)
    ordre      = np.argsort(annees_arr)      # ordre chronologique croissant
    annees_c   = annees_arr[ordre]
    vals_c     = vals[ordre]
    ok_c       = mask_ok[ordre]
    bas, haut  = float(np.nanmin(vals_c)), float(np.nanmax(vals_c))

    idx_connus = np.where(ok_c)[0]
    i_premier, i_dernier = idx_connus[0], idx_connus[-1]
    result_c  = vals_c.copy()
    bornees_c = np.zeros(len(vals_c), dtype=bool)

    def prolonger(fenetre: np.ndarray, manquants: np.ndarray) -> None:
        reg  = LinearRegression().fit(annees_c[fenetre].reshape(-1, 1), vals_c[fenetre])
        pred = reg.predict(annees_c[manquants].reshape(-1, 1))
        if borner:
            bornees_c[manquants] = (pred < bas) | (pred > haut)
            result_c[manquants]  = np.clip(pred, bas, haut)
        else:
            result_c[manquants]  = np.maximum(pred, 0.0)

    if i_premier > 0:
        prolonger(idx_connus[:FENETRE_GRADIENT], np.arange(0, i_premier))                # gradient juste après le trou
    if i_dernier < len(vals_c) - 1:
        prolonger(idx_connus[-FENETRE_GRADIENT:], np.arange(i_dernier + 1, len(vals_c)))  # gradient juste avant le trou

    result = np.empty_like(vals_c)
    result[ordre]  = result_c
    bornees[ordre] = bornees_c
    return result, bornees


def _extrapoler_extremites(vals: np.ndarray, annees: list[int]) -> np.ndarray:
    """Série complétée aux extrémités par le gradient local borné (voir _extrapoler_extremites_detail)."""
    return _extrapoler_extremites_detail(vals, annees)[0]


def _completer_serie(vals: np.ndarray, annees: list[int]) -> tuple[np.ndarray, str, int]:
    """Complétion temporelle d'une série : (valeurs, méthode, nombre d'années bornées)."""
    n_ok = int(np.sum(~np.isnan(vals)))
    if n_ok == N_ANNEES:
        return vals.copy(), "complet", 0
    if n_ok == 0:
        return vals.copy(), "geo_knn", 0

    v       = _interpoler_trous(vals)
    methode = "interpolation"
    n_bornees = 0
    if np.isnan(v).any():
        v, bornees = _extrapoler_extremites_detail(v, annees)
        methode    = "extrapolation"
        n_bornees  = int(bornees.sum())

    return v, methode, n_bornees


# ============================================================
# RECONSTRUCTION DES CHAMPS TEXTE — NOUVEAUTÉ V2
# ============================================================

def _extraire_composante(chaine: str, cle: str) -> str:
    """Extrait la valeur après 'CLE:' jusqu'au prochain ' /' ou fin de chaîne."""
    if not chaine:
        return ""
    m = re.search(rf"{re.escape(cle)}:([^/]*)", chaine)
    if not m:
        return ""
    return m.group(1).strip()


def _reconstruire_chaine(chaine_orig: str,
                          djma_val: float,
                          cam_val: float,
                          est_annee1: bool) -> str:
    """
    Reconstruit la chaîne texte annee_X avec DJMA et %cam complétés.
    DJME, DJMH et 30e h sont conservés tels quels depuis la chaîne originale.
    """
    chaine = chaine_orig or ""

    annee_m = re.match(r"^(\d{4})", chaine)
    annee   = annee_m.group(1) if annee_m else ""

    djme = _extraire_composante(chaine, "DJME")
    djmh = _extraire_composante(chaine, "DJMH")

    djma_str = str(int(round(djma_val))) if not np.isnan(djma_val) else ""
    cam_str  = str(round(cam_val, 1))    if not np.isnan(cam_val)  else ""

    if est_annee1:
        trente = _extraire_composante(chaine, "30e h")
        return (f"{annee} DJMA:{djma_str} / DJME:{djme} / DJMH:{djmh}"
                f" / %cam:{cam_str} / 30e h:{trente}")
    else:
        return (f"{annee} DJMA:{djma_str} / DJME:{djme} / DJMH:{djmh}"
                f" / %cam:{cam_str}")


def reconstruire_champs_texte(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Met à jour les champs annee_en_cours à annee10 avec les valeurs
    DJMA et %cam complétées. DJME et DJMH sont conservés de la source.
    """
    print("\n[Reconstruction texte] Mise à jour des champs annee_X...")

    for i, champ in enumerate(ANNEE_CHAMPS, start=1):
        col_djma = f"val_djma_annee_{i}"
        col_cam  = f"val_cam_annee_{i}"
        est_1    = (i == 1)

        nouvelles_valeurs = []
        for idx, row in gdf.iterrows():
            nouvelle = _reconstruire_chaine(
                chaine_orig = row.get(champ, ""),
                djma_val    = float(row[col_djma]) if pd.notna(row[col_djma]) else np.nan,
                cam_val     = float(row[col_cam])  if pd.notna(row[col_cam])  else np.nan,
                est_annee1  = est_1,
            )
            nouvelles_valeurs.append(nouvelle)

        gdf[champ] = nouvelles_valeurs

    print(f"  {len(ANNEE_CHAMPS)} champs texte reconstruits.")
    return gdf


# ============================================================
# PHASE 1 — COMPLÉTION TEMPORELLE
# ============================================================

def phase1_temporelle(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    print("\n[Phase 1] Complétion temporelle...")

    for col in DJMA_COLS + CAM_COLS:
        gdf[col] = pd.to_numeric(gdf[col], errors="coerce")

    gdf["n_djma_original"] = gdf[DJMA_COLS].notna().sum(axis=1).astype(int)
    gdf["n_cam_original"]  = gdf[CAM_COLS].notna().sum(axis=1).astype(int)

    methodes_djma = []
    methodes_cam  = []
    bornees_djma  = []
    bornees_cam   = []

    for idx, row in gdf.iterrows():
        v_djma, m_djma, b_djma = _completer_serie(_serie_vers_array(row, DJMA_COLS), ANNEES)
        for j, col in enumerate(DJMA_COLS):
            gdf.at[idx, col] = v_djma[j]
        methodes_djma.append(m_djma)
        bornees_djma.append(b_djma)

        v_cam = _serie_vers_array(row, CAM_COLS)
        n_cam = int(np.sum(~np.isnan(v_cam)))
        if n_cam == 0 and row["n_djma_original"] > 0:
            methodes_cam.append("mice")
            bornees_cam.append(0)
        else:
            v_cam_c, m_cam, b_cam = _completer_serie(v_cam, ANNEES)
            for j, col in enumerate(CAM_COLS):
                gdf.at[idx, col] = v_cam_c[j]
            methodes_cam.append(m_cam)
            bornees_cam.append(b_cam)

    gdf["methode_djma"]   = methodes_djma
    gdf["methode_cam"]    = methodes_cam
    gdf["n_bornees_djma"] = bornees_djma
    gdf["n_bornees_cam"]  = bornees_cam

    print(f"  DJMA complet          : {(gdf['methode_djma']=='complet').sum():>5}")
    print(f"  DJMA interpolé        : {(gdf['methode_djma']=='interpolation').sum():>5}")
    print(f"  DJMA extrapolé        : {(gdf['methode_djma']=='extrapolation').sum():>5}"
          f"  (dont {(gdf['n_bornees_djma'] > 0).sum()} bornées à l'intervalle mesuré)")
    print(f"  DJMA → Phase 2 (KNN)  : {(gdf['methode_djma']=='geo_knn').sum():>5}")
    print(f"  %cam extrapolé        : {(gdf['methode_cam']=='extrapolation').sum():>5}"
          f"  (dont {(gdf['n_bornees_cam'] > 0).sum()} bornées à l'intervalle mesuré)")
    print(f"  %cam → MICE           : {(gdf['methode_cam']=='mice').sum():>5}")
    return gdf


# ============================================================
# FEATURES SUPPLÉMENTAIRES — DJME/DJMH, PROFIL HORAIRE, TYPE DE ROUTE, GÉOGRAPHIE
# ============================================================

def construire_features_supplementaires(gdf: gpd.GeoDataFrame) -> pd.DataFrame:
    """
    Construit, SANS modifier gdf, les features utilisées par le RandomForest
    de mice_cam en plus du seul profil DJMA : débits de pointe (DJME/DJMH),
    profil horaire (30e heure), type de route (jointure spatiale la plus
    proche avec le réseau RTSS du MTQ) et localisation géographique
    (centroïde projeté).

    Ce sont des prédicteurs internes au modèle, pas des colonnes de sortie :
    DJME et DJMH doivent rester tels quels dans debits_completes.gpkg (cf.
    reconstruire_champs_texte), donc leur version nettoyée pour le RF vit
    uniquement dans ce DataFrame séparé. Complétées par une moyenne de ligne
    (repli : médiane globale) — pas la cascade complète réservée à DJMA/%cam.

    Réutilisée telle quelle par generer_figures_resultats.py pour que la
    validation croisée porte sur exactement les mêmes features que le modèle
    de production.
    """
    print("\n[Features RF] Préparation DJME/DJMH/30e heure/type de route/géographie...")
    features = pd.DataFrame(index=gdf.index)

    for cols in (DJME_COLS, DJMH_COLS):
        num      = gdf[cols].apply(pd.to_numeric, errors="coerce")
        moyennes = num.mean(axis=1)
        mediane  = num.stack().median()
        for col in cols:
            features[col] = num[col].fillna(moyennes).fillna(mediane)

    col_30e = pd.to_numeric(gdf[COL_30E], errors="coerce")
    features[COL_30E] = col_30e.fillna(col_30e.median())

    rtss = gpd.read_file(RTSS_FILE)[["des_clasf_", "geometry"]].to_crs(gdf.crs)
    jointure = gpd.sjoin_nearest(gdf[["geometry"]], rtss, how="left")
    jointure = jointure[~jointure.index.duplicated(keep="first")].reindex(gdf.index)
    type_route = jointure["des_clasf_"].where(jointure["des_clasf_"].isin(CATEGORIES_ROUTE), "Autre")
    for cat, col in zip(CATEGORIES_ROUTE, ROUTE_DUMMY_COLS):
        features[col] = (type_route == cat).astype(float).values

    centroides = gdf.to_crs(CRS_PROJETE).geometry.centroid
    features["x_centroide"] = centroides.x.values
    features["y_centroide"] = centroides.y.values

    print(f"  Types de route assignés : {type_route.value_counts().to_dict()}")
    return features


# ============================================================
# MICE — COMPLÉTION %cam PAR RANDOMFOREST (DJMA + FEATURES SUPPLÉMENTAIRES)
# ============================================================

def mice_cam(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    mask_mice = gdf["methode_cam"] == "mice"
    if not mask_mice.any():
        print("\n[MICE] Aucun segment à traiter.")
        return gdf

    print(f"\n[MICE] Complétion %cam par RandomForest ({mask_mice.sum()} segments)...")

    mask_train = (
        gdf["methode_djma"].isin(["complet", "interpolation", "extrapolation"])
        & gdf["methode_cam"].isin(["complet", "interpolation", "extrapolation"])
    )
    print(f"  Segments d'entraînement disponibles : {mask_train.sum()}")

    if mask_train.sum() < 50:
        print("  AVERTISSEMENT : trop peu de données d'entraînement, MICE ignoré.")
        return gdf

    features = construire_features_supplementaires(gdf)

    X_train = np.hstack([
        gdf.loc[mask_train, DJMA_COLS].values,
        features.loc[mask_train, FEATURES_SUPPLEMENTAIRES].values,
    ])
    X_mice = np.hstack([
        gdf.loc[mask_mice, DJMA_COLS].values,
        features.loc[mask_mice, FEATURES_SUPPLEMENTAIRES].values,
    ])
    Y_train = gdf.loc[mask_train, CAM_COLS].values
    n_train = X_train.shape[0]
    n_pred  = X_mice.shape[0]
    n_feat  = X_train.shape[1]

    imputer = IterativeImputer(
        estimator=RandomForestRegressor(n_estimators=50, random_state=42, n_jobs=-1),
        max_iter=10,
        random_state=42,
    )
    M_full    = np.vstack([
        np.hstack([X_train, Y_train]),
        np.hstack([X_mice,  np.full((n_pred, N_ANNEES), np.nan)]),
    ])
    M_imputed    = imputer.fit_transform(M_full)
    cam_imputed  = np.maximum(M_imputed[n_train:, n_feat:], 0.0)

    idx_mice = gdf.index[mask_mice].tolist()
    for i, idx in enumerate(idx_mice):
        for j, col in enumerate(CAM_COLS):
            gdf.at[idx, col] = round(float(cam_imputed[i, j]), 1)

    print(f"  %cam imputé avec RandomForest enrichi ({n_feat} features) pour {n_pred} segments.")
    return gdf


# ============================================================
# PHASE 2 — COMPLÉTION GÉOGRAPHIQUE (KNN + IDW)
# ============================================================

def voisins_ponderes(
    coord: np.ndarray,
    type_cible: str,
    coords_src: np.ndarray,
    types_src: np.ndarray,
    src_idx: list,
    arbre: cKDTree,
    k: int = K_VOISINS,
) -> tuple[list, np.ndarray, np.ndarray]:
    """
    Sélectionne les k voisins géographiques les plus proches d'un segment
    cible (priorité aux voisins de même index_agreg — direction N/O —, repli
    sur les autres si besoin) et leurs poids IDW (1/distance²).

    Factorisé hors de phase2_geographique pour être réutilisé tel quel par
    la figure d'illustration knn_geographique (generer_figure_donnees.py) :
    même algorithme, mêmes résultats, pas de logique dupliquée.
    """
    k_large              = min(k * 4, len(src_idx))
    distances, positions = arbre.query(coord, k=k_large)
    distances            = np.atleast_1d(distances)
    positions            = np.atleast_1d(positions)
    distances            = np.where(distances == 0, 1.0, distances)

    meme_type = np.array([types_src[p] == type_cible for p in positions])
    pos_prio  = positions[meme_type][:k]
    dist_prio = distances[meme_type][:k]

    if len(pos_prio) < k:
        pos_fb     = positions[~meme_type][:k - len(pos_prio)]
        dist_fb    = distances[~meme_type][:k - len(pos_prio)]
        pos_final  = np.concatenate([pos_prio, pos_fb])
        dist_final = np.concatenate([dist_prio, dist_fb])
    else:
        pos_final  = pos_prio
        dist_final = dist_prio

    poids       = 1.0 / (dist_final ** 2)
    poids       = poids / poids.sum()
    idx_voisins = [src_idx[p] for p in pos_final]
    return idx_voisins, poids, dist_final


def phase2_geographique(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    mask_knn = gdf["methode_djma"] == "geo_knn"
    if not mask_knn.any():
        print("\n[Phase 2] Aucun segment sans DJMA.")
        return gdf

    print(f"\n[Phase 2] Complétion géographique KNN ({mask_knn.sum()} segments)...")

    gdf_proj   = gdf.to_crs(CRS_PROJETE)
    centroides = gdf_proj.geometry.centroid
    mask_src   = ~mask_knn
    src_idx    = gdf.index[mask_src].tolist()

    coords_src   = np.array([(c.x, c.y) for c in centroides[mask_src]])
    coords_cible = np.array([(c.x, c.y) for c in centroides[mask_knn]])
    types_src    = gdf.loc[mask_src,  "index_agreg"].values
    types_cibles = gdf.loc[mask_knn,  "index_agreg"].values
    idx_cibles   = gdf.index[mask_knn].tolist()
    arbre        = cKDTree(coords_src)

    # Une section sans DJMA publié peut avoir un % camions publié (455 sections) : sa série
    # de % camions, complétée en phase 1 à partir de ses propres mesures, est gardée.
    # Avant le 2026-11-17, les voisines l'écrasaient (788 valeurs publiées remplacées).
    cam_publie = gdf["n_cam_original"] > 0
    n_cam_gardes = int((mask_knn & cam_publie).sum())

    n_traites = 0
    for coord, type_cible, idx_seg in zip(coords_cible, types_cibles, idx_cibles):
        idx_voisins, poids, _ = voisins_ponderes(coord, type_cible, coords_src, types_src, src_idx, arbre)

        for col in DJMA_COLS:
            vals = gdf.loc[idx_voisins, col].values.astype(float)
            gdf.at[idx_seg, col] = round(float(np.dot(poids, vals)), 0)
        gdf.at[idx_seg, "methode_djma"] = "geo_knn"
        if not cam_publie.at[idx_seg]:
            for col in CAM_COLS:
                vals = gdf.loc[idx_voisins, col].values.astype(float)
                gdf.at[idx_seg, col] = round(float(np.dot(poids, vals)), 1)
            gdf.at[idx_seg, "methode_cam"] = "geo_knn"
        n_traites += 1
        if n_traites % 500 == 0:
            print(f"  ...{n_traites}/{mask_knn.sum()} segments traités")

    print(f"  Complétion géographique terminée : {n_traites} segments "
          f"({n_cam_gardes} gardent leur propre % camions publié).")
    return gdf


# ============================================================
# CONTRÔLES — RÈGLES QUE TOUTE VALEUR COMPLÉTÉE DOIT RESPECTER
# ============================================================

def controler_completion(gdf: gpd.GeoDataFrame, brut: pd.DataFrame) -> None:
    """
    Vérifie la sortie avant l'écriture ; lève une ValueError si une règle est violée.

    `brut` : valeurs DJMA et %cam du fichier du MTQ avant complétion (mêmes lignes,
    colonnes DJMA_COLS + CAM_COLS).

      1. plus aucune valeur manquante ;
      2. DJMA > 0 et %cam dans [0, 100] ;
      3. les valeurs publiées par le MTQ restent intactes ;
      4. aucune valeur complétée ≤ 0 sur une section où le MTQ a mesuré une valeur > 0 ;
      5. sections interpolées ou extrapolées : chaque valeur complétée reste dans
         l'intervalle [min, max] des valeurs mesurées sur la section.
    """
    print("\n[Contrôles] Règles de la complétion...")
    ids = gdf["ide_sectn_trafc"].to_numpy()
    erreurs: list[str] = []

    def signaler(nom: str, regle: str, lignes: np.ndarray) -> None:
        if lignes.any():
            exemples = ", ".join(str(x) for x in ids[lignes][:5])
            erreurs.append(f"{nom} : {regle} — {int(lignes.sum())} sections (ex. {exemples})")

    for nom, cols, suffixe in (("DJMA", DJMA_COLS, "djma"), ("%cam", CAM_COLS, "cam")):
        v = gdf[cols].to_numpy(dtype=float)
        b = brut[cols].to_numpy(dtype=float)
        complete = np.isnan(b)

        signaler(nom, "valeurs manquantes", np.isnan(v).any(axis=1))
        if suffixe == "djma":
            signaler(nom, "DJMA ≤ 0", (v <= 0).any(axis=1))
        else:
            signaler(nom, "% camions hors de [0, 100]", ((v < 0) | (v > 100)).any(axis=1))
        signaler(nom, "valeur publiée modifiée", (~complete & (np.abs(v - b) > 1e-6)).any(axis=1))
        mesure_positive = (np.nan_to_num(b, nan=0.0) > 0).any(axis=1)
        signaler(nom, "valeur complétée ≤ 0 malgré une mesure > 0",
                 (complete & (v <= 0)).any(axis=1) & mesure_positive)
        bas, haut = np.nanmin(b, axis=1), np.nanmax(b, axis=1)       # NaN si rien de mesuré
        hors = complete & ((v < bas[:, None] - 1e-9) | (v > haut[:, None] + 1e-9))
        temporelle = gdf[f"methode_{suffixe}"].isin(["interpolation", "extrapolation"]).to_numpy()
        signaler(nom, "valeur complétée hors de l'intervalle mesuré", hors.any(axis=1) & temporelle)

        n_bornees = gdf[f"n_bornees_{suffixe}"]
        print(f"  {nom:5s}: {int(n_bornees.sum())} valeurs extrapolées bornées à l'intervalle mesuré "
              f"({int((n_bornees > 0).sum())} sections)")
    if erreurs:
        raise ValueError("Complétion refusée :\n  " + "\n  ".join(erreurs))
    print("  Toutes les règles sont respectées.")


# ============================================================
# MAIN
# ============================================================

def filtrer_echantillon(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Sections dont le centroïde tombe dans l'emprise de l'échantillon (configs/reseau.yaml)."""
    import yaml
    e = yaml.safe_load(FICHIER_CONFIG_RESEAU.read_text(encoding="utf-8"))["echantillon"]
    centres = gdf.to_crs(CRS_PROJETE).geometry.centroid.to_crs("EPSG:4326")
    dedans = centres.x.between(e["lon_min"], e["lon_max"]) & centres.y.between(e["lat_min"], e["lat_max"])
    print(f"  Échantillon « {e['nom']} » : {int(dedans.sum())} sections sur {len(gdf)}")
    return gdf[dedans.to_numpy()].reset_index(drop=True)


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Complétion des données de débit (Module 1).")
    parser.add_argument("--echantillon", action="store_true",
                        help="emprise de l'échantillon seulement ; sortie dans outputs/module1/echantillon/")
    args = parser.parse_args()
    sortie = SORTIE_ECHANTILLON if args.echantillon else OUTPUT_FILE

    print("=" * 60)
    print("completion_donnees_randomforest.py")
    print("Complétion des données de débit — CIRANO")
    print("=" * 60)

    print(f"\nChargement : {INPUT_FILE}")
    gdf = gpd.read_file(INPUT_FILE)
    print(f"  {len(gdf)} segments chargés")
    if args.echantillon:
        gdf = filtrer_echantillon(gdf)
    brut = gdf[DJMA_COLS + CAM_COLS].apply(pd.to_numeric, errors="coerce")

    gdf = phase1_temporelle(gdf)
    gdf = mice_cam(gdf)
    gdf = phase2_geographique(gdf)
    controler_completion(gdf, brut)
    gdf = reconstruire_champs_texte(gdf)

    print("\n[Bilan final]")
    print("  Méthode DJMA :")
    print(gdf["methode_djma"].value_counts().to_string())
    print("  Méthode %cam :")
    print(gdf["methode_cam"].value_counts().to_string())

    djma_restants = gdf[DJMA_COLS].isna().any(axis=1).sum()
    cam_restants  = gdf[CAM_COLS].isna().any(axis=1).sum()
    print(f"\n  Segments avec DJMA encore incomplets : {djma_restants}")
    print(f"  Segments avec %cam encore incomplets : {cam_restants}")

    sortie.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(sortie, layer=OUTPUT_LAYER, driver="GPKG")
    print(f"\nSortie écrite : {sortie}  (layer: {OUTPUT_LAYER})")
    print("=" * 60)


if __name__ == "__main__":
    main()
