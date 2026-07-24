"""
generer_figure_donnees.py
==========================
Projet CIRANO — Figures de vulgarisation des données (README, section « Données »)

Génère 4 figures dans figures/, cadrées sur la même emprise et le même gabarit
(comme si elles pouvaient se superposer), sur un fond de carte neutre façon
Google/Apple Maps (terre unie, eau bleue — Natural Earth) : la couleur reste
réservée aux données (lignes/points), pas au fond :
  - reseau_graphe.png      : nœuds et liens du graphe simplifié
  - reseau_routier.png     : réseau routier MTQ (RTSS), coloré par type de route
  - comptage_routier.png   : stations de comptage MTQ, colorées par complétude DJMA
  - comptage_completude.png : complétude DJMA vs %camion (colonnes empilées)

Les dictionnaires de variables et tableaux de répartition correspondants sont
présentés en texte dans le README, pas dans les figures.
"""

import urllib.request
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.lines import Line2D

RACINE  = Path(__file__).resolve().parent.parent
FIG_DIR = RACINE / "figures"

PATH_ARCS   = RACINE / "data" / "raw" / "reseau_arcs.gpkg"
PATH_RTSS   = RACINE / "data" / "raw" / "ReseauRoutier_RTSS.gpkg"
PATH_DEBITS = RACINE / "data" / "raw" / "DebitCirculation.gpkg"

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

    print(f"\nTerminé — figures dans {FIG_DIR}")


if __name__ == "__main__":
    main()
