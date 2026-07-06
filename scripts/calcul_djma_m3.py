"""
calcul_djma_m3.py
=================
Projet CIRANO II — Calcul du DJMA agrégé par arc (méthode m3)

MÉTHODE
-------
Pondération par type d'axe routier (hiérarchie fonctionnelle MTQ).
Le type de chaque segment est extrait du champ ids_segs_rtss_type
déjà encodé dans arcs_enrichis_v2 (aligné positionnellement avec ids_segs_djma_val).

    djma_m3 = Σ(djma_val × poids_type) / Σ(poids_type)

Table des poids (basée sur la fiabilité et la représentativité des comptages) :
  Autoroute                                    → 4
  Nationale                                    → 3
  Régionale                                    → 2
  Collectrice                                  → 2
  Accès aux ressources                         → 1
  Accès aux ressources et aux localités isolées → 1
  Sans classe / Local 1/2/3                    → 1

ENTRÉE
------
  data/processed/graphe_routier_v2.gpkg  (layer : arcs_enrichis_v2)

SORTIE
------
  data/processed/graphe_routier_v2_djma_m3.gpkg  (layer : arcs_enrichis_v2_djma_m3)

CHAMPS PRODUITS
---------------
  djma_m3        : moyenne pondérée par type d'axe (entier)
  pct_cam_m3     : % camion pondéré par type d'axe (1 décimale)
  djma_cam_m3    : débit camion estimé (entier)
  n_segs_m3      : nombre de segments ayant contribué
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
OUTPUT_FILE  = os.path.expanduser("~/projects/projet-resilience-cirano/data/processed/graphe_routier_v2_djma_m3.gpkg")
OUTPUT_LAYER = "arcs_enrichis_v2_djma_m3"

POIDS_TYPE: dict[str, int] = {
    "Autoroute": 4,
    "Nationale": 3,
    "Régionale": 2,
    "Collectrice": 2,
    "Accès aux ressources": 1,
    "Accès aux ressources et aux localités isolées": 1,
    "Sans classe": 1,
    "Local 1": 1,
    "Local 2": 1,
    "Local 3": 1,
}
POIDS_DEFAUT = 1


# ============================================================
# PARSING
# ============================================================

def parser_paires_djma_type(
    chaine_val: str | None,
    chaine_type: str | None,
    separateur: str = "|",
) -> tuple[list[float], list[int]]:
    """
    Parse en parallèle ids_segs_djma_val et ids_segs_rtss_type.
    Ignore les positions où djma_val est NA ou invalide.
    Retourne (valeurs_djma, poids_type) alignés.
    """
    if not chaine_val or pd.isna(chaine_val):
        return [], []

    tokens_val  = str(chaine_val).split(separateur)
    tokens_type = str(chaine_type).split(separateur) if (chaine_type and not pd.isna(chaine_type)) \
                  else [""] * len(tokens_val)

    vals, poids = [], []
    for tv, tt in zip(tokens_val, tokens_type):
        tv = tv.strip()
        if not tv or tv.upper() == "NA":
            continue
        partie = tv.split("@")[0]
        try:
            vals.append(float(partie))
            poids.append(POIDS_TYPE.get(tt.strip(), POIDS_DEFAUT))
        except ValueError:
            continue
    return vals, poids


def parser_paires_cam_type(
    chaine_cam: str | None,
    chaine_type: str | None,
    separateur: str = "|",
) -> tuple[list[float], list[int]]:
    """Même logique que parser_paires_djma_type mais pour les % camion."""
    return parser_paires_djma_type(chaine_cam, chaine_type, separateur)


# ============================================================
# CALCUL PAR ARC
# ============================================================

def calculer_m3(row: pd.Series) -> tuple[float | None, float | None, float | None, int]:
    """Moyenne pondérée par type d'axe des DJMA et % camion des segments."""
    vals_djma, poids = parser_paires_djma_type(
        row.get("ids_segs_djma_val"),
        row.get("ids_segs_rtss_type"),
    )

    if not vals_djma:
        return None, None, None, 0

    total_poids = sum(poids)
    djma_m3 = round(float(np.average(vals_djma, weights=poids))) if total_poids > 0 else round(float(np.mean(vals_djma)))
    n_segs  = len(vals_djma)

    vals_cam, poids_cam = parser_paires_cam_type(
        row.get("ids_segs_djma_val_cam"),
        row.get("ids_segs_rtss_type"),
    )
    if vals_cam:
        total_pc = sum(poids_cam)
        pct_cam_m3 = round(float(np.average(vals_cam, weights=poids_cam)), 1) if total_pc > 0 \
                     else round(float(np.mean(vals_cam)), 1)
    else:
        pct_cam_m3 = None

    djma_cam_m3 = round(djma_m3 * pct_cam_m3 / 100) if pct_cam_m3 is not None else None
    return djma_m3, pct_cam_m3, djma_cam_m3, n_segs


def enrichir_arcs(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    resultats = gdf.apply(calculer_m3, axis=1, result_type="expand")
    resultats.columns = ["djma_m3", "pct_cam_m3", "djma_cam_m3", "n_segs_m3"]

    gdf = gdf.copy()
    gdf["djma_m3"]    = resultats["djma_m3"].astype("Int64")
    gdf["pct_cam_m3"] = resultats["pct_cam_m3"]
    gdf["djma_cam_m3"] = resultats["djma_cam_m3"].astype("Int64")
    gdf["n_segs_m3"]  = resultats["n_segs_m3"].astype(int)
    return gdf


# ============================================================
# PIPELINE PRINCIPAL
# ============================================================

def main() -> None:
    print("=" * 65)
    print("CALCUL DJMA — méthode m3 (pondération par type d'axe)")
    print("=" * 65)
    print("\nPoids utilisés :")
    for t, p in POIDS_TYPE.items():
        print(f"  {t:<50} → {p}")

    print(f"\n[1/3] Chargement {INPUT_LAYER}...")
    gdf = gpd.read_file(INPUT_FILE, layer=INPUT_LAYER)
    print(f"  {len(gdf)} arcs chargés")

    print("\n[2/3] Calcul m3...")
    gdf = enrichir_arcs(gdf)

    n_avec = gdf["djma_m3"].notna().sum()
    n_sans = gdf["djma_m3"].isna().sum()
    ok     = gdf[gdf["djma_m3"].notna()]

    print(f"  Arcs avec djma_m3 : {n_avec}  |  sans : {n_sans}")
    if n_avec > 0:
        print(f"  djma_m3 — médiane : {ok['djma_m3'].median():.0f}  "
              f"min : {ok['djma_m3'].min()}  max : {ok['djma_m3'].max()}")

    print(f"\n[3/3] Export → {OUTPUT_FILE}")
    gdf.to_file(OUTPUT_FILE, layer=OUTPUT_LAYER, driver="GPKG")
    print("  Terminé.")


if __name__ == "__main__":
    main()
