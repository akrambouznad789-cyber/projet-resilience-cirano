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
  6. segments_par_arc.png            : carte du réseau — arcs classés par nombre
     de segments DJMA (5 classes), échecs distingués par cause
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
from style_figures import (
    BLEU_450,
    COULEURS_M,
    COULEURS_ROUTE,
    CRITIQUE,
    CRS_WORK,
    ECHEC_HORS_QC,
    ECHEC_INTRA,
    EAU,
    FOND_BORDURE,
    FOND_TERRE,
    MARGE_M,
    MUET as GRIS_AXE,
    QUEBEC_BORDURE,
    SURFACE,
    appliquer_style,
    entete_carte,
    entete_figure,
    legende_carte,
)

RACINE      = Path(__file__).resolve().parent.parent
FIG_DIR     = RACINE / "figures"

DEBITS_FILE = RACINE / "data" / "processed" / "debits_completes.gpkg"
DEBITS_LAYER = "debits_completes"

RESEAU_FILE  = RACINE / "data" / "processed" / "graphe_routier_djma.gpkg"
RESEAU_LAYER = "arcs_enrichis_djma"

ARCS_FILE   = RACINE / "data" / "processed" / "graphe_routier.gpkg"
ARCS_LAYER  = "arcs_enrichis"

# Fond de carte 2D — mêmes fichiers Natural Earth déjà mis en cache par
# generer_figure_donnees.py.
DIR_REFERENCE = RACINE / "data" / "reference"
PATH_FOND_ADMIN = DIR_REFERENCE / "ne_50m_admin_1_states_provinces.zip"
PATH_FOND_OCEAN = DIR_REFERENCE / "ne_50m_ocean.zip"
PATH_FOND_LACS  = DIR_REFERENCE / "ne_50m_lakes.zip"

N_ANNEES  = 10
DJMA_COLS = [f"val_djma_annee_{i}" for i in range(1, N_ANNEES + 1)]
CAM_COLS  = [f"val_cam_annee_{i}"  for i in range(1, N_ANNEES + 1)]

# Rampe séquentielle pastel pour le DJMA (carte_reseau_resultats.png) — prolonge
# BLEU_450 (déjà la série "séquentielle" de ce fichier) plutôt que le cmap "Blues"
# générique de matplotlib, pour rester dans la même identité visuelle que le reste
# du README.
CMAP_DJMA = LinearSegmentedColormap.from_list("cirano_djma", ["#dce8f7", BLEU_450, "#0d2d52"])

# Rayon du zoom Grand Montréal (carte_montreal_resultats.png), centré sur le nœud
# "Montréal" — capture la quasi-totalité des échecs intraurbains (île + couronnes
# nord/sud), qui se tassent en un fouillis illisible à l'échelle du Québec.
RAYON_MTL_M = 55_000

# Classes de segments_par_arc.png — bornes choisies sur les quintiles actuels de
# n_segs_djma (~55-60 arcs par classe) plutôt qu'un dégradé continu : on lit
# directement combien de segments un arc regroupe, sans consulter une colorbar.
BORNES_SEGMENTS     = [0, 3, 6, 10, 18, np.inf]
ETIQUETTES_SEGMENTS = ["1–3", "4–6", "7–10", "11–18", "19+"]
COULEURS_SEGMENTS   = [CMAP_DJMA(t) for t in np.linspace(0.08, 1.0, len(ETIQUETTES_SEGMENTS))]

appliquer_style()


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

    fig, ax = plt.subplots(figsize=(6, 6.4))
    ax.scatter(y_true_flat, y_pred_flat, s=10, alpha=0.35, color=BLEU_450, linewidths=0)
    lims = [0, max(y_true_flat.max(), y_pred_flat.max()) * 1.05]
    ax.plot(lims, lims, color=GRIS_AXE, linewidth=1.5, linestyle="--")
    ax.set_xlim(lims); ax.set_ylim(lims)
    ax.set_xlabel("% camions réel")
    ax.set_ylabel("% camions prédit (RandomForest, validation croisée 5-fold)")
    entete_figure(fig, ax, "Validation du RandomForest",
                  f"R² = {cv['r2']:.3f}, RMSE = {cv['rmse']:.2f} pts", y=0.98)
    legende_carte(ax, [Line2D([0], [0], color=GRIS_AXE, lw=1.5, linestyle="--",
                              label="Prédiction parfaite")], loc="upper left")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
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
    """Fond de carte Québec (Natural Earth) — mêmes fichiers que ceux mis en cache
    par generer_figure_donnees.py (data/reference/), relus ici avec un clip exact
    (gpd.clip) plutôt qu'un simple filtre de bbox (.cx[]) : chaque figure de ce
    module a son propre ratio d'emprise (calculé depuis ses propres données), un
    clip exact évite tout débord au-delà de ce cadre précis.
    """
    if not (PATH_FOND_ADMIN.exists() and PATH_FOND_OCEAN.exists() and PATH_FOND_LACS.exists()):
        return None
    from shapely.geometry import box
    minx, miny, maxx, maxy = bbox
    fenetre = box(minx, miny, maxx, maxy)

    fond   = gpd.read_file(PATH_FOND_ADMIN).to_crs(CRS_WORK).cx[minx:maxx, miny:maxy]
    quebec = gpd.clip(fond[fond["name"] == "Québec"], fenetre)
    autres = gpd.clip(fond[fond["name"] != "Québec"], fenetre)
    ocean  = gpd.clip(gpd.read_file(PATH_FOND_OCEAN).to_crs(CRS_WORK).cx[minx:maxx, miny:maxy], fenetre)
    lacs   = gpd.clip(gpd.read_file(PATH_FOND_LACS).to_crs(CRS_WORK).cx[minx:maxx, miny:maxy], fenetre)
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
    entete_carte(ax, "Où les 4 méthodes DJMA divergent le plus",
                 "Écart relatif entre m1-m4, par arc")

    sm = plt.cm.ScalarMappable(cmap=cmap_divergence, norm=norm)
    cbar = fig.colorbar(sm, ax=ax, shrink=0.55, pad=0.02)
    cbar.set_label("Écart relatif entre les 4 méthodes DJMA (%)")

    top3 = valides.nlargest(3, "ecart_pct")
    lignes_top3 = "  |  ".join(
        f"{row['VILLE_A']}–{row['VILLE_B']} ({row['ecart_pct']:.0f} %)" for _, row in top3.iterrows()
    )
    fig.text(0.5, 0.03, f"Écarts les plus marqués : {lignes_top3}",
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
    entete_carte(ax, "Réseau enrichi — DJMA par arc",
                 f"{len(ok)}/{len(gdf)} arcs enrichis ({100 * len(ok) / len(gdf):.1f} %) · couverture du routage")

    sm = plt.cm.ScalarMappable(cmap=CMAP_DJMA, norm=norm)
    cbar = fig.colorbar(sm, ax=ax, shrink=0.55, pad=0.02)
    cbar.set_label("DJMA — méthode m4 (véh./jour, échelle log)")

    legende_carte(ax, [
        Line2D([0], [0], color=ECHEC_INTRA, lw=2, linestyle=(0, (4, 2)),
               label=f"Échec — aucune station MTQ ({len(echec_intra)})"),
        Line2D([0], [0], color=ECHEC_HORS_QC, lw=2.2,
               label=f"Échec — hors Québec ({len(echec_hqc)})"),
    ])

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
    entete_carte(ax, "Zoom — Grand Montréal",
                 f"{len(echec_intra)} des 19 échecs intraurbains dans un rayon de "
                 f"{RAYON_MTL_M // 1000} km autour de Montréal")

    sm = plt.cm.ScalarMappable(cmap=CMAP_DJMA, norm=norm)
    cbar = fig.colorbar(sm, ax=ax, shrink=0.55, pad=0.02)
    cbar.set_label("DJMA — méthode m4 (véh./jour, échelle log)")

    legende_carte(ax, [Line2D([0], [0], color=ECHEC_INTRA, lw=2, linestyle=(0, (4, 2)),
                              label=f"Échec — aucune station MTQ ({len(echec_intra)})")])

    fig.tight_layout()
    fig.savefig(FIG_DIR / "carte_montreal_resultats.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  carte_montreal_resultats.png ({len(echec_intra)} échecs dans le zoom, {len(ok)} arcs OK)")


def figure_segments_par_arc() -> None:
    """Carte du réseau — arcs classés par nombre de segments DJMA (5 classes).

    Même gabarit que figure_carte_reseau_resultats() (fond Québec, échecs
    distingués par cause), mais n_segs_djma est découpé en 5 classes
    (BORNES_SEGMENTS) plutôt que codé en dégradé continu : chaque arc porte une
    couleur de palier directement lisible dans la légende, pas une position sur
    une colorbar.
    """
    arcs = gpd.read_file(ARCS_FILE, layer=ARCS_LAYER)[
        ["ID_ARC", "statut", "n_segs_djma", "geometry"]
    ]

    minx, miny, maxx, maxy = arcs.total_bounds
    bbox = (minx - MARGE_M, miny - MARGE_M, maxx + MARGE_M, maxy + MARGE_M)
    fond = _charger_fond_quebec(bbox)

    ok          = arcs[arcs["statut"] == "ok"].copy()
    echec_intra = arcs[arcs["statut"] == "aucun_djma"]
    echec_hqc   = arcs[arcs["statut"] == "hors_quebec"]

    ok["classe"] = pd.cut(ok["n_segs_djma"], bins=BORNES_SEGMENTS, labels=ETIQUETTES_SEGMENTS)

    span_x, span_y = bbox[2] - bbox[0], bbox[3] - bbox[1]
    fig, ax = plt.subplots(figsize=(9.5, 9.5 * span_y / span_x + 1.1))
    _tracer_fond(ax, fond)

    for etiquette, couleur in zip(ETIQUETTES_SEGMENTS, COULEURS_SEGMENTS):
        ok[ok["classe"] == etiquette].plot(ax=ax, color=couleur, linewidth=1.9, zorder=2)
    echec_intra.plot(ax=ax, color=ECHEC_INTRA, linewidth=1.6, linestyle=(0, (4, 2)), zorder=3)
    echec_hqc.plot(ax=ax, color=ECHEC_HORS_QC, linewidth=2.2, zorder=3)

    ax.set_xlim(bbox[0], bbox[2]); ax.set_ylim(bbox[1], bbox[3])
    ax.set_aspect("equal")
    ax.set_axis_off()
    entete_carte(ax, "Réseau enrichi — segments DJMA par arc",
                 f"{len(ok)}/{len(arcs)} arcs routés · médiane {int(ok['n_segs_djma'].median())} segments/arc")

    legende_carte(ax, [
        Line2D([0], [0], color=couleur, lw=4,
               label=f"{etiquette} segments ({(ok['classe'] == etiquette).sum()})")
        for etiquette, couleur in zip(ETIQUETTES_SEGMENTS, COULEURS_SEGMENTS)
    ] + [
        Line2D([0], [0], color=ECHEC_INTRA, lw=2, linestyle=(0, (4, 2)),
               label=f"Échec — aucune station MTQ ({len(echec_intra)})"),
        Line2D([0], [0], color=ECHEC_HORS_QC, lw=2.2,
               label=f"Échec — hors Québec ({len(echec_hqc)})"),
    ], titre="Segments DJMA par arc")

    fig.tight_layout()
    fig.savefig(FIG_DIR / "segments_par_arc.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  segments_par_arc.png ({len(ok)} arcs, médiane {int(ok['n_segs_djma'].median())} segments/arc)")


def main() -> None:
    FIG_DIR.mkdir(exist_ok=True)
    print("Génération des figures...")
    cv = _validation_croisee_randomforest()
    figure_validation_randomforest(cv)
    figure_randomforest_subsets(cv)
    figure_carte_divergence_methodes()
    figure_carte_reseau_resultats()
    figure_carte_montreal_resultats()
    figure_segments_par_arc()
    print(f"\nTerminé — figures dans {FIG_DIR}")


if __name__ == "__main__":
    main()
