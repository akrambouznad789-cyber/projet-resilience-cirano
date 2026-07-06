"""
comparer_methodes_djma.py
=========================
Projet CIRANO II — Comparaison des méthodes DJMA m1 à m4

Charge les 4 couches produites, joint les colonnes djma_m{1..4} sur ID_ARC,
affiche les statistiques descriptives et identifie les arcs divergents.

SORTIE CONSOLE
--------------
  - Stats descriptives par méthode
  - Matrice de corrélation
  - Tableau des arcs avec écart relatif max > seuil (défaut 30 %)
"""

import os
import geopandas as gpd
import pandas as pd
import numpy as np
import warnings

warnings.filterwarnings("ignore")

BASE     = os.path.expanduser("~/projects/projet-resilience-cirano/data/processed")
SEUIL_ECART_PCT = 30  # % d'écart entre min et max des 4 méthodes pour signaler un arc

METHODES = {
    "m1": ("graphe_routier_v2_djma_m1.gpkg", "arcs_enrichis_v2_djma_m1", "djma_m1"),
    "m2": ("graphe_routier_v2_djma_m2.gpkg", "arcs_enrichis_v2_djma_m2", "djma_m2"),
    "m3": ("graphe_routier_v2_djma_m3.gpkg", "arcs_enrichis_v2_djma_m3", "djma_m3"),
    "m4": ("graphe_routier_v2_djma_m4.gpkg", "arcs_enrichis_v2_djma_m4", "djma_m4"),
}

ID_COL = "ID_ARC"


def charger_methode(nom: str, fichier: str, layer: str, col_djma: str) -> pd.DataFrame:
    path = os.path.join(BASE, fichier)
    gdf = gpd.read_file(path, layer=layer)
    cols = [ID_COL, col_djma]
    return gdf[cols].rename(columns={col_djma: nom})


def main() -> None:
    print("=" * 65)
    print("COMPARAISON DJMA — méthodes m1 à m4")
    print("=" * 65)

    # --- Chargement & jointure ---
    dfs = []
    for nom, (fichier, layer, col) in METHODES.items():
        df = charger_methode(nom, fichier, layer, col)
        dfs.append(df)

    combined = dfs[0]
    for df in dfs[1:]:
        combined = combined.merge(df, on=ID_COL, how="outer")

    cols_m = list(METHODES.keys())

    print(f"\n{len(combined)} arcs au total\n")

    # --- Stats descriptives ---
    print("-" * 65)
    print("STATISTIQUES DESCRIPTIVES (véh./jour)")
    print("-" * 65)
    stats = combined[cols_m].describe().loc[["count", "mean", "50%", "min", "max", "std"]]
    stats.index = ["N valide", "Moyenne", "Médiane", "Min", "Max", "Écart-type"]
    print(stats.map(lambda x: f"{x:,.0f}" if pd.notna(x) else "NA").to_string())

    # --- Corrélation ---
    print("\n" + "-" * 65)
    print("MATRICE DE CORRÉLATION (Pearson)")
    print("-" * 65)
    corr = combined[cols_m].corr()
    print(corr.map(lambda x: f"{x:.4f}").to_string())

    # --- Arcs divergents ---
    print("\n" + "-" * 65)
    print(f"ARCS DIVERGENTS (écart relatif max > {SEUIL_ECART_PCT} %)")
    print("  Écart relatif = (max_méthodes − min_méthodes) / moyenne_méthodes × 100")
    print("-" * 65)

    data = combined[cols_m].copy()
    combined["_min"]  = data.min(axis=1)
    combined["_max"]  = data.max(axis=1)
    combined["_mean"] = data.mean(axis=1)
    combined["ecart_pct"] = (
        (combined["_max"] - combined["_min"]) / combined["_mean"] * 100
    ).round(1)

    divergents = combined[combined["ecart_pct"] > SEUIL_ECART_PCT].sort_values(
        "ecart_pct", ascending=False
    )

    if divergents.empty:
        print(f"  Aucun arc avec écart > {SEUIL_ECART_PCT} % — convergence excellente.")
    else:
        affichage = divergents[[ID_COL] + cols_m + ["ecart_pct"]].copy()
        for col in cols_m:
            affichage[col] = affichage[col].apply(
                lambda x: f"{x:,.0f}" if pd.notna(x) else "NA"
            )
        affichage["ecart_pct"] = affichage["ecart_pct"].apply(
            lambda x: f"{x:.1f} %" if pd.notna(x) else "NA"
        )
        print(affichage.to_string(index=False))

    print(f"\n  {len(divergents)} arc(s) divergent(s) sur {len(combined)}")

    # --- Tableau complet trié par ID_ARC ---
    print("\n" + "-" * 65)
    print("TABLEAU COMPLET — tous les arcs (trié par ID_ARC)")
    print("-" * 65)
    affichage_full = combined[[ID_COL] + cols_m + ["ecart_pct"]].sort_values(ID_COL).copy()
    for col in cols_m:
        affichage_full[col] = affichage_full[col].apply(
            lambda x: f"{x:,.0f}" if pd.notna(x) else "NA"
        )
    affichage_full["ecart_pct"] = affichage_full["ecart_pct"].apply(
        lambda x: f"{x:.1f} %" if pd.notna(x) else "NA"
    )
    print(affichage_full.to_string(index=False))


if __name__ == "__main__":
    main()
