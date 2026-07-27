"""
generer_figures_resultats.py
=============================
Projet CIRANO — Figures statiques pour la présentation du projet (README)

Génère 5 PNG dans figures/ :
  1. validation_randomforest.png     : validation croisée du RandomForest (%cam),
     prédit vs réel, avec R² et RMSE
  2. randomforest_subsets.png        : la même validation croisée, facettée par type
     de route — le "subset" que le modèle apprend à distinguer
  3. carte_divergence_methodes.png   : carte 2D (choropleth) sur fond de carte du
     Québec — couleur (pâle→ambre→rouge) = écart relatif entre les 4 méthodes DJMA
  4. carte_reseau_resultats.png      : carte du réseau enrichi — DJMA par arc (m4)
     et couverture du routage (arcs en échec distingués par cause) sur fond de
     carte du Québec — fusionne les anciennes carte_reseau_djma.png/carte_resultats.png
  5. carte_montreal_resultats.png    : zoom Grand Montréal de la carte précédente —
     rend lisibles les échecs intraurbains, trop denses à l'échelle du Québec
"""

from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap, LogNorm, Normalize
from matplotlib.lines import Line2D
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

# Fond de carte 3D — mêmes fichiers Natural Earth déjà mis en cache par
# generer_figure_donnees.py (pas d'import croisé, cf. COULEURS_ROUTE ci-dessous).
DIR_REFERENCE = RACINE / "data" / "reference"
PATH_FOND_ADMIN = DIR_REFERENCE / "ne_50m_admin_1_states_provinces.zip"
PATH_FOND_OCEAN = DIR_REFERENCE / "ne_50m_ocean.zip"
PATH_FOND_LACS  = DIR_REFERENCE / "ne_50m_lakes.zip"
MARGE_M = 20_000

N_ANNEES  = 10
DJMA_COLS = [f"val_djma_annee_{i}" for i in range(1, N_ANNEES + 1)]
CAM_COLS  = [f"val_cam_annee_{i}"  for i in range(1, N_ANNEES + 1)]

# Palette (cf. skill dataviz — palette validée)
BLEU_450   = "#2a78d6"   # séquentiel / série 1
CRITIQUE   = "#d03b3b"   # rouge (carte 3D — écart de méthode le plus fort)
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

# Fond de carte 2D (mêmes teintes que charger_fond_geographique() dans
# generer_figure_donnees.py — redéfini ici pour la même raison que COULEURS_ROUTE
# ci-dessus : pas d'import croisé, pas d'effet de bord police/rcParams).
FOND_TERRE     = "#f1efe6"
EAU            = "#bcdcf2"
FOND_BORDURE   = "#ddd9cc"
QUEBEC_BORDURE = "#9aa5b1"

# Rampe séquentielle pastel pour le DJMA (carte_reseau_resultats.png) — prolonge
# BLEU_450 (déjà la série "séquentielle" de ce fichier) plutôt que le cmap "Blues"
# générique de matplotlib, pour rester dans la même identité visuelle que le reste
# du README.
CMAP_DJMA = LinearSegmentedColormap.from_list("cirano_djma", ["#dce8f7", BLEU_450, "#0d2d52"])

# Couleurs d'échec — orange clair, teinte opposée à la rampe DJMA (bleu) sur le
# cercle chromatique : contraste net avec les arcs enrichis, sans tomber dans le
# rouge/vert saturé "feu de circulation" (cf. feedback_dataviz_style).
ECHEC_INTRA   = "#f2a765"   # échec — aucune station MTQ à proximité (intraurbain)
ECHEC_HORS_QC = "#c96f2e"   # échec — tracé hors territoire québécois (plus soutenu, peu nombreux)

# Rayon du zoom Grand Montréal (carte_montreal_resultats.png), centré sur le nœud
# "Montréal" — capture la quasi-totalité des échecs intraurbains (île + couronnes
# nord/sud), qui se tassent en un fouillis illisible à l'échelle du Québec.
RAYON_MTL_M = 55_000

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


def _charger_fond_quebec(bbox: tuple) -> dict | None:
    """Fond de carte Québec (Natural Earth) — même fichiers que ceux mis en cache
    par generer_figure_donnees.py, relus ici sans les importer.

    Ne réutilise pas charger_fond_geographique() de generer_figure_donnees.py :
    l'importer déclencherait le téléchargement de police et le rcParams global
    Arimo de ce module (effets de bord déjà évités ici, cf. COULEURS_ROUTE).
    Relit les mêmes fichiers déjà mis en cache par ce script — aucun accès
    réseau si generer_figure_donnees.py a déjà tourné une fois.
    """
    if not (PATH_FOND_ADMIN.exists() and PATH_FOND_OCEAN.exists() and PATH_FOND_LACS.exists()):
        return None
    from shapely.geometry import box
    minx, miny, maxx, maxy = bbox
    fenetre = box(minx, miny, maxx, maxy)

    fond   = gpd.read_file(PATH_FOND_ADMIN).to_crs("EPSG:32198").cx[minx:maxx, miny:maxy]
    quebec = gpd.clip(fond[fond["name"] == "Québec"], fenetre)
    autres = gpd.clip(fond[fond["name"] != "Québec"], fenetre)
    ocean  = gpd.clip(gpd.read_file(PATH_FOND_OCEAN).to_crs("EPSG:32198").cx[minx:maxx, miny:maxy], fenetre)
    lacs   = gpd.clip(gpd.read_file(PATH_FOND_LACS).to_crs("EPSG:32198").cx[minx:maxx, miny:maxy], fenetre)
    return {"quebec": quebec, "autres": autres, "ocean": ocean, "lacs": lacs}


def _tracer_fond(ax, fond: dict | None) -> None:
    """Trace le fond de carte neutre (terre crème, contour Québec marqué, eau
    bleu clair) — factorisé, réutilisé par les 3 cartes 2D de ce fichier."""
    if fond is None:
        return
    fond["ocean"].plot(ax=ax, color=EAU, linewidth=0, zorder=0)
    fond["autres"].plot(ax=ax, color=FOND_TERRE, edgecolor=FOND_BORDURE, linewidth=0.6, zorder=0)
    fond["quebec"].plot(ax=ax, color=FOND_TERRE, edgecolor=QUEBEC_BORDURE, linewidth=1.1, zorder=0)
    fond["lacs"].plot(ax=ax, color=EAU, linewidth=0, zorder=0)


def figure_carte_divergence_methodes() -> None:
    """Carte 2D : écart relatif entre les 4 méthodes DJMA, par arc.

    Remplace la V2 en 3D (prismes façon ax.bar3d) — jugée trop difficile à lire :
    les hauteurs de colonnes en perspective se comparent mal d'un arc à l'autre.
    Un choropleth 2D classique (couleur du tracé = écart, réseau complet en trame
    de fond pour le contexte) va directement à l'essentiel.
    """
    cols = ["djma_m1", "djma_m2", "djma_m3", "djma_m4"]
    djma = gpd.read_file(RESEAU_FILE, layer=RESEAU_LAYER)[["ID_ARC"] + cols]
    arcs = gpd.read_file(ARCS_FILE, layer=ARCS_LAYER)[["ID_ARC", "VILLE_A", "VILLE_B", "geometry"]]
    gdf = arcs.merge(djma, on="ID_ARC", how="left")

    valides = gdf.dropna(subset=cols).copy()
    valides["ecart_pct"] = (
        (valides[cols].max(axis=1) - valides[cols].min(axis=1)) / valides[cols].mean(axis=1) * 100
    )

    minx, miny, maxx, maxy = gdf.total_bounds
    span_x, span_y = (maxx - minx), (maxy - miny)
    bbox = (minx - MARGE_M, miny - MARGE_M, maxx + MARGE_M, maxy + MARGE_M)
    fond = _charger_fond_quebec(bbox)

    # Jaune pâle (pas la teinte exacte du fond, pour rester une ligne visible) →
    # ambre → rouge : même narration que la V1 3D ("un arc qui s'accorde reste
    # discret, un arc qui diverge fortement ressort"), sans reposer sur la hauteur.
    PALE  = "#f6e9c8"
    AMBRE = COULEURS_M["m4"]
    ROUGE = CRITIQUE
    cmap_divergence = LinearSegmentedColormap.from_list("cirano_divergence", [PALE, AMBRE, ROUGE])
    norm = Normalize(vmin=0, vmax=valides["ecart_pct"].max())

    fig, ax = plt.subplots(figsize=(9.5, 9.5 * span_y / span_x + 1.1))
    _tracer_fond(ax, fond)

    gdf.plot(ax=ax, color=GRIS_AXE, linewidth=0.5, alpha=0.3, zorder=1)  # réseau complet, contexte
    valides.plot(ax=ax, column="ecart_pct", cmap=cmap_divergence, norm=norm, linewidth=2.1, zorder=2)

    ax.set_xlim(bbox[0], bbox[2]); ax.set_ylim(bbox[1], bbox[3])
    ax.set_aspect("equal")
    ax.set_axis_off()
    ax.set_title("Où les 4 méthodes DJMA divergent le plus", fontsize=13)

    sm = plt.cm.ScalarMappable(cmap=cmap_divergence, norm=norm)
    cbar = fig.colorbar(sm, ax=ax, shrink=0.55, pad=0.02)
    cbar.set_label("Écart relatif entre les 4 méthodes DJMA (%)")

    top3 = valides.nlargest(3, "ecart_pct")
    lignes_top3 = "  |  ".join(
        f"{row['VILLE_A']}–{row['VILLE_B']} ({row['ecart_pct']:.0f} %)" for _, row in top3.iterrows()
    )
    fig.text(0.5, 0.03,
             f"Couleur du tracé ∝ écart relatif entre méthodes, par arc\n"
             f"Écarts les plus marqués : {lignes_top3}",
             ha="center", fontsize=8.5, color=GRIS_AXE)

    fig.tight_layout()
    fig.savefig(FIG_DIR / "carte_divergence_methodes.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("  carte_divergence_methodes.png")


def figure_carte_reseau_resultats() -> None:
    """Carte de synthèse unique : DJMA par arc (m4) + couverture du routage.

    Remplace carte_reseau_djma.png et carte_resultats.png — les deux traçaient la
    même géométrie (statut vs magnitude), jugées redondantes une fois mises côte à
    côte, comme les anciens graphiques de comparaison DJMA remplacés par la carte
    de divergence. Réutilise le même fond de carte Natural Earth que
    figure_carte_divergence_methodes() (_charger_fond_quebec / _tracer_fond).
    """
    arcs = gpd.read_file(ARCS_FILE, layer=ARCS_LAYER)[["ID_ARC", "statut", "geometry"]]
    djma = gpd.read_file(RESEAU_FILE, layer=RESEAU_LAYER)[["ID_ARC", "djma_m4"]]
    gdf = arcs.merge(djma, on="ID_ARC", how="left")

    minx, miny, maxx, maxy = gdf.total_bounds
    bbox = (minx - MARGE_M, miny - MARGE_M, maxx + MARGE_M, maxy + MARGE_M)
    fond = _charger_fond_quebec(bbox)

    ok          = gdf[gdf["statut"] == "ok"]
    echec_intra = gdf[gdf["statut"] == "aucun_djma"]
    echec_hqc   = gdf[gdf["statut"] == "hors_quebec"]

    # Figsize proportionnel à l'emprise réelle (ratio ≈ 1,37) + marge à droite pour
    # colorbar/légende — sans ça, un figsize carré laisse un bandeau blanc en haut/bas
    # (aspect equal contraint par la donnée, pas par la figure).
    span_x, span_y = bbox[2] - bbox[0], bbox[3] - bbox[1]
    fig, ax = plt.subplots(figsize=(9.5, 9.5 * span_y / span_x + 1.1))
    _tracer_fond(ax, fond)

    norm = LogNorm(vmin=ok["djma_m4"].min(), vmax=ok["djma_m4"].max())
    ok.plot(ax=ax, column="djma_m4", cmap=CMAP_DJMA, norm=norm, linewidth=1.7, zorder=2)
    echec_intra.plot(ax=ax, color=ECHEC_INTRA, linewidth=1.6, linestyle=(0, (4, 2)), zorder=3)
    echec_hqc.plot(ax=ax, color=ECHEC_HORS_QC, linewidth=2.2, zorder=3)

    ax.set_xlim(bbox[0], bbox[2]); ax.set_ylim(bbox[1], bbox[3])
    ax.set_aspect("equal")
    ax.set_axis_off()
    ax.set_title("Réseau enrichi — DJMA par arc et couverture du routage", fontsize=13)

    sm = plt.cm.ScalarMappable(cmap=CMAP_DJMA, norm=norm)
    cbar = fig.colorbar(sm, ax=ax, shrink=0.55, pad=0.02)
    cbar.set_label("DJMA — méthode m4 (véh./jour, échelle log)")

    handles = [
        Line2D([0], [0], color=ECHEC_INTRA, lw=2, linestyle=(0, (4, 2)),
               label=f"Échec — aucune station MTQ ({len(echec_intra)})"),
        Line2D([0], [0], color=ECHEC_HORS_QC, lw=2.2,
               label=f"Échec — hors Québec ({len(echec_hqc)})"),
    ]
    leg = ax.legend(handles=handles, loc="lower right", fontsize=9,
                     frameon=True, facecolor="white", edgecolor=GRIS_AXE, framealpha=1.0)
    leg.get_frame().set_linewidth(0.8)

    fig.text(0.5, 0.03, f"{len(ok)}/{len(gdf)} arcs enrichis ({100 * len(ok) / len(gdf):.1f} %)",
              ha="center", fontsize=9.5, color=GRIS_AXE)

    fig.tight_layout()
    fig.savefig(FIG_DIR / "carte_reseau_resultats.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  carte_reseau_resultats.png ({len(ok)} OK, {len(echec_intra)} aucun_djma, {len(echec_hqc)} hors_quebec)")


def figure_carte_montreal_resultats() -> None:
    """Même carte que figure_carte_reseau_resultats(), zoomée sur le Grand Montréal.

    À l'échelle du Québec, les échecs intraurbains (île de Montréal, couronnes
    nord/sud) se tassent en un fouillis illisible — ce zoom les rend lisibles
    individuellement. Rayon RAYON_MTL_M autour du nœud "Montréal" (reseau_arcs.gpkg).
    """
    from shapely.geometry import box

    noeuds = gpd.read_file(RACINE / "data" / "raw" / "reseau_arcs.gpkg", layer="noeuds")
    # "MontrÃ©al" : encodage déjà corrompu dans la donnée source (raw, non modifiable,
    # cf. CLAUDE.md) — "ontr..al" isole le nœud sans dépendre des accents mal formés.
    centre = noeuds[noeuds["NOM"].str.contains("ontr..al$", regex=True)].geometry.iloc[0]
    cx, cy = centre.x, centre.y
    bbox = (cx - RAYON_MTL_M, cy - RAYON_MTL_M, cx + RAYON_MTL_M, cy + RAYON_MTL_M)
    fenetre = box(*bbox)

    arcs = gpd.read_file(ARCS_FILE, layer=ARCS_LAYER)[["ID_ARC", "statut", "geometry"]]
    djma = gpd.read_file(RESEAU_FILE, layer=RESEAU_LAYER)[["ID_ARC", "djma_m4"]]
    gdf = arcs.merge(djma, on="ID_ARC", how="left")
    gdf = gdf[gdf.geometry.intersects(fenetre)]

    ok          = gdf[gdf["statut"] == "ok"]
    echec_intra = gdf[gdf["statut"] == "aucun_djma"]

    fond = _charger_fond_quebec(bbox)
    fig, ax = plt.subplots(figsize=(9, 9))
    _tracer_fond(ax, fond)

    norm = LogNorm(vmin=ok["djma_m4"].min(), vmax=ok["djma_m4"].max())
    ok.plot(ax=ax, column="djma_m4", cmap=CMAP_DJMA, norm=norm, linewidth=2.6, zorder=2)
    echec_intra.plot(ax=ax, color=ECHEC_INTRA, linewidth=2.4, linestyle=(0, (4, 2)), zorder=3)

    ax.set_xlim(bbox[0], bbox[2]); ax.set_ylim(bbox[1], bbox[3])
    ax.set_aspect("equal")
    ax.set_axis_off()
    ax.set_title("Zoom — Grand Montréal : où se concentrent les échecs", fontsize=13)

    sm = plt.cm.ScalarMappable(cmap=CMAP_DJMA, norm=norm)
    cbar = fig.colorbar(sm, ax=ax, shrink=0.55, pad=0.02)
    cbar.set_label("DJMA — méthode m4 (véh./jour, échelle log)")

    handles = [Line2D([0], [0], color=ECHEC_INTRA, lw=2, linestyle=(0, (4, 2)),
                      label=f"Échec — aucune station MTQ ({len(echec_intra)})")]
    leg = ax.legend(handles=handles, loc="lower right", fontsize=9,
                     frameon=True, facecolor="white", edgecolor=GRIS_AXE, framealpha=1.0)
    leg.get_frame().set_linewidth(0.8)

    fig.text(0.5, 0.03, f"{len(echec_intra)} des 19 échecs intraurbains se trouvent dans un rayon de "
              f"{RAYON_MTL_M // 1000} km autour de Montréal", ha="center", fontsize=9.5, color=GRIS_AXE)

    fig.tight_layout()
    fig.savefig(FIG_DIR / "carte_montreal_resultats.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  carte_montreal_resultats.png ({len(echec_intra)} échecs dans le zoom, {len(ok)} arcs OK)")


def main() -> None:
    FIG_DIR.mkdir(exist_ok=True)
    print("Génération des figures...")
    cv = _validation_croisee_randomforest()
    figure_validation_randomforest(cv)
    figure_randomforest_subsets(cv)
    figure_carte_divergence_methodes()
    figure_carte_reseau_resultats()
    figure_carte_montreal_resultats()
    print(f"\nTerminé — figures dans {FIG_DIR}")


if __name__ == "__main__":
    main()
