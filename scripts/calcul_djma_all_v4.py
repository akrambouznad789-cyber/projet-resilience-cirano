"""
calcul_djma_all_v4.py
=====================
Projet CIRANO II — Calcul du DJMA agrégé par arc, 4 méthodes combinées

MÉTHODES
--------
  m1 : Moyenne simple des DJMA des segments sous-jacents
  m2 : Moyenne pondérée par longueur de segment (depuis la géométrie de trajets_segments_v4)
  m3 : Moyenne pondérée par type d'axe routier (hiérarchie MTQ)
  m4 : Approximation 90e percentile → α·max(x) + (1−α)·mean(x), α=ALPHA_M4

ENTRÉE
------
  data/processed/graphe_routier_v4.gpkg
    layers : arcs_enrichis_v4, trajets_segments_v4

SORTIE
------
  data/processed/graphe_routier_v4_djma.gpkg
    layer  : arcs_enrichis_v4_djma

CHAMPS PRODUITS (par méthode)
------------------------------
  djma_m{N}     : DJMA agrégé (entier)
  pct_cam_m{N}  : % camion agrégé (1 décimale) — N/A pour m4
  djma_cam_m{N} : débit camion estimé = djma_m{N} × pct_cam_m{N} / 100 (entier)
  n_segs_m{N}   : nombre de segments contributeurs
"""

import os
import warnings

import geopandas as gpd
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ============================================================
# CONFIGURATION
# ============================================================

INPUT_FILE   = os.path.expanduser("~/projects/projet-resilience-cirano/data/processed/graphe_routier_v4.gpkg")
LAYER_ARCS   = "arcs_enrichis_v4"
LAYER_SEGS   = "trajets_segments_v4"

OUTPUT_FILE  = os.path.expanduser("~/projects/projet-resilience-cirano/data/processed/graphe_routier_v4_djma.gpkg")
OUTPUT_LAYER = "arcs_enrichis_v4_djma"

# Poids m3 — hiérarchie fonctionnelle MTQ
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

# Paramètre m4 — proximité du maximum (0.9 = approximation 90e percentile)
ALPHA_M4 = 0.90


# ============================================================
# PARSING COMMUN
# ============================================================

def parser_valeurs(chaine: str | None, separateur: str = "|") -> list[float]:
    """Extrait les valeurs numériques d'une chaîne 'valeur@année|...' ; ignore NA."""
    if not chaine or pd.isna(chaine):
        return []
    valeurs = []
    for token in str(chaine).split(separateur):
        token = token.strip()
        if not token or token.upper() == "NA":
            continue
        try:
            valeurs.append(float(token.split("@")[0]))
        except ValueError:
            continue
    return valeurs


def parser_paires_type(
    chaine_val: str | None,
    chaine_type: str | None,
    separateur: str = "|",
) -> tuple[list[float], list[int]]:
    """Parse en parallèle ids_segs_djma_val et ids_segs_rtss_type → (valeurs, poids)."""
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
        try:
            vals.append(float(tv.split("@")[0]))
            poids.append(POIDS_TYPE.get(tt.strip(), POIDS_DEFAUT))
        except ValueError:
            continue
    return vals, poids


# ============================================================
# M1 — Moyenne simple
# ============================================================

def calculer_m1(row: pd.Series) -> tuple:
    vals_djma = parser_valeurs(row.get("ids_segs_djma_val"))
    vals_cam  = parser_valeurs(row.get("ids_segs_djma_val_cam"))
    if not vals_djma:
        return None, None, None, 0
    djma_m1    = round(float(np.mean(vals_djma)))
    pct_cam_m1 = round(float(np.mean(vals_cam)), 1) if vals_cam else None
    djma_cam_m1 = round(djma_m1 * pct_cam_m1 / 100) if pct_cam_m1 is not None else None
    return djma_m1, pct_cam_m1, djma_cam_m1, len(vals_djma)


def appliquer_m1(arcs: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    res = arcs.apply(calculer_m1, axis=1, result_type="expand")
    res.columns = ["djma_m1", "pct_cam_m1", "djma_cam_m1", "n_segs_m1"]
    arcs = arcs.copy()
    arcs["djma_m1"]     = res["djma_m1"].astype("Int64")
    arcs["pct_cam_m1"]  = res["pct_cam_m1"]
    arcs["djma_cam_m1"] = res["djma_cam_m1"].astype("Int64")
    arcs["n_segs_m1"]   = res["n_segs_m1"].astype(int)
    return arcs


# ============================================================
# M2 — Pondération par longueur (nécessite trajets_segments_v4)
# ============================================================

def aggreger_m2(segs: gpd.GeoDataFrame) -> pd.DataFrame:
    """Calcule djma_m2 (moyenne pondérée par longueur) pour chaque arc."""
    resultats = []
    for id_arc, groupe in segs.groupby("ID_ARC"):
        vals_djma = groupe["djma_val"].dropna()
        if vals_djma.empty:
            resultats.append({"ID_ARC": id_arc, "djma_m2": None, "pct_cam_m2": None,
                               "djma_cam_m2": None, "n_segs_m2": 0})
            continue
        idx       = vals_djma.index
        poids     = groupe.loc[idx, "longueur_m"]
        total_p   = poids.sum()
        if total_p == 0:
            resultats.append({"ID_ARC": id_arc, "djma_m2": None, "pct_cam_m2": None,
                               "djma_cam_m2": None, "n_segs_m2": 0})
            continue
        djma_m2  = round(float(np.average(vals_djma, weights=poids)))
        vals_cam = groupe.loc[idx, "cam_val"].dropna()
        if not vals_cam.empty:
            pct_cam_m2 = round(float(np.average(vals_cam, weights=groupe.loc[vals_cam.index, "longueur_m"])), 1)
        else:
            pct_cam_m2 = None
        djma_cam_m2 = round(djma_m2 * pct_cam_m2 / 100) if pct_cam_m2 is not None else None
        resultats.append({"ID_ARC": id_arc, "djma_m2": djma_m2, "pct_cam_m2": pct_cam_m2,
                           "djma_cam_m2": djma_cam_m2, "n_segs_m2": len(vals_djma)})
    return pd.DataFrame(resultats)


def appliquer_m2(arcs: gpd.GeoDataFrame, segs: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    df = aggreger_m2(segs)
    arcs = arcs.merge(df, on="ID_ARC", how="left")
    arcs["djma_m2"]     = arcs["djma_m2"].astype("Int64")
    arcs["djma_cam_m2"] = arcs["djma_cam_m2"].astype("Int64")
    arcs["n_segs_m2"]   = arcs["n_segs_m2"].fillna(0).astype(int)
    return arcs


# ============================================================
# M3 — Pondération par type d'axe
# ============================================================

def calculer_m3(row: pd.Series) -> tuple:
    vals, poids = parser_paires_type(row.get("ids_segs_djma_val"), row.get("ids_segs_rtss_type"))
    if not vals:
        return None, None, None, 0
    total_p = sum(poids)
    djma_m3 = round(float(np.average(vals, weights=poids))) if total_p > 0 else round(float(np.mean(vals)))
    vals_cam, poids_cam = parser_paires_type(row.get("ids_segs_djma_val_cam"), row.get("ids_segs_rtss_type"))
    if vals_cam:
        total_pc   = sum(poids_cam)
        pct_cam_m3 = round(float(np.average(vals_cam, weights=poids_cam)), 1) if total_pc > 0 \
                     else round(float(np.mean(vals_cam)), 1)
    else:
        pct_cam_m3 = None
    djma_cam_m3 = round(djma_m3 * pct_cam_m3 / 100) if pct_cam_m3 is not None else None
    return djma_m3, pct_cam_m3, djma_cam_m3, len(vals)


def appliquer_m3(arcs: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    res = arcs.apply(calculer_m3, axis=1, result_type="expand")
    res.columns = ["djma_m3", "pct_cam_m3", "djma_cam_m3", "n_segs_m3"]
    arcs = arcs.copy()
    arcs["djma_m3"]     = res["djma_m3"].astype("Int64")
    arcs["pct_cam_m3"]  = res["pct_cam_m3"]
    arcs["djma_cam_m3"] = res["djma_cam_m3"].astype("Int64")
    arcs["n_segs_m3"]   = res["n_segs_m3"].astype(int)
    return arcs


# ============================================================
# M4 — Approximation 90e percentile : α·max + (1−α)·mean
# ============================================================

def calculer_m4(row: pd.Series) -> tuple:
    vals_djma = parser_valeurs(row.get("ids_segs_djma_val"))
    vals_cam  = parser_valeurs(row.get("ids_segs_djma_val_cam"))
    if not vals_djma:
        return None, None, None, 0
    djma_m4    = round(ALPHA_M4 * max(vals_djma) + (1 - ALPHA_M4) * float(np.mean(vals_djma)))
    pct_cam_m4 = round(ALPHA_M4 * max(vals_cam) + (1 - ALPHA_M4) * float(np.mean(vals_cam)), 1) \
                 if vals_cam else None
    djma_cam_m4 = round(djma_m4 * pct_cam_m4 / 100) if pct_cam_m4 is not None else None
    return djma_m4, pct_cam_m4, djma_cam_m4, len(vals_djma)


def appliquer_m4(arcs: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    res = arcs.apply(calculer_m4, axis=1, result_type="expand")
    res.columns = ["djma_m4", "pct_cam_m4", "djma_cam_m4", "n_segs_m4"]
    arcs = arcs.copy()
    arcs["djma_m4"]     = res["djma_m4"].astype("Int64")
    arcs["pct_cam_m4"]  = res["pct_cam_m4"]
    arcs["djma_cam_m4"] = res["djma_cam_m4"].astype("Int64")
    arcs["n_segs_m4"]   = res["n_segs_m4"].astype(int)
    return arcs


# ============================================================
# RÉSUMÉ PAR MÉTHODE
# ============================================================

def afficher_resume(gdf: gpd.GeoDataFrame, col: str, label: str) -> None:
    n_avec = gdf[col].notna().sum()
    n_sans = gdf[col].isna().sum()
    ok     = gdf[gdf[col].notna()]
    print(f"  {label:<8} : {n_avec:>3} arcs avec valeur  |  {n_sans:>2} sans")
    if n_avec > 0:
        print(f"           médiane={ok[col].median():.0f}  min={ok[col].min()}  max={ok[col].max()}")


# ============================================================
# PIPELINE PRINCIPAL
# ============================================================

def main() -> None:
    print("=" * 65)
    print("CALCUL DJMA — méthodes m1, m2, m3, m4 (v4)")
    print(f"  alpha m4 = {ALPHA_M4}")
    print("=" * 65)

    print(f"\n[1/5] Chargement {LAYER_ARCS}...")
    arcs = gpd.read_file(INPUT_FILE, layer=LAYER_ARCS)
    print(f"  {len(arcs)} arcs chargés")

    print(f"\n[2/5] Chargement {LAYER_SEGS}...")
    segs = gpd.read_file(INPUT_FILE, layer=LAYER_SEGS)
    segs["longueur_m"] = segs.geometry.length
    print(f"  {len(segs)} segments chargés")

    print("\n[3/5] Calcul m1 (moyenne simple)...")
    arcs = appliquer_m1(arcs)

    print("[3/5] Calcul m2 (pondération longueur)...")
    arcs = appliquer_m2(arcs, segs)

    print("[3/5] Calcul m3 (pondération type d'axe)...")
    arcs = appliquer_m3(arcs)

    print("[3/5] Calcul m4 (α·max + (1−α)·mean)...")
    arcs = appliquer_m4(arcs)

    print("\n[4/5] Résultats :")
    for col, label in [("djma_m1", "m1"), ("djma_m2", "m2"), ("djma_m3", "m3"), ("djma_m4", "m4")]:
        afficher_resume(arcs, col, label)

    print(f"\n[5/5] Export → {OUTPUT_FILE}  (layer : {OUTPUT_LAYER})")
    arcs.to_file(OUTPUT_FILE, layer=OUTPUT_LAYER, driver="GPKG")
    print("  Terminé.")


if __name__ == "__main__":
    main()
