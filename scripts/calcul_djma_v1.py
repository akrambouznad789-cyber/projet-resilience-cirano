"""
calcul_djma_v1.py
=================
Projet CIRANO II — Calcul du DJMA agrégé par arc (méthode djma_1)

OBJECTIF
--------
À partir du graphe enrichi (arcs_enrichis), calculer pour chaque arc :
  - djma_1       : moyenne simple des valeurs DJMA des segments sous-jacents
  - pct_cam_1    : moyenne simple des pourcentages camion disponibles
  - djma_cam_1   : débit camion estimé = djma_1 × pct_cam_1 / 100
  - n_segs_djma_1: nombre de segments ayant contribué au calcul

La méthode complète est documentée dans docs/methodes_djma.md.

ENTRÉE
------
  data/processed/graphe_routier_v1_sample.gpkg  (layer : arcs_enrichis)

SORTIE
------
  data/processed/graphe_routier_v1_djma.gpkg    (layer : arcs_enrichis_djma1)
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

INPUT_FILE   = os.path.expanduser("~/projects/projet-resilience-cirano/data/processed/graphe_routier_v1_sample.gpkg")
INPUT_LAYER  = "arcs_enrichis"
OUTPUT_FILE  = os.path.expanduser("~/projects/projet-resilience-cirano/data/processed/graphe_routier_v1_djma.gpkg")
OUTPUT_LAYER = "arcs_enrichis_djma1"


# ============================================================
# PARSING
# ============================================================

def parser_valeurs(chaine: str | None, separateur: str = "|") -> list[float]:
    """
    Extrait les valeurs numériques d'une chaîne au format 'valeur@année|valeur@année|...'.

    Les tokens 'NA' ou vides sont ignorés.
    Retourne une liste de floats (peut être vide).
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

def calculer_djma_1(row: pd.Series) -> tuple[float | None, float | None, float | None, int]:
    """
    Calcule djma_1, pct_cam_1, djma_cam_1 et n_segs_djma_1 pour un arc.

    Retourne (djma_1, pct_cam_1, djma_cam_1, n_segs_djma_1).
    Tous les champs sont None si aucun segment valide n'est disponible.
    """
    vals_djma = parser_valeurs(row.get("ids_segs_djma_val"))
    vals_cam  = parser_valeurs(row.get("ids_segs_djma_val_cam"))

    if not vals_djma:
        return None, None, None, 0

    djma_1  = round(float(np.mean(vals_djma)))
    n_segs  = len(vals_djma)

    pct_cam_1  = round(float(np.mean(vals_cam)), 1) if vals_cam else None
    djma_cam_1 = round(djma_1 * pct_cam_1 / 100) if pct_cam_1 is not None else None

    return djma_1, pct_cam_1, djma_cam_1, n_segs


# ============================================================
# ENRICHISSEMENT
# ============================================================

def enrichir_arcs(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Applique calculer_djma_1 à chaque arc et ajoute les colonnes résultantes.

    Colonnes ajoutées : djma_1, pct_cam_1, djma_cam_1, n_segs_djma_1.
    La colonne statut_djma_pct existante est conservée telle quelle.
    """
    resultats = gdf.apply(calculer_djma_1, axis=1, result_type="expand")
    resultats.columns = ["djma_1", "pct_cam_1", "djma_cam_1", "n_segs_djma_1"]

    gdf = gdf.copy()
    gdf["djma_1"]        = resultats["djma_1"].astype("Int64")
    gdf["pct_cam_1"]     = resultats["pct_cam_1"]
    gdf["djma_cam_1"]    = resultats["djma_cam_1"].astype("Int64")
    gdf["n_segs_djma_1"] = resultats["n_segs_djma_1"].astype(int)

    return gdf


# ============================================================
# PIPELINE PRINCIPAL
# ============================================================

def main() -> None:
    print("=" * 65)
    print("CALCUL DJMA — Projet CIRANO II — méthode djma_1")
    print("=" * 65)

    print(f"\n[1/3] Chargement de {INPUT_LAYER}...")
    gdf = gpd.read_file(INPUT_FILE, layer=INPUT_LAYER)
    print(f"  {len(gdf)} arcs chargés")

    print("\n[2/3] Calcul djma_1...")
    gdf = enrichir_arcs(gdf)

    n_avec = gdf["djma_1"].notna().sum()
    n_sans = gdf["djma_1"].isna().sum()
    ok     = gdf[gdf["djma_1"].notna()]

    print(f"  Arcs avec djma_1       : {n_avec}")
    print(f"  Arcs sans djma_1       : {n_sans}")
    if n_avec > 0:
        print(f"\n  djma_1  — médiane : {ok['djma_1'].median():.0f}  "
              f"min : {ok['djma_1'].min()}  max : {ok['djma_1'].max()}")
        cam_ok = ok[ok["pct_cam_1"].notna()]
        if not cam_ok.empty:
            print(f"  pct_cam_1 — médiane : {cam_ok['pct_cam_1'].median():.1f}%")
        cam_abs = ok["pct_cam_1"].isna().sum()
        if cam_abs:
            print(f"  Arcs sans pct_cam_1 : {cam_abs}")

    print(f"\n[3/3] Export vers {OUTPUT_FILE} (layer : {OUTPUT_LAYER})...")
    gdf.to_file(OUTPUT_FILE, layer=OUTPUT_LAYER, driver="GPKG")
    print("  Export terminé.")

    print("\n" + "=" * 65)
    print("RÉSUMÉ")
    print("=" * 65)
    print(f"Arcs traités   : {len(gdf)}")
    print(f"Arcs avec djma_1   : {n_avec}")
    print(f"Arcs sans djma_1   : {n_sans}")
    print(f"\nSortie : data/processed/graphe_routier_v1_djma.gpkg")


if __name__ == "__main__":
    main()
