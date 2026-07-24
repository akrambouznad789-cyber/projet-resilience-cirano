"""
generer_figures_resultats.py
=============================
Projet CIRANO — Figures statiques pour la présentation du projet (README)

Génère 6 PNG dans figures/ :
  1. validation_randomforest.png : validation croisée du RandomForest (%cam),
     prédit vs réel, avec R² et RMSE
  2. randomforest_subsets.png    : la même validation croisée, facettée par type
     de route — le "subset" que le modèle apprend à distinguer
  3. distribution_djma.png       : distribution des DJMA par méthode (m1-m4)
  4. comparaison_methodes.png    : comparaison des 4 méthodes par arc
  5. carte_reseau_djma.png       : carte statique du réseau, arcs colorés par DJMA (m4)
  6. carte_resultats.png         : carte statique OK (vert) / échec (rouge, orange)
"""

from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LogNorm
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_squared_error, r2_score
from sklearn.model_selection import KFold, cross_val_predict

from completion_donnees_randomforest import (
    CATEGORIES_ROUTE,
    FEATURES_SUPPLEMENTAIRES,
    ROUTE_DUMMY_COLS,
    construire_features_supplementaires,
)

RACINE      = Path(__file__).resolve().parent.parent
FIG_DIR     = RACINE / "figures"

DEBITS_FILE = RACINE / "data" / "processed" / "debits_completes.gpkg"
DEBITS_LAYER = "debits_completes"

RESEAU_FILE  = RACINE / "data" / "processed" / "graphe_routier_djma.gpkg"
RESEAU_LAYER = "arcs_enrichis_djma"

ARCS_FILE   = RACINE / "data" / "processed" / "graphe_routier.gpkg"
ARCS_LAYER  = "arcs_enrichis"

N_ANNEES  = 10
DJMA_COLS = [f"val_djma_annee_{i}" for i in range(1, N_ANNEES + 1)]
CAM_COLS  = [f"val_cam_annee_{i}"  for i in range(1, N_ANNEES + 1)]

# Palette (cf. skill dataviz — palette validée)
BLEU_450   = "#2a78d6"   # séquentiel / série 1
VERT_STAT  = "#0ca30c"   # statut : ok
SERIEUX    = "#ec835a"   # statut : aucun_djma
CRITIQUE   = "#d03b3b"   # statut : hors_quebec
GRIS_AXE   = "#898781"
COULEURS_M = {"m1": "#2a78d6", "m2": "#008300", "m3": "#e87ba4", "m4": "#eda100"}

# Couleurs par type de route — DOIT rester synchronisé avec COULEURS_CLASSE /
# COULEUR_AUTRE dans generer_figure_donnees.py (identité visuelle cohérente
# dans tout le README). Redéfini ici plutôt qu'importé pour ne pas déclencher
# les effets de bord de ce module au chargement (téléchargement de polices,
# plt.rcParams global en Arimo).
COULEURS_ROUTE = {
    "Autoroute":   "#f2a0b5",
    "Nationale":   "#8fa8f0",
    "Régionale":   "#7ecfb0",
    "Collectrice": "#b6a8d9",
    "Autre":       "#cfcabd",
}

plt.rcParams.update({
    "font.family": "sans-serif",
    "axes.edgecolor": GRIS_AXE,
    "axes.labelcolor": "#0b0b0b",
    "text.color": "#0b0b0b",
    "xtick.color": GRIS_AXE,
    "ytick.color": GRIS_AXE,
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "savefig.facecolor": "white",
})


def _validation_croisee_randomforest() -> dict:
    """Validation croisée (5-fold) du RandomForest utilisé pour compléter %cam.

    Calculée une seule fois ; partagée par figure_validation_randomforest()
    (score global) et figure_randomforest_subsets() (le même résultat,
    facetté par type de route) pour ne pas entraîner le modèle deux fois.
    """
    gdf = gpd.read_file(DEBITS_FILE, layer=DEBITS_LAYER)

    mask_train = (
        gdf["methode_djma"].isin(["complet", "interpolation", "extrapolation"])
        & gdf["methode_cam"].isin(["complet", "interpolation", "extrapolation"])
    )
    features = construire_features_supplementaires(gdf)
    X = np.hstack([
        gdf.loc[mask_train, DJMA_COLS].values,
        features.loc[mask_train, FEATURES_SUPPLEMENTAIRES].values,
    ])
    Y = gdf.loc[mask_train, CAM_COLS].values

    type_route = features.loc[mask_train, ROUTE_DUMMY_COLS].idxmax(axis=1).str.replace("route_", "")

    modele = RandomForestRegressor(n_estimators=50, random_state=42, n_jobs=-1)
    kf = KFold(n_splits=5, shuffle=True, random_state=42)
    y_pred = cross_val_predict(modele, X, Y, cv=kf)

    y_true_flat = Y.flatten()
    y_pred_flat = y_pred.flatten()
    # une ligne = 10 années consécutives dans .flatten() (ordre C) : répéter le
    # type de route 10x aligne exactement chaque valeur annuelle sur son segment
    type_route_flat = np.repeat(type_route.values, N_ANNEES)

    return {
        "y_true": y_true_flat, "y_pred": y_pred_flat, "type_route": type_route_flat,
        "r2": r2_score(y_true_flat, y_pred_flat),
        "rmse": mean_squared_error(y_true_flat, y_pred_flat) ** 0.5,
        "n_segments": int(mask_train.sum()), "n_features": X.shape[1],
    }


def figure_validation_randomforest(cv: dict) -> None:
    """Validation croisée (5-fold) du RandomForest utilisé pour compléter %cam."""
    y_true_flat, y_pred_flat = cv["y_true"], cv["y_pred"]

    rng = np.random.default_rng(42)
    if len(y_true_flat) > 3000:
        idx = rng.choice(len(y_true_flat), 3000, replace=False)
        y_true_flat, y_pred_flat = y_true_flat[idx], y_pred_flat[idx]

    fig, ax = plt.subplots(figsize=(6, 6))
    ax.scatter(y_true_flat, y_pred_flat, s=10, alpha=0.35, color=BLEU_450, linewidths=0)
    lims = [0, max(y_true_flat.max(), y_pred_flat.max()) * 1.05]
    ax.plot(lims, lims, color=GRIS_AXE, linewidth=1.5, linestyle="--", label="Prédiction parfaite")
    ax.set_xlim(lims); ax.set_ylim(lims)
    ax.set_xlabel("% camions réel")
    ax.set_ylabel("% camions prédit (RandomForest, validation croisée 5-fold)")
    ax.set_title(f"Validation du RandomForest — R² = {cv['r2']:.3f}, RMSE = {cv['rmse']:.2f} pts")
    ax.legend(frameon=False, loc="upper left")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "validation_randomforest.png", dpi=150)
    plt.close(fig)
    print(f"  validation_randomforest.png  (R²={cv['r2']:.3f}, RMSE={cv['rmse']:.2f}, "
          f"n={cv['n_segments']} segments, {cv['n_features']} features)")


def figure_randomforest_subsets(cv: dict) -> None:
    """La même validation croisée que ci-dessus, facettée par type de route.

    Le type de route est la feature la plus structurante du modèle enrichi :
    ce petit multiple montre que le RandomForest sépare effectivement ses
    erreurs selon ce "subset", plutôt que de prédire une masse indifférenciée.
    """
    y_true, y_pred, type_route = cv["y_true"], cv["y_pred"], cv["type_route"]
    lims = [0, max(y_true.max(), y_pred.max()) * 1.05]

    fig, axes = plt.subplots(2, 3, figsize=(13, 8.5))
    axes_flat = axes.flatten()
    rng = np.random.default_rng(42)

    for ax, categorie in zip(axes_flat, CATEGORIES_ROUTE):
        masque = type_route == categorie
        yt, yp = y_true[masque], y_pred[masque]
        if len(yt) > 1500:
            idx = rng.choice(len(yt), 1500, replace=False)
            yt, yp = yt[idx], yp[idx]

        couleur = COULEURS_ROUTE[categorie]
        ax.scatter(yt, yp, s=10, alpha=0.35, color=couleur, linewidths=0)
        ax.plot(lims, lims, color=GRIS_AXE, linewidth=1.2, linestyle="--")
        ax.set_xlim(lims); ax.set_ylim(lims)
        r2_sub = r2_score(y_true[masque], y_pred[masque])
        ax.set_title(f"{categorie}  (n={masque.sum()}, R²={r2_sub:.2f})", fontsize=10.5)
        ax.spines[["top", "right"]].set_visible(False)
        ax.tick_params(labelsize=8.5)

    for ax in axes_flat[len(CATEGORIES_ROUTE):]:
        ax.set_axis_off()

    fig.supxlabel("% camions réel", fontsize=10.5)
    fig.supylabel("% camions prédit", fontsize=10.5)
    fig.suptitle("RandomForest — prédictions par type de route (les \"subsets\" du modèle)",
               fontsize=13, fontweight="bold")
    fig.tight_layout(rect=(0.01, 0.01, 1, 0.95))
    fig.savefig(FIG_DIR / "randomforest_subsets.png", dpi=150)
    plt.close(fig)
    print("  randomforest_subsets.png")


def figure_distribution_djma() -> None:
    gdf = gpd.read_file(RESEAU_FILE, layer=RESEAU_LAYER)
    cols = ["djma_m1", "djma_m2", "djma_m3", "djma_m4"]
    data = [gdf[c].dropna().values for c in cols]

    fig, ax = plt.subplots(figsize=(7, 5))
    bp = ax.boxplot(data, tick_labels=["m1", "m2", "m3", "m4"], patch_artist=True, widths=0.5)
    for patch, col in zip(bp["boxes"], cols):
        patch.set_facecolor(COULEURS_M[col.replace("djma_", "")])
        patch.set_alpha(0.75)
    for median in bp["medians"]:
        median.set_color("#0b0b0b")

    ax.set_yscale("log")
    ax.set_ylabel("DJMA (véh./jour, échelle log)")
    ax.set_title("Distribution du DJMA agrégé par méthode (285 arcs valides)")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "distribution_djma.png", dpi=150)
    plt.close(fig)
    print("  distribution_djma.png")


def figure_comparaison_methodes() -> None:
    gdf = gpd.read_file(RESEAU_FILE, layer=RESEAU_LAYER)
    cols = ["djma_m1", "djma_m2", "djma_m3", "djma_m4"]
    valides = gdf.dropna(subset=cols).sort_values("djma_m1")

    fig, ax = plt.subplots(figsize=(9, 5))
    x = np.arange(len(valides))
    for col in cols:
        ax.plot(x, valides[col].values, color=COULEURS_M[col.replace("djma_", "")],
                linewidth=1.5, label=col.replace("djma_", ""), alpha=0.9)

    ax.set_yscale("log")
    ax.set_xlabel("Arcs (triés par djma_m1)")
    ax.set_ylabel("DJMA (véh./jour, échelle log)")
    ax.set_title("Comparaison des 4 méthodes DJMA, par arc")
    ax.legend(frameon=False, ncol=4, loc="upper left")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "comparaison_methodes.png", dpi=150)
    plt.close(fig)
    print("  comparaison_methodes.png")


def figure_carte_reseau_djma() -> None:
    djma = gpd.read_file(RESEAU_FILE, layer=RESEAU_LAYER)[["ID_ARC", "djma_m4"]]
    arcs = gpd.read_file(ARCS_FILE, layer=ARCS_LAYER)[["ID_ARC", "geometry"]]
    gdf = arcs.merge(djma, on="ID_ARC", how="left")

    fig, ax = plt.subplots(figsize=(8, 8))
    sans_valeur = gdf[gdf["djma_m4"].isna()]
    avec_valeur = gdf[gdf["djma_m4"].notna()]
    sans_valeur.plot(ax=ax, color="#e1e0d9", linewidth=0.8)
    norm = LogNorm(vmin=avec_valeur["djma_m4"].min(), vmax=avec_valeur["djma_m4"].max())
    avec_valeur.plot(ax=ax, column="djma_m4", cmap="Blues", norm=norm, linewidth=1.8,
                      legend=True, legend_kwds={"label": "DJMA — méthode m4 (véh./jour, échelle log)", "shrink": 0.6})
    ax.set_title("Réseau routier — DJMA par arc (méthode m4)")
    ax.set_axis_off()
    fig.tight_layout()
    fig.savefig(FIG_DIR / "carte_reseau_djma.png", dpi=150)
    plt.close(fig)
    print("  carte_reseau_djma.png")


def figure_carte_resultats() -> None:
    arcs = gpd.read_file(ARCS_FILE, layer=ARCS_LAYER)[["ID_ARC", "statut", "geometry"]]

    couleurs = {"ok": VERT_STAT, "aucun_djma": SERIEUX, "hors_quebec": CRITIQUE}
    etiquettes = {"ok": "OK (285)", "aucun_djma": "Aucun DJMA (19)", "hors_quebec": "Hors Québec (3)"}

    fig, ax = plt.subplots(figsize=(8, 8))
    for statut, couleur in couleurs.items():
        sous_ensemble = arcs[arcs["statut"] == statut]
        sous_ensemble.plot(ax=ax, color=couleur, linewidth=1.6, label=etiquettes[statut])

    ax.set_title("Résultats du routage — 285/307 arcs OK (92,8 %)")
    ax.legend(frameon=False, loc="lower right")
    ax.set_axis_off()
    fig.tight_layout()
    fig.savefig(FIG_DIR / "carte_resultats.png", dpi=150)
    plt.close(fig)
    print("  carte_resultats.png")


def main() -> None:
    FIG_DIR.mkdir(exist_ok=True)
    print("Génération des figures...")
    cv = _validation_croisee_randomforest()
    figure_validation_randomforest(cv)
    figure_randomforest_subsets(cv)
    figure_distribution_djma()
    figure_comparaison_methodes()
    figure_carte_reseau_djma()
    figure_carte_resultats()
    print(f"\nTerminé — figures dans {FIG_DIR}")


if __name__ == "__main__":
    main()
