"""
calcul_djma_m2.py
=================
Projet CIRANO II — Calcul du DJMA agrégé par arc (méthode m2)

MÉTHODE
-------
Pondération par longueur de segment.
Un segment de 5 km contribue 100× plus qu'un segment de 50 m.
La longueur est calculée depuis la géométrie de trajets_segments_v2 (CRS EPSG:32198 → mètres).

    djma_m2 = Σ(djma_val × longueur_m) / Σ(longueur_m)

ENTRÉE
------
  data/processed/graphe_routier_v2.gpkg  (layers : arcs_enrichis_v2, trajets_segments_v2)

SORTIE
------
  data/processed/graphe_routier_v2_djma_m2.gpkg  (layer : arcs_enrichis_v2_djma_m2)

CHAMPS PRODUITS
---------------
  djma_m2        : moyenne pondérée par longueur (entier)
  pct_cam_m2     : % camion pondéré par longueur (1 décimale)
  djma_cam_m2    : débit camion estimé (entier)
  n_segs_m2      : nombre de segments ayant contribué
  longueur_segs_m : longueur totale des segments contributeurs (mètres)
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
OUTPUT_FILE  = os.path.expanduser("~/projects/projet-resilience-cirano/data/processed/graphe_routier_v2_djma_m2.gpkg")
OUTPUT_LAYER = "arcs_enrichis_v2_djma_m2"


# ============================================================
# CALCUL PAR ARC
# ============================================================

def aggreger_par_arc(segs: gpd.GeoDataFrame) -> pd.DataFrame:
    """
    Pour chaque arc, calcule la moyenne pondérée par longueur de djma_val et cam_val.
    Entrée : trajets_segments_v2 avec colonne longueur_m ajoutée.
    """
    resultats = []
    for id_arc, groupe in segs.groupby("ID_ARC"):
        vals_djma = groupe["djma_val"].dropna()
        if vals_djma.empty:
            resultats.append({
                "ID_ARC": id_arc, "djma_m2": None, "pct_cam_m2": None,
                "djma_cam_m2": None, "n_segs_m2": 0, "longueur_segs_m": 0.0
            })
            continue

        # Aligner longueurs sur les lignes avec djma_val non-nul
        idx_valides    = vals_djma.index
        poids          = groupe.loc[idx_valides, "longueur_m"]
        total_poids    = poids.sum()

        if total_poids == 0:
            resultats.append({
                "ID_ARC": id_arc, "djma_m2": None, "pct_cam_m2": None,
                "djma_cam_m2": None, "n_segs_m2": 0, "longueur_segs_m": 0.0
            })
            continue

        djma_m2 = round(float(np.average(vals_djma, weights=poids)))
        n_segs  = len(vals_djma)

        # % camion pondéré par longueur (si disponible)
        vals_cam = groupe.loc[idx_valides, "cam_val"].dropna()
        if not vals_cam.empty:
            poids_cam = groupe.loc[vals_cam.index, "longueur_m"]
            pct_cam_m2 = round(float(np.average(vals_cam, weights=poids_cam)), 1)
        else:
            pct_cam_m2 = None

        djma_cam_m2 = round(djma_m2 * pct_cam_m2 / 100) if pct_cam_m2 is not None else None

        resultats.append({
            "ID_ARC": id_arc,
            "djma_m2": djma_m2,
            "pct_cam_m2": pct_cam_m2,
            "djma_cam_m2": djma_cam_m2,
            "n_segs_m2": n_segs,
            "longueur_segs_m": round(total_poids, 1),
        })

    return pd.DataFrame(resultats)


# ============================================================
# PIPELINE PRINCIPAL
# ============================================================

def main() -> None:
    print("=" * 65)
    print("CALCUL DJMA — méthode m2 (pondération par longueur)")
    print("=" * 65)

    print(f"\n[1/4] Chargement arcs_enrichis_v2...")
    arcs = gpd.read_file(INPUT_FILE, layer="arcs_enrichis_v2")
    print(f"  {len(arcs)} arcs chargés")

    print(f"\n[2/4] Chargement trajets_segments_v2...")
    segs = gpd.read_file(INPUT_FILE, layer="trajets_segments_v2")
    segs["longueur_m"] = segs.geometry.length
    print(f"  {len(segs)} segments chargés  "
          f"(longueur totale : {segs['longueur_m'].sum() / 1000:.1f} km)")

    print("\n[3/4] Calcul m2 (moyenne pondérée par longueur)...")
    df_resultats = aggreger_par_arc(segs)

    arcs = arcs.merge(df_resultats, on="ID_ARC", how="left")
    arcs["djma_m2"]        = arcs["djma_m2"].astype("Int64")
    arcs["djma_cam_m2"]    = arcs["djma_cam_m2"].astype("Int64")
    arcs["n_segs_m2"]      = arcs["n_segs_m2"].fillna(0).astype(int)
    arcs["longueur_segs_m"] = arcs["longueur_segs_m"].fillna(0.0)

    n_avec = arcs["djma_m2"].notna().sum()
    n_sans = arcs["djma_m2"].isna().sum()
    ok     = arcs[arcs["djma_m2"].notna()]

    print(f"  Arcs avec djma_m2 : {n_avec}  |  sans : {n_sans}")
    if n_avec > 0:
        print(f"  djma_m2 — médiane : {ok['djma_m2'].median():.0f}  "
              f"min : {ok['djma_m2'].min()}  max : {ok['djma_m2'].max()}")

    print(f"\n[4/4] Export → {OUTPUT_FILE}")
    arcs.to_file(OUTPUT_FILE, layer=OUTPUT_LAYER, driver="GPKG")
    print("  Terminé.")


if __name__ == "__main__":
    main()
