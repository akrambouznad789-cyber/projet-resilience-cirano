"""
calcul_djma_m4.py
=================
Projet CIRANO II — Calcul du DJMA agrégé par arc (méthode m4)

MÉTHODE
-------
Pondération composite : longueur × poids_type.
Un segment long sur une Autoroute contribue davantage qu'un segment court sur une route locale.

    poids_composite = longueur_m × poids_type
    djma_m4 = Σ(djma_val × poids_composite) / Σ(poids_composite)

La longueur est calculée depuis la géométrie de trajets_segments_v2 (CRS EPSG:32198 → mètres).
Le type d'axe est extrait depuis ids_segs_rtss_type (arcs_enrichis_v2), aligné par ID_ARC
et ide_sectn_trafc avec les segments de trajets_segments_v2.

ENTRÉE
------
  data/processed/graphe_routier_v2.gpkg  (layers : arcs_enrichis_v2, trajets_segments_v2)

SORTIE
------
  data/processed/graphe_routier_v2_djma_m4.gpkg  (layer : arcs_enrichis_v2_djma_m4)

CHAMPS PRODUITS
---------------
  djma_m4        : moyenne pondérée composite longueur × type (entier)
  pct_cam_m4     : % camion pondéré composite (1 décimale)
  djma_cam_m4    : débit camion estimé (entier)
  n_segs_m4      : nombre de segments ayant contribué
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
OUTPUT_FILE  = os.path.expanduser("~/projects/projet-resilience-cirano/data/processed/graphe_routier_v2_djma_m4.gpkg")
OUTPUT_LAYER = "arcs_enrichis_v2_djma_m4"

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
# CONSTRUCTION DE LA TABLE TYPE PAR SEGMENT
# ============================================================

def construire_table_types(arcs: gpd.GeoDataFrame) -> dict[tuple[int, int], str]:
    """
    Construit un index {(ID_ARC, ide_sectn_trafc) → type_axe} depuis
    les champs encodés ids_segs_djma et ids_segs_rtss_type de arcs_enrichis_v2.

    Permet d'affecter le type à chaque ligne de trajets_segments_v2
    sans join spatial sur le RTSS brut.
    """
    index: dict[tuple[int, int], str] = {}
    for _, row in arcs.iterrows():
        chaine_ids  = row.get("ids_segs_djma_id")
        chaine_type = row.get("ids_segs_rtss_type")
        id_arc      = row["ID_ARC"]

        if not chaine_ids or pd.isna(chaine_ids):
            continue

        tokens_ids  = str(chaine_ids).split("|")
        tokens_type = str(chaine_type).split("|") if (chaine_type and not pd.isna(chaine_type)) \
                      else [""] * len(tokens_ids)

        for tid, ttype in zip(tokens_ids, tokens_type):
            tid = tid.strip()
            if not tid or tid.upper() == "NA":
                continue
            try:
                index[(int(id_arc), int(tid))] = ttype.strip()
            except ValueError:
                continue
    return index


# ============================================================
# CALCUL PAR ARC
# ============================================================

def aggreger_par_arc(segs: gpd.GeoDataFrame) -> pd.DataFrame:
    """
    Pour chaque arc, calcule la moyenne pondérée composite (longueur × type)
    de djma_val et cam_val.
    Entrée : trajets_segments_v2 avec colonnes longueur_m et poids_type ajoutées.
    """
    resultats = []
    for id_arc, groupe in segs.groupby("ID_ARC"):
        vals_djma = groupe["djma_val"].dropna()
        if vals_djma.empty:
            resultats.append({
                "ID_ARC": id_arc, "djma_m4": None, "pct_cam_m4": None,
                "djma_cam_m4": None, "n_segs_m4": 0,
            })
            continue

        idx_valides = vals_djma.index
        poids_comp  = groupe.loc[idx_valides, "poids_composite"]
        total_poids = poids_comp.sum()

        if total_poids == 0:
            resultats.append({
                "ID_ARC": id_arc, "djma_m4": None, "pct_cam_m4": None,
                "djma_cam_m4": None, "n_segs_m4": 0,
            })
            continue

        djma_m4 = round(float(np.average(vals_djma, weights=poids_comp)))
        n_segs  = len(vals_djma)

        vals_cam = groupe.loc[idx_valides, "cam_val"].dropna()
        if not vals_cam.empty:
            pc_comp    = groupe.loc[vals_cam.index, "poids_composite"]
            pct_cam_m4 = round(float(np.average(vals_cam, weights=pc_comp)), 1)
        else:
            pct_cam_m4 = None

        djma_cam_m4 = round(djma_m4 * pct_cam_m4 / 100) if pct_cam_m4 is not None else None

        resultats.append({
            "ID_ARC": id_arc,
            "djma_m4": djma_m4,
            "pct_cam_m4": pct_cam_m4,
            "djma_cam_m4": djma_cam_m4,
            "n_segs_m4": n_segs,
        })

    return pd.DataFrame(resultats)


# ============================================================
# PIPELINE PRINCIPAL
# ============================================================

def main() -> None:
    print("=" * 65)
    print("CALCUL DJMA — méthode m4 (pondération composite longueur × type)")
    print("=" * 65)

    print(f"\n[1/5] Chargement arcs_enrichis_v2...")
    arcs = gpd.read_file(INPUT_FILE, layer="arcs_enrichis_v2")
    print(f"  {len(arcs)} arcs chargés")

    print(f"\n[2/5] Construction de la table type par segment...")
    table_types = construire_table_types(arcs)
    print(f"  {len(table_types)} entrées (ID_ARC, ide_sectn_trafc) → type")

    print(f"\n[3/5] Chargement trajets_segments_v2...")
    segs = gpd.read_file(INPUT_FILE, layer="trajets_segments_v2")
    segs["longueur_m"] = segs.geometry.length

    # Affecter le type et le poids composite à chaque segment
    segs["type_axe"] = segs.apply(
        lambda r: table_types.get((int(r["ID_ARC"]), int(r["ide_sectn_trafc"])), ""),
        axis=1,
    )
    segs["poids_type"]      = segs["type_axe"].map(lambda t: POIDS_TYPE.get(t, POIDS_DEFAUT))
    segs["poids_composite"] = segs["longueur_m"] * segs["poids_type"]

    types_trouves = (segs["type_axe"] != "").sum()
    print(f"  {len(segs)} segments  |  type trouvé : {types_trouves}/{len(segs)}")

    print("\n[4/5] Calcul m4 (composite longueur × type)...")
    df_resultats = aggreger_par_arc(segs)

    arcs = arcs.merge(df_resultats, on="ID_ARC", how="left")
    arcs["djma_m4"]     = arcs["djma_m4"].astype("Int64")
    arcs["djma_cam_m4"] = arcs["djma_cam_m4"].astype("Int64")
    arcs["n_segs_m4"]   = arcs["n_segs_m4"].fillna(0).astype(int)

    n_avec = arcs["djma_m4"].notna().sum()
    n_sans = arcs["djma_m4"].isna().sum()
    ok     = arcs[arcs["djma_m4"].notna()]

    print(f"  Arcs avec djma_m4 : {n_avec}  |  sans : {n_sans}")
    if n_avec > 0:
        print(f"  djma_m4 — médiane : {ok['djma_m4'].median():.0f}  "
              f"min : {ok['djma_m4'].min()}  max : {ok['djma_m4'].max()}")

    print(f"\n[5/5] Export → {OUTPUT_FILE}")
    arcs.to_file(OUTPUT_FILE, layer=OUTPUT_LAYER, driver="GPKG")
    print("  Terminé.")


if __name__ == "__main__":
    main()
