"""
comparaison_methodes_djma.py
=============================
Projet CIRANO II — Comparaison des méthodes DJMA m1 à m4

Charge la couche unique arcs_enrichis_djma (colonnes djma_m1..m4 déjà
combinées par calcul_djma_methodes.py), affiche les statistiques
descriptives et identifie les arcs divergents.

SORTIE CONSOLE
--------------
  - Stats descriptives par méthode
  - Matrice de corrélation
  - Tableau des arcs avec écart relatif max > seuil (défaut 30 %)
"""

from pathlib import Path

import geopandas as gpd
import pandas as pd
import warnings

warnings.filterwarnings("ignore")

RACINE      = Path(__file__).resolve().parent.parent
INPUT_FILE  = RACINE / "data" / "processed" / "graphe_routier_djma.gpkg"
INPUT_LAYER = "arcs_enrichis_djma"

SEUIL_ECART_PCT = 30  # % d'écart entre min et max des 4 méthodes pour signaler un arc

ID_COL = "ID_ARC"
COLS_M = ["djma_m1", "djma_m2", "djma_m3", "djma_m4"]


def charger_arcs() -> pd.DataFrame:
    gdf = gpd.read_file(INPUT_FILE, layer=INPUT_LAYER)
    return gdf[[ID_COL] + COLS_M]


def main() -> None:
    print("=" * 65)
    print("COMPARAISON DJMA — méthodes m1 à m4")
    print("=" * 65)

    combined = charger_arcs()
    print(f"\n{len(combined)} arcs au total\n")

    # --- Stats descriptives ---
    print("-" * 65)
    print("STATISTIQUES DESCRIPTIVES (véh./jour)")
    print("-" * 65)
    stats = combined[COLS_M].describe().loc[["count", "mean", "50%", "min", "max", "std"]]
    stats.index = ["N valide", "Moyenne", "Médiane", "Min", "Max", "Écart-type"]
    print(stats.map(lambda x: f"{x:,.0f}" if pd.notna(x) else "NA").to_string())

    # --- Corrélation ---
    print("\n" + "-" * 65)
    print("MATRICE DE CORRÉLATION (Pearson)")
    print("-" * 65)
    corr = combined[COLS_M].corr()
    print(corr.map(lambda x: f"{x:.4f}").to_string())

    # --- Arcs divergents ---
    print("\n" + "-" * 65)
    print(f"ARCS DIVERGENTS (écart relatif max > {SEUIL_ECART_PCT} %)")
    print("  Écart relatif = (max_méthodes − min_méthodes) / moyenne_méthodes × 100")
    print("-" * 65)

    data = combined[COLS_M].copy()
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
        affichage = divergents[[ID_COL] + COLS_M + ["ecart_pct"]].copy()
        for col in COLS_M:
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
    affichage_full = combined[[ID_COL] + COLS_M + ["ecart_pct"]].sort_values(ID_COL).copy()
    for col in COLS_M:
        affichage_full[col] = affichage_full[col].apply(
            lambda x: f"{x:,.0f}" if pd.notna(x) else "NA"
        )
    affichage_full["ecart_pct"] = affichage_full["ecart_pct"].apply(
        lambda x: f"{x:.1f} %" if pd.notna(x) else "NA"
    )
    print(affichage_full.to_string(index=False))


if __name__ == "__main__":
    main()
