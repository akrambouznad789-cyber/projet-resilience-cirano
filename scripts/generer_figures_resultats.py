"""
generer_figures_resultats.py
=============================
Projet CIRANO — Figures statiques pour la présentation du projet (README)

Génère 5 PNG dans figures/ :
  1. validation_randomforest.png     : validation croisée du RandomForest (%cam),
     prédit vs réel, avec R² et RMSE
  2. randomforest_subsets.png        : la même validation croisée, facettée par type
     de route — le "subset" que le modèle apprend à distinguer
  3. carte_3d_divergence_methodes.png : réseau routier empilé en 4 plateaux (m1-m4),
     colorés par DJMA — épaisseur des traits et connecteurs verticaux mettant en
     évidence les arcs où les 4 méthodes divergent le plus
  4. carte_reseau_djma.png          : carte statique du réseau, arcs colorés par DJMA (m4)
  5. carte_resultats.png            : carte statique OK (vert) / échec (rouge, orange)
"""

from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LogNorm
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401 — enregistre la projection 3D
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


def figure_carte_3d_divergence_methodes() -> None:
    """Réseau routier empilé en 4 plateaux (m1 à m4, bas → haut).

    Couleur = DJMA de la méthode (rampe séquentielle Blues, échelle log commune
    aux 4 plateaux : une teinte qui change verticalement au même endroit EST le
    signal de divergence). Épaisseur du trait (identique sur les 4 plateaux) et
    connecteurs verticaux = écart relatif entre méthodes — "densité" sur les
    arcs les plus hétérogènes, remplace les anciennes figures distribution_djma
    / comparaison_methodes (jugées redondantes, aucune information spatiale).
    """
    cols = ["djma_m1", "djma_m2", "djma_m3", "djma_m4"]
    djma = gpd.read_file(RESEAU_FILE, layer=RESEAU_LAYER)[["ID_ARC"] + cols]
    arcs = gpd.read_file(ARCS_FILE, layer=ARCS_LAYER)[["ID_ARC", "VILLE_A", "VILLE_B", "geometry"]]
    gdf = arcs.merge(djma, on="ID_ARC", how="left")

    valides = gdf.dropna(subset=cols).copy()
    valides["ecart_pct"] = (
        (valides[cols].max(axis=1) - valides[cols].min(axis=1)) / valides[cols].mean(axis=1) * 100
    )
    sans_valeur = gdf[gdf[cols].isna().any(axis=1)]

    vmin = valides[cols].min().min()
    vmax = valides[cols].max().max()
    norm = LogNorm(vmin=vmin, vmax=vmax)
    cmap = plt.get_cmap("Blues")

    def largeur(ecart_pct: float) -> float:
        return 0.5 + min(ecart_pct, 150.0) / 150.0 * 3.5

    ACCENT = COULEURS_M["m4"]
    N_TOP  = 10
    top_divergents = valides.nlargest(N_TOP, "ecart_pct")

    fig = plt.figure(figsize=(10, 7.5))
    ax = fig.add_subplot(111, projection="3d")

    for _, row in sans_valeur.iterrows():
        xs, ys = row.geometry.xy
        for z in range(4):
            ax.plot(xs, ys, [z] * len(xs), color="#e1e0d9", linewidth=0.4, alpha=0.5)

    for _, row in valides.iterrows():
        xs, ys = row.geometry.xy
        lw = largeur(row["ecart_pct"])
        for z, col in enumerate(cols):
            couleur = cmap(norm(row[col]))
            ax.plot(xs, ys, [z] * len(xs), color=couleur, linewidth=lw, alpha=0.88,
                     solid_capstyle="round")

    for _, row in top_divergents.iterrows():
        cx, cy = row.geometry.centroid.x, row.geometry.centroid.y
        ax.plot([cx, cx], [cy, cy], [0, 3], color=ACCENT, linewidth=0.8,
                 alpha=0.4, linestyle=(0, (1, 1.5)))

    xmin, ymin = gdf.total_bounds[0], gdf.total_bounds[1]
    for z in range(4):
        ax.text(xmin, ymin, z, f"m{z + 1}", fontsize=11, fontweight="bold", color=GRIS_AXE)

    ax.set_axis_off()
    ax.set_zlim(-0.2, 3.3)
    ax.view_init(elev=24, azim=-60)
    try:
        ax.set_box_aspect((1, 1, 0.55), zoom=1.6)
    except TypeError:
        ax.set_box_aspect((1, 1, 0.55))

    sm = plt.cm.ScalarMappable(cmap=cmap, norm=norm)
    cbar = fig.colorbar(sm, ax=ax, shrink=0.4, pad=0.0)
    cbar.set_label("DJMA (véh./jour, échelle log)")

    ax.set_title("Superposition des 4 méthodes DJMA — où les écarts sont les plus grands",
                 fontsize=12.5)

    top3 = top_divergents.nlargest(3, "ecart_pct")
    lignes_top3 = "  |  ".join(
        f"{row['VILLE_A']}–{row['VILLE_B']} ({row['ecart_pct']:.0f} %)" for _, row in top3.iterrows()
    )
    fig.text(0.5, 0.05,
             f"Épaisseur ∝ écart relatif entre méthodes  |  pointillés = {N_TOP} arcs les plus divergents\n"
             f"Écarts les plus marqués : {lignes_top3}",
             ha="center", fontsize=8.5, color=GRIS_AXE)

    chemin = FIG_DIR / "carte_3d_divergence_methodes.png"
    fig.savefig(chemin, dpi=150, bbox_inches="tight")
    plt.close(fig)
    _recadrer_marges_blanches(chemin)
    print("  carte_3d_divergence_methodes.png")


def _recadrer_marges_blanches(chemin: Path, pad: int = 20) -> None:
    """Recadre les marges blanches excédentaires — mplot3d laisse une grande
    zone vide autour de la scène 3D même après bbox_inches='tight'."""
    from PIL import Image, ImageChops

    img = Image.open(chemin).convert("RGB")
    fond = Image.new("RGB", img.size, (255, 255, 255))
    bbox = ImageChops.difference(img, fond).getbbox()
    if bbox is None:
        return
    gauche, haut, droite, bas = bbox
    gauche = max(gauche - pad, 0)
    haut   = max(haut - pad, 0)
    droite = min(droite + pad, img.size[0])
    bas    = min(bas + pad, img.size[1])
    img.crop((gauche, haut, droite, bas)).save(chemin)


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
    figure_carte_3d_divergence_methodes()
    figure_carte_reseau_djma()
    figure_carte_resultats()
    print(f"\nTerminé — figures dans {FIG_DIR}")


if __name__ == "__main__":
    main()
