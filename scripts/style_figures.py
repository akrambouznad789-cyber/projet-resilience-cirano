"""
style_figures.py
=================
Projet CIRANO — Style partagé pour toutes les figures matplotlib du README

Source unique pour la police (Arimo), la palette (fond de carte neutre façon
Google/Apple Maps, couleurs de données pastel) et les en-têtes titre+sous-titre,
réutilisée par generer_figure_donnees.py ET generer_figures_resultats.py.

Avant ce module, les deux scripts redéfinissaient indépendamment les mêmes
constantes (cf. historique de generer_figures_resultats.py, qui évitait
d'importer generer_figure_donnees.py pour ne pas redéclencher un téléchargement
de police / un rcParams global au chargement) — ce qui a fait dériver les deux
jeux de figures visuellement l'un de l'autre. Importer ce module n'a aucun
effet de bord ; seul un appel explicite à appliquer_style() charge la police et
modifie rcParams, donc chaque script garde le contrôle du moment où ça arrive.

Voir aussi la mémoire feedback_dataviz_style (fond neutre, palette pastel,
police Arimo, légendes toujours encadrées) — figée le 2026-07-23.
"""

import urllib.request
from pathlib import Path

import matplotlib.pyplot as plt

RACINE        = Path(__file__).resolve().parent.parent
DIR_REFERENCE = RACINE / "data" / "reference"
DIR_FONTS     = DIR_REFERENCE / "fonts"

POLICES = {
    DIR_FONTS / "Arimo-Regular.ttf": "https://fonts.gstatic.com/s/arimo/v36/P5sfzZCDf9_T_3cV7NCUECyoxNk37cxsBw.ttf",
    DIR_FONTS / "Arimo-Bold.ttf":    "https://fonts.gstatic.com/s/arimo/v36/P5sfzZCDf9_T_3cV7NCUECyoxNk3CstsBw.ttf",
}

CRS_WORK = "EPSG:32198"
MARGE_M  = 20_000

# --- Fond de carte neutre, façon Google/Apple Maps ----------------------
# Une seule teinte terre pour le territoire d'intérêt ET ses voisins ; le
# Québec se distingue uniquement par un contour plus marqué (jamais par un
# remplissage différent). La couleur reste réservée aux données.
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

# --- Les données portent la couleur : palette pastel claire -------------
BLEU_ARC   = "#b7c9f2"   # ton très clair — liens du graphe
BLEU_NOEUD = "#5b7fdb"   # ton plein mais doux — nœuds du graphe
BLEU_450   = "#2a78d6"   # séquentiel — DJMA (carte_reseau_resultats), série m1

COULEURS_CLASSE = {
    "Autoroute":   "#f2a0b5",   # rose pastel
    "Nationale":   "#8fa8f0",   # bleu pastel
    "Régionale":   "#7ecfb0",   # vert d'eau pastel
    "Collectrice": "#b6a8d9",   # mauve pastel
}
COULEUR_AUTRE  = "#cfcabd"
COULEURS_ROUTE = {**COULEURS_CLASSE, "Autre": COULEUR_AUTRE}

COMPLETUDE = {   # clair et doux — pas de rouge/jaune/vert agressifs
    "Complet": "#7fe0c0",
    "Partiel": "#fcd675",
    "Vide":    "#f7a19a",
}
ORDRE_COMPLETUDE = ["Complet", "Partiel", "Vide"]

# Paire catégorielle DJMA / %camions — validée (validate_palette.js, mode light) :
# CVD adjacent ΔE 17.3 (protan), normal-vision ΔE 24.3, PASS.
SAUMON = "#e0857f"

COULEURS_M = {"m1": "#2a78d6", "m2": "#008300", "m3": "#e87ba4", "m4": "#eda100"}
CRITIQUE   = "#d03b3b"   # carte de divergence — écart de méthode le plus fort

# Statut/échec sur une carte déjà colorée par une donnée continue (DJMA) :
# teinte complémentaire/opposée sur le cercle chromatique (orange face au bleu),
# claire plutôt que sombre — jamais rouge/vert saturé "feu de circulation".
ECHEC_INTRA   = "#f2a765"   # échec — aucune station MTQ à proximité (intraurbain)
ECHEC_HORS_QC = "#c96f2e"   # échec — tracé hors territoire québécois


def charger_polices() -> None:
    """Télécharge (une fois, mis en cache) Arimo — clone libre d'Helvetica/Arial."""
    import matplotlib.font_manager as fm
    DIR_FONTS.mkdir(parents=True, exist_ok=True)
    for chemin, url in POLICES.items():
        if not chemin.exists():
            urllib.request.urlretrieve(url, chemin)
        fm.fontManager.addfont(str(chemin))


def appliquer_style() -> None:
    """Police + rcParams communs à toutes les figures — à appeler une fois,
    explicitement, en tête de chaque script de figures (pas d'effet de bord
    au simple import de ce module)."""
    charger_polices()
    plt.rcParams.update({
        "font.family": "Arimo",
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
        "text.color": ENCRE,
        "axes.edgecolor": BASELINE,
        "axes.labelcolor": ENCRE,
        "xtick.color": MUET,
        "ytick.color": MUET,
    })


def entete_carte(ax, titre: str, sous_titre: str) -> None:
    """En-tête titre+sous-titre pour une carte (axis off) : positionné au-dessus
    des données via les coordonnées de l'axe, pas de la figure."""
    ax.text(0.0, 1.07, titre, fontsize=15, fontweight="bold", color=ENCRE, transform=ax.transAxes)
    ax.text(0.0, 1.02, sous_titre, fontsize=10.5, color=ENCRE_2, transform=ax.transAxes)


def entete_figure(fig, ax, titre: str, sous_titre: str | None = None, y: float = 0.99) -> None:
    """En-tête titre+sous-titre pour un graphique (axes visibles : nuage de
    points, colonnes, courbes) — même hiérarchie visuelle que entete_carte()
    (titre gras ENCRE, sous-titre plus clair ENCRE_2), via fig.suptitle/ax.set_title."""
    fig.suptitle(titre, fontsize=13, fontweight="bold", color=ENCRE, y=y)
    if sous_titre:
        ax.set_title(sous_titre, fontsize=10, color=ENCRE_2, pad=10)
    else:
        ax.set_title("")


def legende_carte(ax, handles: list, titre: str | None = None, loc: str = "lower right") -> None:
    """Légende encadrée (fond plein), pour ne pas se fondre dans la carte/le graphique."""
    leg = ax.legend(handles=handles, loc=loc, fontsize=9.5, frameon=True,
                    facecolor=SURFACE, edgecolor=BASELINE, framealpha=1.0, title=titre)
    leg.get_frame().set_linewidth(0.8)
    if titre:
        leg.get_title().set_fontweight("bold")
