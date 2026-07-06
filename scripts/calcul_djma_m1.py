"""
calcul_djma_m1.py
=================
Projet CIRANO II — Calcul du DJMA agrégé par arc (méthode m1)

MÉTHODE
-------
Moyenne simple des valeurs DJMA des segments sous-jacents.
Chaque segment contribue également, indépendamment de sa longueur ou de son type.

ENTRÉE
------
  data/processed/graphe_routier_v2.gpkg  (layer : arcs_enrichis_v2)

SORTIE
------
  data/processed/graphe_routier_v2_djma_m1.gpkg  (layer : arcs_enrichis_v2_djma_m1)

CHAMPS PRODUITS
---------------
  djma_m1        : moyenne simple des DJMA des segments sous-jacents (entier)
  pct_cam_m1     : moyenne simple des % camion disponibles (1 décimale)
  djma_cam_m1    : débit camion estimé = djma_m1 × pct_cam_m1 / 100 (entier)
  n_segs_m1      : nombre de segments ayant contribué au calcul
"""

import os
import geopandas as gpd
import pandas as pd
import numpy as np
import warnings

warnings.filterwarnings("ignore")

# ============================================================
# CONFIGURATION
# ============================================================

INPUT_FILE   = os.path.expanduser("~/projects/projet-resilience-cirano/data/processed/graphe_routier_v2.gpkg")
INPUT_LAYER  = "arcs_enrichis_v2"
OUTPUT_FILE  = os.path.expanduser("~/projects/projet-resilience-cirano/data/processed/graphe_routier_v2_djma_m1.gpkg")
OUTPUT_LAYER = "arcs_enrichis_v2_djma_m1"


# ============================================================
# PARSING
# ============================================================

def parser_valeurs(chaine: str | None, separateur: str = "|") -> list[float]:
    """
    Extrait les valeurs numériques d'une chaîne encodée 'valeur@année|valeur@année|...'.
    Les tokens 'NA' ou vides sont ignorés.
    """
    if not chaine or pd.isna(chaine):
        return []
    valeurs = []
    for token in str(chaine).split(separateur):
        token = token.strip()
        if not token or token.upper() == "NA":
            continue
        partie = token.split("@")[0]
        try:
            valeurs.append(float(partie))
        except ValueError:
            continue
    return valeurs


# ============================================================
# CALCUL PAR ARC
# ============================================================

def calculer_m1(row: pd.Series) -> tuple[float | None, float | None, float | None, int]:
    """Moyenne simple des DJMA et % camion des segments sous-jacents."""
    vals_djma = parser_valeurs(row.get("ids_segs_djma_val"))
    vals_cam  = parser_valeurs(row.get("ids_segs_djma_val_cam"))

    if not vals_djma:
        return None, None, None, 0

    djma_m1    = round(float(np.mean(vals_djma)))
    n_segs     = len(vals_djma)
    pct_cam_m1 = round(float(np.mean(vals_cam)), 1) if vals_cam else None
    djma_cam_m1 = round(djma_m1 * pct_cam_m1 / 100) if pct_cam_m1 is not None else None

    return djma_m1, pct_cam_m1, djma_cam_m1, n_segs


def enrichir_arcs(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    resultats = gdf.apply(calculer_m1, axis=1, result_type="expand")
    resultats.columns = ["djma_m1", "pct_cam_m1", "djma_cam_m1", "n_segs_m1"]

    gdf = gdf.copy()
    gdf["djma_m1"]    = resultats["djma_m1"].astype("Int64")
    gdf["pct_cam_m1"] = resultats["pct_cam_m1"]
    gdf["djma_cam_m1"] = resultats["djma_cam_m1"].astype("Int64")
    gdf["n_segs_m1"]  = resultats["n_segs_m1"].astype(int)
    return gdf


# ============================================================
# PIPELINE PRINCIPAL
# ============================================================

def main() -> None:
    print("=" * 65)
    print("CALCUL DJMA — méthode m1 (moyenne simple)")
    print("=" * 65)

    print(f"\n[1/3] Chargement {INPUT_LAYER}...")
    gdf = gpd.read_file(INPUT_FILE, layer=INPUT_LAYER)
    print(f"  {len(gdf)} arcs chargés")

    print("\n[2/3] Calcul m1...")
    gdf = enrichir_arcs(gdf)

    n_avec = gdf["djma_m1"].notna().sum()
    n_sans = gdf["djma_m1"].isna().sum()
    ok     = gdf[gdf["djma_m1"].notna()]

    print(f"  Arcs avec djma_m1 : {n_avec}  |  sans : {n_sans}")
    if n_avec > 0:
        print(f"  djma_m1 — médiane : {ok['djma_m1'].median():.0f}  "
              f"min : {ok['djma_m1'].min()}  max : {ok['djma_m1'].max()}")

    print(f"\n[3/3] Export → {OUTPUT_FILE}")
    gdf.to_file(OUTPUT_FILE, layer=OUTPUT_LAYER, driver="GPKG")
    print("  Terminé.")


if __name__ == "__main__":
    main()
