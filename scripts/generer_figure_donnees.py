"""
generer_figure_donnees.py
==========================
Projet CIRANO — Figures de vulgarisation des données (README, section « Données »)

Génère 7 figures dans figures/ :
  - reseau_graphe.png          : nœuds et liens du graphe simplifié
  - reseau_routier.png         : réseau routier MTQ (RTSS), coloré par type de route
  - comptage_routier.png       : stations de comptage MTQ, colorées par complétude DJMA
  - comptage_completude.png    : complétude DJMA vs %camion (colonnes empilées)
  - extrapolation_gradient.png : étape 1 (interpolation/extrapolation), segment réel
  - knn_geographique.png       : étape 3 (KNN + IDW), segment réel et ses voisins
  - evolution_completion.png   : synthèse — % complet après chaque étape de la cascade

Les 4 premières sont cadrées sur la même emprise et le même gabarit (comme si
elles pouvaient se superposer), sur un fond de carte neutre façon Google/Apple
Maps (terre unie, eau bleue — Natural Earth) : la couleur reste réservée aux
données (lignes/points), pas au fond. Les 3 dernières illustrent la cascade de
complétion (cf. completion_donnees_randomforest.py) sur des exemples réels.

Les dictionnaires de variables et tableaux de répartition correspondants sont
présentés en texte dans le README, pas dans les figures.
"""

import urllib.request
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from scipy.spatial import cKDTree
from sklearn.linear_model import LinearRegression

from completion_donnees_randomforest import (
    ANNEES,
    CAM_COLS,
    CRS_PROJETE,
    DJMA_COLS,
    FENETRE_GRADIENT,
    K_VOISINS,
    _extrapoler_extremites,
    mice_cam,
    phase1_temporelle,
    phase2_geographique,
    voisins_ponderes,
)

RACINE  = Path(__file__).resolve().parent.parent
FIG_DIR = RACINE / "figures"

PATH_ARCS   = RACINE / "data" / "raw" / "reseau_arcs.gpkg"
PATH_RTSS   = RACINE / "data" / "raw" / "ReseauRoutier_RTSS.gpkg"
PATH_DEBITS = RACINE / "data" / "raw" / "DebitCirculation.gpkg"
PATH_DEBITS_COMPLETES = RACINE / "data" / "processed" / "debits_completes.gpkg"

DIR_REFERENCE = RACINE / "data" / "reference"
PATH_FOND   = DIR_REFERENCE / "ne_50m_admin_1_states_provinces.zip"
URL_FOND    = "https://naciscdn.org/naturalearth/50m/cultural/ne_50m_admin_1_states_provinces.zip"
PATH_OCEAN  = DIR_REFERENCE / "ne_50m_ocean.zip"
URL_OCEAN   = "https://naciscdn.org/naturalearth/50m/physical/ne_50m_ocean.zip"
PATH_LACS   = DIR_REFERENCE / "ne_50m_lakes.zip"
URL_LACS    = "https://naciscdn.org/naturalearth/50m/physical/ne_50m_lakes.zip"

DIR_FONTS   = DIR_REFERENCE / "fonts"
POLICES = {
    DIR_FONTS / "Arimo-Regular.ttf": "https://fonts.gstatic.com/s/arimo/v36/P5sfzZCDf9_T_3cV7NCUECyoxNk37cxsBw.ttf",
    DIR_FONTS / "Arimo-Bold.ttf":    "https://fonts.gstatic.com/s/arimo/v36/P5sfzZCDf9_T_3cV7NCUECyoxNk3CstsBw.ttf",
}

CRS_WORK    = "EPSG:32198"
MARGE_M     = 20_000
N_ANNEES    = 10

# --- Palette -----------------------------------------------------------
# Fond de carte neutre, façon Google/Apple Maps : une seule teinte terre,
# le Québec distingué par un simple contour (pas une couleur de remplissage).
SURFACE  = "#fcfcfb"
ENCRE    = "#0b0b0b"
ENCRE_2  = "#52514e"
MUET     = "#898781"
GRILLE   = "#e1e0d9"
BASELINE = "#c3c2b7"

EAU            = "#bcdcf2"   # océan + lacs
FOND_TERRE     = "#f1efe6"   # terre — Québec et voisins, même teinte neutre
FOND_BORDURE   = "#ddd9cc"   # frontières provinces/états voisins
QUEBEC_BORDURE = "#9aa5b1"   # contour du Québec (plus marqué, pas de remplissage distinct)

# Les données (lignes/points) portent la couleur : palette pastel claire.
BLEU_ARC   = "#b7c9f2"   # ton très clair — liens du graphe
BLEU_NOEUD = "#5b7fdb"   # ton plein mais doux — nœuds du graphe

COULEURS_CLASSE = {
    "Autoroute":   "#f2a0b5",   # rose pastel
    "Nationale":   "#8fa8f0",   # bleu pastel
    "Régionale":   "#7ecfb0",   # vert d'eau pastel
    "Collectrice": "#b6a8d9",   # mauve pastel
}
COULEUR_AUTRE = "#cfcabd"

COMPLETUDE = {   # clair et doux — pas de rouge/jaune/vert agressifs
    "Complet": "#7fe0c0",
    "Partiel": "#fcd675",
    "Vide":    "#f7a19a",
}
ORDRE_COMPLETUDE = ["Complet", "Partiel", "Vide"]

# Paire catégorielle DJMA / %camions — validée (validate_palette.js, mode light) :
# CVD adjacent ΔE 17.3 (protan), normal-vision ΔE 24.3, PASS.
SAUMON = "#e0857f"   # série %camions (DJMA reprend BLEU_NOEUD, déjà utilisé pour le graphe)

# Segments réels choisis pour illustrer chaque étape de la cascade (cf. exploration
# data/processed/debits_completes.gpkg) :
#   - extrapolation : tendances locales de signe opposé aux deux bords (bon test du gradient local)
#   - geo_knn       : 5 voisins de même index_agreg, distance médiane — carte lisible à un zoom raisonnable
ID_SEGMENT_EXTRAPOLATION = 28787
ID_SEGMENT_KNN            = 30014

# Gabarit commun aux 3 cartes, pour qu'elles soient superposables
MAP_FIGSIZE  = (10, 8)
MAP_MARGINS  = dict(left=0.02, right=0.98, top=0.90, bottom=0.02)


def charger_polices() -> None:
    """Télécharge (une fois, mis en cache) Arimo — clone libre d'Helvetica/Arial."""
    import matplotlib.font_manager as fm
    DIR_FONTS.mkdir(parents=True, exist_ok=True)
    for chemin, url in POLICES.items():
        if not chemin.exists():
            urllib.request.urlretrieve(url, chemin)
        fm.fontManager.addfont(str(chemin))


charger_polices()
plt.rcParams.update({
    "font.family": "Arimo",
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "savefig.facecolor": SURFACE,
    "text.color": ENCRE,
})


def categoriser(n: int) -> str:
    """Classe un nombre d'années connues (0-10) en Complet / Partiel / Vide."""
    if n == N_ANNEES:
        return "Complet"
    if n == 0:
        return "Vide"
    return "Partiel"


def calculer_bbox_reference() -> tuple:
    """Emprise commune aux 3 cartes : étendue réelle des 307 arcs (+ marge)."""
    arcs = gpd.read_file(PATH_ARCS, layer="arcs").set_crs(CRS_WORK, allow_override=True)
    minx, miny, maxx, maxy = arcs.total_bounds
    return (minx - MARGE_M, miny - MARGE_M, maxx + MARGE_M, maxy + MARGE_M)


def telecharger_si_absent(path: Path, url: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        urllib.request.urlretrieve(url, path)
    return path


def charger_fond_geographique(bbox: tuple) -> dict:
    """Télécharge (une fois, mis en cache) le fond de carte façon Google/Apple Maps :
    provinces/états voisins (gris), Québec (pastel), océan et lacs (bleu)."""
    fond = gpd.read_file(telecharger_si_absent(PATH_FOND, URL_FOND)).to_crs(CRS_WORK)
    quebec = fond[fond["name"] == "Québec"]
    autres = fond[fond["name"] != "Québec"]

    minx, miny, maxx, maxy = bbox
    ocean = gpd.read_file(telecharger_si_absent(PATH_OCEAN, URL_OCEAN)).to_crs(CRS_WORK)
    ocean = ocean.cx[minx:maxx, miny:maxy]
    lacs = gpd.read_file(telecharger_si_absent(PATH_LACS, URL_LACS)).to_crs(CRS_WORK)
    lacs = lacs.cx[minx:maxx, miny:maxy]

    return {"quebec": quebec, "autres": autres, "ocean": ocean, "lacs": lacs}


def nouvelle_carte() -> tuple:
    """Figure + axe, gabarit identique pour les 3 cartes."""
    fig, ax = plt.subplots(figsize=MAP_FIGSIZE)
    fig.subplots_adjust(**MAP_MARGINS)
    return fig, ax


def cadrer(ax, bbox: tuple, fond: dict) -> None:
    """Fond de carte neutre façon Google/Apple Maps (eau bleue, terre unie) + cadrage identique.

    Le Québec n'a pas de remplissage distinct de ses voisins — seul un contour plus
    marqué le délimite, comme une frontière d'État sur une carte grand public. La
    couleur reste réservée aux données (lignes/points) tracées par-dessus.
    """
    fond["ocean"].plot(ax=ax, color=EAU, linewidth=0, zorder=-3)
    fond["autres"].plot(ax=ax, color=FOND_TERRE, edgecolor=FOND_BORDURE, linewidth=0.6, zorder=-2)
    fond["quebec"].plot(ax=ax, color=FOND_TERRE, edgecolor=QUEBEC_BORDURE, linewidth=1.1, zorder=-1)
    fond["lacs"].plot(ax=ax, color=EAU, linewidth=0, zorder=-0.5)
    ax.set_xlim(bbox[0], bbox[2])
    ax.set_ylim(bbox[1], bbox[3])
    ax.set_aspect("equal")
    ax.set_axis_off()


def entete_carte(ax, titre: str, sous_titre: str) -> None:
    ax.text(0.0, 1.07, titre, fontsize=15, fontweight="bold", color=ENCRE, transform=ax.transAxes)
    ax.text(0.0, 1.02, sous_titre, fontsize=10.5, color=ENCRE_2, transform=ax.transAxes)


def legende_carte(ax, handles: list, titre: str | None = None, loc: str = "lower right") -> None:
    """Légende encadrée (fond plein), pour ne pas se fondre dans la carte."""
    leg = ax.legend(handles=handles, loc=loc, fontsize=9.5, frameon=True,
                    facecolor=SURFACE, edgecolor=BASELINE, framealpha=1.0, title=titre)
    leg.get_frame().set_linewidth(0.8)
    if titre:
        leg.get_title().set_fontweight("bold")


# ============================================================
# FIGURE 1 — Réseau graphe (nœuds et liens)
# ============================================================

def figure_reseau_graphe(bbox: tuple, fond: dict) -> None:
    arcs   = gpd.read_file(PATH_ARCS, layer="arcs").set_crs(CRS_WORK, allow_override=True)
    noeuds = gpd.read_file(PATH_ARCS, layer="noeuds").set_crs(CRS_WORK, allow_override=True)
    ids_relies = set(arcs["ID_A"]) | set(arcs["ID_B"])
    noeuds = noeuds[noeuds["ID"].isin(ids_relies)]

    fig, ax = nouvelle_carte()
    cadrer(ax, bbox, fond)
    arcs.plot(ax=ax, color=BLEU_ARC, linewidth=1.1, zorder=2)
    noeuds.plot(ax=ax, color=BLEU_NOEUD, markersize=16, zorder=3)
    entete_carte(ax, "Réseau graphe — nœuds et liens", f"{len(arcs)} liens · {len(noeuds)} nœuds reliés")

    legende_carte(ax, [
        Line2D([0], [0], color=BLEU_ARC, lw=3, label="Lien (arc)"),
        Line2D([0], [0], marker="o", color=SURFACE, markerfacecolor=BLEU_NOEUD,
              markersize=9, label="Nœud (ville)"),
    ])

    fig.savefig(FIG_DIR / "reseau_graphe.png", dpi=150)
    plt.close(fig)
    print(f"  reseau_graphe.png ({len(arcs)} arcs, {len(noeuds)} nœuds)")


# ============================================================
# FIGURE 2 — Réseau routier (RTSS)
# ============================================================

def figure_reseau_routier(bbox: tuple, fond: dict) -> None:
    rtss = gpd.read_file(PATH_RTSS).to_crs(CRS_WORK)
    total = len(rtss)

    fig, ax = nouvelle_carte()
    cadrer(ax, bbox, fond)
    rtss[~rtss["des_clasf_"].isin(COULEURS_CLASSE)].plot(
        ax=ax, color=COULEUR_AUTRE, linewidth=0.5, zorder=1)
    for classe, couleur in COULEURS_CLASSE.items():
        rtss[rtss["des_clasf_"] == classe].plot(ax=ax, color=couleur, linewidth=0.7, zorder=2)

    entete_carte(ax, "Réseau routier — RTSS (MTQ)",
               f"{total:,} segments · classifiés par type de route".replace(",", " "))

    legende_carte(ax, [
        Line2D([0], [0], color=c, lw=3, label=classe) for classe, c in COULEURS_CLASSE.items()
    ] + [Line2D([0], [0], color=COULEUR_AUTRE, lw=3, label="Autre / sans classe")])

    fig.savefig(FIG_DIR / "reseau_routier.png", dpi=150)
    plt.close(fig)
    print(f"  reseau_routier.png ({total} segments RTSS)")

    classes_ordre = [*COULEURS_CLASSE.keys(), "Autre"]
    compte = rtss["des_clasf_"].apply(lambda c: c if c in COULEURS_CLASSE else "Autre").value_counts()
    for classe in classes_ordre:
        n = int(compte.get(classe, 0))
        print(f"    {classe:<12} {n:>6}  ({100 * n / total:.1f} %)")


# ============================================================
# FIGURE 3 — Comptage routier (DebitCirculation)
# ============================================================

def calculer_completude_globale(deb: pd.DataFrame) -> dict:
    djma_cols = [f"val_djma_annee_{i}" for i in range(1, N_ANNEES + 1)]
    cam_cols  = [f"val_cam_annee_{i}"  for i in range(1, N_ANNEES + 1)]
    for c in djma_cols + cam_cols:
        deb[c] = pd.to_numeric(deb[c], errors="coerce")

    cat_djma = deb[djma_cols].notna().sum(axis=1).apply(categoriser)
    cat_cam  = deb[cam_cols].notna().sum(axis=1).apply(categoriser)
    total = len(deb)

    pct_djma = (cat_djma.value_counts() / total * 100).reindex(ORDRE_COMPLETUDE, fill_value=0)
    pct_cam  = (cat_cam.value_counts()  / total * 100).reindex(ORDRE_COMPLETUDE, fill_value=0)
    n_djma_complet = int((cat_djma == "Complet").sum())
    n_les_deux     = int(((cat_djma == "Complet") & (cat_cam == "Complet")).sum())

    return {
        "cat_djma": cat_djma, "total": total,
        "pct_djma": pct_djma, "pct_cam": pct_cam,
        "n_djma_complet": n_djma_complet, "n_les_deux": n_les_deux,
        "pct_condition": 100 * n_les_deux / n_djma_complet,
    }


def figure_comptage_routier(bbox: tuple, fond: dict, stats: dict, deb: gpd.GeoDataFrame) -> None:
    fig, ax = nouvelle_carte()
    cadrer(ax, bbox, fond)
    for cat in ORDRE_COMPLETUDE:
        deb[deb["cat_djma"] == cat].plot(ax=ax, color=COMPLETUDE[cat], linewidth=0.9, zorder=2)

    entete_carte(ax, "Comptage routier — DebitCirculation (MTQ)",
               f"{stats['total']:,} segments · complétude du DJMA sur 10 ans".replace(",", " "))

    legende_carte(ax, [Line2D([0], [0], color=COMPLETUDE[s], lw=3, label=s) for s in ORDRE_COMPLETUDE],
                titre="DJMA")

    fig.savefig(FIG_DIR / "comptage_routier.png", dpi=150)
    plt.close(fig)
    print(f"  comptage_routier.png ({stats['total']} segments)")


def figure_comptage_completude(stats: dict) -> None:
    """Complétude DJMA vs %camion — colonnes empilées à 100 %, seules (pas de carte)."""
    fig, ax = plt.subplots(figsize=(6.5, 5.5))
    categories = ["DJMA", "% camions"]
    largeur = 0.5
    x = [0, 1]

    for xi, cat_label in zip(x, categories):
        pct = stats["pct_djma"] if cat_label == "DJMA" else stats["pct_cam"]
        bas = 0.0
        for statut in ORDRE_COMPLETUDE:
            hauteur = pct[statut]
            ax.bar(xi, hauteur, bottom=bas, width=largeur, color=COMPLETUDE[statut],
                  edgecolor=SURFACE, linewidth=2, zorder=3)
            etiquette = f"{hauteur:.1f}".replace(".", ",") + " %"
            centre_y = bas + hauteur / 2
            if hauteur >= 7:
                ax.text(xi, centre_y, etiquette, fontsize=10, fontweight="bold",
                       color=ENCRE, ha="center", va="center", zorder=4)
            else:
                ax.plot([xi + largeur / 2, xi + largeur / 2 + 0.09], [centre_y, centre_y],
                       color=MUET, linewidth=1, zorder=2, clip_on=False)
                ax.text(xi + largeur / 2 + 0.12, centre_y, etiquette, fontsize=9,
                       color=ENCRE, ha="left", va="center", clip_on=False)
            bas += hauteur

    ax.set_title("Complétude DJMA vs % camions", fontsize=13, fontweight="bold", color=ENCRE, pad=14)
    ax.set_xlim(-0.55, 1.55)
    ax.set_ylim(0, 100)
    ax.set_xticks(x)
    ax.set_xticklabels(categories, fontsize=11, fontweight="bold", color=ENCRE)
    ax.tick_params(axis="x", length=0, pad=8)
    ax.set_yticks([0, 50, 100])
    ax.set_yticklabels([f"{v} %" for v in [0, 50, 100]], fontsize=8.5, color=MUET)
    ax.tick_params(axis="y", length=0)
    ax.yaxis.grid(True, color=GRILLE, linewidth=1, zorder=0)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_visible(False)

    legende_carte(ax, [Line2D([0], [0], color=COMPLETUDE[s], lw=8, label=s) for s in ORDRE_COMPLETUDE],
                loc="lower center")

    fig.tight_layout()
    fig.savefig(FIG_DIR / "comptage_completude.png", dpi=150)
    plt.close(fig)
    print(f"  comptage_completude.png ({stats['n_les_deux']}/{stats['n_djma_complet']} "
          f"DJMA-complets aussi %camion-complets)")


# ============================================================
# FIGURE — Interpolation / extrapolation (gradient local par bord)
# ============================================================

def figure_extrapolation_gradient() -> None:
    """Étape 1 de la cascade, sur un vrai segment : gradient local par bord.

    Compare, point par point, l'ancienne méthode (une régression unique sur
    toute la plage connue) à l'implémentation actuelle (_extrapoler_extremites,
    une régression LOCALE par bord) sur un segment réel où les deux bords ont
    des tendances de signe opposé — le cas où l'ancienne méthode se trompait.
    """
    raw = gpd.read_file(PATH_DEBITS)
    ligne = raw.loc[raw["ide_sectn_trafc"] == ID_SEGMENT_EXTRAPOLATION].iloc[0]
    vals_brut = pd.to_numeric(ligne[DJMA_COLS], errors="coerce").values.astype(float)

    annees_arr = np.array(ANNEES)
    ordre      = np.argsort(annees_arr)          # ordre chronologique croissant
    annees_c   = annees_arr[ordre]
    vals_c     = vals_brut[ordre]
    mask_ok    = ~np.isnan(vals_c)
    idx_ok     = np.where(mask_ok)[0]
    manquants  = ~mask_ok

    nouvelle_c = _extrapoler_extremites(vals_brut, list(ANNEES))[ordre]

    reg_globale = LinearRegression().fit(annees_c[mask_ok].reshape(-1, 1), vals_c[mask_ok])
    ancienne_c  = reg_globale.predict(annees_c.reshape(-1, 1))

    fig, ax = plt.subplots(figsize=(8, 5.5))

    for fenetre in (idx_ok[:FENETRE_GRADIENT], idx_ok[-FENETRE_GRADIENT:]):
        ax.axvspan(annees_c[fenetre[0]] - 0.4, annees_c[fenetre[-1]] + 0.4,
                  color=BLEU_NOEUD, alpha=0.08, zorder=0)

    ax.plot(annees_c, ancienne_c, color=MUET, linewidth=1, linestyle="--", alpha=0.5, zorder=1)
    ax.plot(annees_c[manquants], ancienne_c[manquants], color=MUET, linewidth=0, zorder=2,
           marker="^", markersize=8, markerfacecolor=MUET, markeredgecolor=SURFACE, markeredgewidth=1.3)

    ax.plot(annees_c[:idx_ok[0] + 1], nouvelle_c[:idx_ok[0] + 1], color=SAUMON, linewidth=1.8, zorder=2)
    ax.plot(annees_c[idx_ok[-1]:], nouvelle_c[idx_ok[-1]:], color=SAUMON, linewidth=1.8, zorder=2)
    ax.plot(annees_c[manquants], nouvelle_c[manquants], color=SAUMON, linewidth=0, zorder=4,
           marker="D", markersize=8, markerfacecolor=SAUMON, markeredgecolor=SURFACE, markeredgewidth=1.3)

    ax.plot(annees_c[mask_ok], vals_c[mask_ok], color=ENCRE, linewidth=2, zorder=3,
           marker="o", markersize=8, markerfacecolor=ENCRE, markeredgecolor=SURFACE, markeredgewidth=1.3)

    for xi, yi in zip(annees_c[manquants], nouvelle_c[manquants]):
        ax.annotate(f"{yi:,.0f}".replace(",", " "), (xi, yi), textcoords="offset points",
                   xytext=(0, 11), ha="center", fontsize=8.5, fontweight="bold", color=ENCRE, zorder=5)
    for xi, yi in zip(annees_c[manquants], ancienne_c[manquants]):
        ax.annotate(f"{yi:,.0f}".replace(",", " "), (xi, yi), textcoords="offset points",
                   xytext=(0, -15), ha="center", fontsize=8, color=MUET, zorder=5)

    fig.suptitle("Interpolation / extrapolation — gradient local par bord", fontsize=13,
               fontweight="bold", color=ENCRE, y=0.99)
    ax.set_title(f"Segment réel #{ID_SEGMENT_EXTRAPOLATION} · DJMA (véh./jour)",
               fontsize=10, color=ENCRE_2, pad=10)
    ax.set_xticks(annees_c)
    ax.set_xticklabels(annees_c, fontsize=9, color=ENCRE_2)
    ax.set_ylabel("DJMA (véh./jour)", fontsize=9.5, color=ENCRE_2)
    ax.yaxis.grid(True, color=GRILLE, linewidth=1, zorder=0)
    ax.set_axisbelow(True)
    ax.tick_params(axis="both", length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)

    legende_carte(ax, [
        Line2D([0], [0], color=ENCRE, lw=2, marker="o", markersize=7, label="Connu"),
        Line2D([0], [0], color=MUET, lw=1, linestyle="--", marker="^", markersize=7,
              label="Ancienne méthode (régression globale)"),
        Line2D([0], [0], color=SAUMON, lw=1.8, marker="D", markersize=7,
              label="Nouvelle méthode (gradient local)"),
    ], loc="lower center")

    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(FIG_DIR / "extrapolation_gradient.png", dpi=150)
    plt.close(fig)
    print(f"  extrapolation_gradient.png (segment {ID_SEGMENT_EXTRAPOLATION})")


# ============================================================
# FIGURE — Complétion géographique (KNN + IDW, exemple réel)
# ============================================================

def figure_knn_geographique() -> None:
    """Étape 3 de la cascade, sur un vrai segment sans aucune mesure : KNN + IDW.

    Reproduit exactement la sélection de voisins_ponderes() (même algorithme
    que phase2_geographique) pour un segment cible réel, et montre les
    K_VOISINS segments source qui ont déterminé sa valeur complétée, reliés
    par des traits dont l'épaisseur encode le poids IDW (1/distance²).

    Fond neutre uni (pas le fond de carte Québec/eau) : au zoom local requis
    ici (quelques km), une grande étendue d'eau peut dominer visuellement le
    cadre sans rien ajouter à la lecture du mécanisme KNN.
    """
    gdf = gpd.read_file(PATH_DEBITS_COMPLETES, layer="debits_completes")
    gdf_proj   = gdf.to_crs(CRS_PROJETE)
    centroides = gdf_proj.geometry.centroid

    mask_knn = gdf["methode_djma"] == "geo_knn"
    mask_src = ~mask_knn
    src_idx    = gdf.index[mask_src].tolist()
    coords_src = np.array([(c.x, c.y) for c in centroides[mask_src]])
    types_src  = gdf.loc[mask_src, "index_agreg"].values
    arbre      = cKDTree(coords_src)

    idx_cible   = gdf.index[gdf["ide_sectn_trafc"] == ID_SEGMENT_KNN][0]
    coord_cible = np.array([centroides.loc[idx_cible].x, centroides.loc[idx_cible].y])
    type_cible  = gdf.loc[idx_cible, "index_agreg"]

    idx_voisins, poids, distances = voisins_ponderes(
        coord_cible, type_cible, coords_src, types_src, src_idx, arbre)

    moyennes_voisins = gdf.loc[idx_voisins, DJMA_COLS].astype(float).mean(axis=1)
    moyenne_cible     = gdf.loc[idx_cible, DJMA_COLS].astype(float).mean()

    zone = gdf_proj.loc[[idx_cible] + idx_voisins]
    minx, miny, maxx, maxy = zone.total_bounds
    marge = max(600.0, 0.35 * max(maxx - minx, maxy - miny))
    bbox_local = (minx - marge, miny - marge, maxx + marge, maxy + marge)

    fig, ax = nouvelle_carte()
    ax.set_facecolor(FOND_TERRE)
    ax.set_xlim(bbox_local[0], bbox_local[2])
    ax.set_ylim(bbox_local[1], bbox_local[3])
    ax.set_aspect("equal")
    ax.set_axis_off()

    for idx_v, poids_v in zip(idx_voisins, poids):
        cv = centroides.loc[idx_v]
        ax.plot([coord_cible[0], cv.x], [coord_cible[1], cv.y], color=MUET,
               linewidth=0.6 + 5.0 * poids_v, alpha=0.7, zorder=2, solid_capstyle="round")

    gdf_proj.loc[idx_voisins].plot(ax=ax, color=BLEU_NOEUD, linewidth=2.2, zorder=3)
    gdf_proj.loc[[idx_cible]].plot(ax=ax, color=SAUMON, linewidth=3, zorder=4)

    # Étiquettes orientées radialement (centre du groupe → chaque point), pour
    # limiter les collisions entre voisins proches sur l'image.
    centre = np.array([coord_cible[0], coord_cible[1]])
    for idx_v, poids_v in zip(idx_voisins, poids):
        cv = centroides.loc[idx_v]
        ax.plot(cv.x, cv.y, marker="o", markersize=8, markerfacecolor=BLEU_NOEUD,
               markeredgecolor=SURFACE, markeredgewidth=1.5, zorder=5)
        direction = np.array([cv.x, cv.y]) - centre
        direction = direction / (np.linalg.norm(direction) or 1.0)
        ax.annotate(f"{moyennes_voisins[idx_v]:,.0f} véh/j — poids {100*poids_v:.0f} %".replace(",", " "),
                   (cv.x, cv.y), textcoords="offset points",
                   xytext=(18 * direction[0], 18 * direction[1]),
                   ha="center", va="center", fontsize=8.5, color=ENCRE_2, zorder=6)

    ax.plot(coord_cible[0], coord_cible[1], marker="*", markersize=18, markerfacecolor=SAUMON,
           markeredgecolor=SURFACE, markeredgewidth=1.5, zorder=6)
    ax.annotate(f"Cible → {moyenne_cible:,.0f} véh/j (estimé)".replace(",", " "),
               (coord_cible[0], coord_cible[1]), textcoords="offset points", xytext=(0, -18),
               ha="center", fontsize=9.5, fontweight="bold", color=ENCRE, zorder=6)

    entete_carte(ax, "Complétion géographique — KNN + IDW",
               f"Segment réel #{ID_SEGMENT_KNN} · {len(idx_voisins)} voisins pondérés par 1/distance²")

    legende_carte(ax, [
        Line2D([0], [0], marker="*", color=SURFACE, markerfacecolor=SAUMON, markersize=14,
              label="Segment cible (sans mesure)"),
        Line2D([0], [0], marker="o", color=SURFACE, markerfacecolor=BLEU_NOEUD, markersize=9,
              label="Voisins utilisés (IDW)"),
        Line2D([0], [0], color=MUET, lw=2, label="Poids ∝ épaisseur du trait"),
    ])

    fig.savefig(FIG_DIR / "knn_geographique.png", dpi=150)
    plt.close(fig)
    print(f"  knn_geographique.png (segment {ID_SEGMENT_KNN}, {len(idx_voisins)} voisins)")


# ============================================================
# FIGURE 5 — Évolution de la complétude à travers la cascade
# ============================================================

ETAPES_CASCADE = ["Brut", "+ Interpolation /\nextrapolation", "+ RandomForest", "+ KNN géo"]


def calculer_evolution_completion(deb: gpd.GeoDataFrame, stats: dict) -> dict:
    """% de segments complets (DJMA, %camion) après chaque étape de la cascade de complétion.

    Rejoue phase1_temporelle / mice_cam / phase2_geographique (les mêmes fonctions
    que le pipeline de production) sur une copie, en mesurant la complétude réelle
    (comptage direct des NaN restants) après chacune — pas les étiquettes internes
    methode_djma/methode_cam, pour rester vrai même si leur sémantique évolue.
    """
    print("\n[Évolution complétion] Rejeu séquentiel des 3 étapes de la cascade...")

    pct_djma = [float(stats["pct_djma"]["Complet"])]
    pct_cam  = [float(stats["pct_cam"]["Complet"])]

    gdf = phase1_temporelle(deb.copy())
    pct_djma.append(100 * gdf[DJMA_COLS].notna().all(axis=1).mean())
    pct_cam.append(100 * gdf[CAM_COLS].notna().all(axis=1).mean())

    gdf = mice_cam(gdf)
    pct_djma.append(pct_djma[-1])   # le RandomForest ne complète que %camion, jamais DJMA
    pct_cam.append(100 * gdf[CAM_COLS].notna().all(axis=1).mean())

    gdf = phase2_geographique(gdf)
    pct_djma.append(100 * gdf[DJMA_COLS].notna().all(axis=1).mean())
    pct_cam.append(100 * gdf[CAM_COLS].notna().all(axis=1).mean())

    return {"etapes": ETAPES_CASCADE, "pct_djma": pct_djma, "pct_cam": pct_cam}


def figure_evolution_completion(evolution: dict) -> None:
    """Évolution du % de segments complets, DJMA vs %camions, à travers la cascade."""
    etapes   = evolution["etapes"]
    pct_djma = evolution["pct_djma"]
    pct_cam  = evolution["pct_cam"]
    x = list(range(len(etapes)))

    fig, ax = plt.subplots(figsize=(7.5, 5.5))

    for serie, couleur, label in [(pct_djma, BLEU_NOEUD, "DJMA"), (pct_cam, SAUMON, "% camions")]:
        ax.plot(x, serie, color=couleur, linewidth=2, solid_capstyle="round", zorder=3,
                marker="o", markersize=8, markerfacecolor=couleur,
                markeredgecolor=SURFACE, markeredgewidth=2, label=label)
        for xi, yi in zip(x, serie):
            etiquette = f"{yi:.0f} %".replace(".", ",")
            ax.annotate(etiquette, (xi, yi), textcoords="offset points", xytext=(0, 10),
                       ha="center", fontsize=9, fontweight="bold", color=ENCRE, zorder=4)

    ax.set_title("Évolution de la complétude des données — DJMA vs % camions", fontsize=13,
                fontweight="bold", color=ENCRE, pad=14)
    ax.set_xlim(-0.3, len(etapes) - 0.7)
    ax.set_xticks(x)
    ax.set_xticklabels(etapes, fontsize=9.5, color=ENCRE_2)
    ax.set_ylim(0, 112)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.set_yticklabels([f"{v} %" for v in [0, 25, 50, 75, 100]], fontsize=8.5, color=MUET)
    ax.tick_params(axis="both", length=0)
    ax.yaxis.grid(True, color=GRILLE, linewidth=1, zorder=0)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_visible(False)

    legende_carte(ax, [
        Line2D([0], [0], color=BLEU_NOEUD, lw=2, marker="o", markersize=7, label="DJMA"),
        Line2D([0], [0], color=SAUMON, lw=2, marker="o", markersize=7, label="% camions"),
    ], loc="lower right")

    fig.tight_layout()
    fig.savefig(FIG_DIR / "evolution_completion.png", dpi=150)
    plt.close(fig)
    print(f"  evolution_completion.png  (DJMA {pct_djma[0]:.1f}%→{pct_djma[-1]:.1f}% ; "
          f"%camions {pct_cam[0]:.1f}%→{pct_cam[-1]:.1f}%)")


def main() -> None:
    FIG_DIR.mkdir(exist_ok=True)
    print("Génération des figures de vulgarisation des données...")
    bbox = calculer_bbox_reference()
    fond = charger_fond_geographique(bbox)

    figure_reseau_graphe(bbox, fond)
    figure_reseau_routier(bbox, fond)

    deb = gpd.read_file(PATH_DEBITS).to_crs(CRS_WORK)
    stats = calculer_completude_globale(deb)
    deb["cat_djma"] = stats["cat_djma"]
    figure_comptage_routier(bbox, fond, stats, deb)
    figure_comptage_completude(stats)

    figure_extrapolation_gradient()
    figure_knn_geographique()

    evolution = calculer_evolution_completion(deb, stats)
    figure_evolution_completion(evolution)

    print(f"\nTerminé — figures dans {FIG_DIR}")


if __name__ == "__main__":
    main()
