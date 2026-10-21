"""
make_maps.py
============
Projet CIRANO — Portfolio cartographique (candidature « Bilingual Spatial Data
Scientist », CPCS)

Produit, à partir des sorties du Volet 1 et sans rien modifier :
  - 5 figures PNG à 300 dpi (portfolio/png/) ;
  - un seul PDF, une figure par page + 3 lignes (question / données et méthode /
    décision) : portfolio/portfolio_Akram_Bouznad.pdf ;
  - une carte interactive folium publiable sur GitHub Pages :
    portfolio/carte_interactive.html ;
  - portfolio/chiffres.txt : TOUS les nombres cités dans les titres, légendes
    et notes, calculés ici (aucun chiffre écrit à la main dans le texte).

Entrées en lecture seule : data/processed/graphe_routier_djma.gpkg,
data/raw/reseau_arcs.gpkg, data/processed/debits_completes.gpkg,
data/raw/DebitCirculation.gpkg, data/reference/ (Natural Earth, police Arimo).
Aucune donnée ENR (microdonnées sous entente) n'est lue : la figure 5 est un
schéma de méthode sur un réseau fictif.

Texte des cartes en anglais. Thème propre au portfolio (viridis pour le trafic
et la provenance, magma pour la sensibilité, fond Natural Earth neutre) —
distinct du style pastel des figures du dépôt (scripts/style_figures.py).

Usage :
    ~/.venvs/portfolio/bin/python portfolio/make_maps.py
"""

from __future__ import annotations

import logging
import sys
import textwrap
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import matplotlib
import matplotlib.text
matplotlib.use("Agg")

import folium
import geopandas as gpd
import mapclassify
import matplotlib.colors as mcolors
import matplotlib.font_manager as fm
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pyogrio
from matplotlib.axes import Axes
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.figure import Figure, SubFigure
from matplotlib.lines import Line2D
from matplotlib.patches import FancyBboxPatch, Patch, Rectangle
from pyproj import Transformer
from shapely.geometry import LineString, Point, Polygon, box

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "scripts"))
from calcul_djma_methodes import PERCENTILE_M4  # noqa: E402  (même règle de rang que m4)

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning)
# « Ignoring fixed y limits… » : attendu, l'aspect 1:1 élargit volontairement une des deux emprises
logging.getLogger("matplotlib.axes._base").setLevel(logging.ERROR)

# ============================================================
# CHEMINS
# ============================================================

FICHIER_ARCS      = RACINE / "data" / "processed" / "graphe_routier_djma.gpkg"
COUCHE_ARCS       = "arcs_enrichis_djma"
FICHIER_GRAPHE    = RACINE / "data" / "raw" / "reseau_arcs.gpkg"
FICHIER_COMPLETES = RACINE / "data" / "processed" / "debits_completes.gpkg"
FICHIER_BRUT      = RACINE / "data" / "raw" / "DebitCirculation.gpkg"
DIR_REFERENCE     = RACINE / "data" / "reference"
NE_ADMIN          = DIR_REFERENCE / "ne_50m_admin_1_states_provinces.zip"
NE_OCEAN          = DIR_REFERENCE / "ne_50m_ocean.zip"
NE_LACS           = DIR_REFERENCE / "ne_50m_lakes.zip"
POLICES           = [DIR_REFERENCE / "fonts" / "Arimo-Regular.ttf",
                     DIR_REFERENCE / "fonts" / "Arimo-Bold.ttf"]

DIR_PORTFOLIO   = RACINE / "portfolio"
DIR_PNG         = DIR_PORTFOLIO / "png"
SORTIE_PDF      = DIR_PORTFOLIO / "portfolio_Akram_Bouznad.pdf"
SORTIE_CHIFFRES = DIR_PORTFOLIO / "chiffres.txt"
SORTIE_HTML     = DIR_PORTFOLIO / "carte_interactive.html"

# ============================================================
# PARAMÈTRES
# ============================================================

CRS               = "EPSG:32198"   # NAD83 / Québec Lambert
RAYON_CORRIDOR_M  = 50_000         # corridor = 50 km de part et d'autre de l'axe Montréal–Québec
SEUIL_ECART_PCT   = 30             # sensibilité : écart (max − min) / moyenne m1-m4
SEUIL_ECART_FORT  = 60
N_CLASSES         = 5              # classes de Jenks (Fisher-Jenks) sur le DJMA m4
DPI               = 300
TAILLE_CARTE      = (11.0, 7.0)    # pouces — identique à la zone carte d'une page PDF
TAILLE_PAGE       = (11.0, 8.5)    # lettre US paysage
HAUTEUR_NOTES     = TAILLE_PAGE[1] - TAILLE_CARTE[1]

EMPRISE_QC     = (-880_000, 85_000, 600_000, 1_165_000)   # xmin, ymin, xmax, ymax (m)
DEMI_ENCART_MTL = (46_000, 36_000)                        # demi-largeur, demi-hauteur (m)

URL_DEPOT = "github.com/akrambouznad789-cyber/projet-resilience-cirano"
SOURCE_MTMD = ("Data: MTMD traffic counts (AADT), Données Québec, CC BY 4.0 · Basemap: Natural Earth.  "
               "Analysis: A. Bouznad, CIRANO / Polytechnique Montréal")

# ============================================================
# THÈME DU PORTFOLIO
# ============================================================

SURFACE    = "#fcfcfb"
ENCRE      = "#0b0b0b"
ENCRE_2    = "#52514e"
MUET       = "#898781"
BASELINE   = "#c3c2b7"
EAU        = "#d3e5f2"
TERRE      = "#f1efe6"
BORDURE    = "#dcd8cb"
BORDURE_QC = "#9aa5b1"
GRIS_LIEN  = "#c9c6bb"
GRIS_VIDE  = "#9a978c"


def _viridis(p: float) -> str:
    return mcolors.to_hex(matplotlib.colormaps["viridis"](p))


def _magma(p: float) -> str:
    return mcolors.to_hex(matplotlib.colormaps["magma"](p))


# Viridis tronqué à 0,70 : au-delà, le vert-jaune passe sous 2:1 de contraste
# sur la terre crème (#f1efe6) et les arcs à faible trafic disparaîtraient.
COUL_TRAFIC = [_viridis(p) for p in (0.70, 0.52, 0.35, 0.18, 0.02)]
LARG_TRAFIC = [0.5, 0.9, 1.5, 2.2, 3.0]

COUL_LIEN   = _viridis(0.30)
COUL_VILLE  = _viridis(0.02)
COUL_FRONT  = _viridis(0.52)
COUL_CORR   = _viridis(0.62)

# Provenance : ordinale — plus la valeur s'éloigne d'un comptage MTMD 2025, plus
# le trait est foncé ; tirets/pointillés en codage secondaire (daltonisme, N&B).
PROVENANCE: dict[str, tuple[str, str, float, object]] = {
    "direct":   ("2025 MTMD estimate",                  _viridis(0.70), 1.1, "-"),
    "temporel": ("Filled from earlier MTMD years",      _viridis(0.45), 1.7, "-"),
    "spatial":  ("Imputed from nearby segments (k-NN)", _viridis(0.22), 1.7, "-"),
    "emprunt":  ("Borrowed from the adjacent link",     _viridis(0.00), 1.9, (0, (3, 1.6))),
    "absent":   ("No MTMD data",                        GRIS_VIDE,      1.2, (0, (1, 1.6))),
}

COUL_ECART_MOYEN = _magma(0.62)
COUL_ECART_FORT  = _magma(0.30)

# Trio catégoriel du schéma (3 camions) — 3 premiers slots de la palette de
# référence du skill dataviz, validés « all pairs » en mode clair.
COUL_CAMIONS = ["#2a78d6", "#eb6834", "#1baf7a"]

HALO = [pe.withStroke(linewidth=2.6, foreground=SURFACE)]

NOMS_EN = {
    "Nouveau-Brunswick": "New Brunswick",
    "Terre-Neuve-et-Labrador": "Newfoundland and Labrador",
    "Ile-du-Prince-Edouard": "Prince Edward Island",
    "Nouvelle-Ecosse": "Nova Scotia",
    "Ontario-Sud": "Ontario (south)",
}

# ============================================================
# DONNÉES
# ============================================================


@dataclass
class Donnees:
    """Tout ce que les figures consomment, calculé une seule fois."""
    arcs: gpd.GeoDataFrame
    noeuds: gpd.GeoDataFrame
    fond: dict[str, gpd.GeoDataFrame]
    corridor: Polygon
    montreal: Point
    classes: list[tuple[float, float, int]]   # (min, max, n) par classe de Jenks


def appliquer_theme() -> None:
    """Police Arimo (cache du dépôt) + rcParams du portfolio."""
    for chemin in POLICES:
        fm.fontManager.addfont(str(chemin))
    plt.rcParams.update({
        "font.family": "Arimo",
        "mathtext.fontset": "dejavusans",
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
        "text.color": ENCRE,
        "pdf.fonttype": 42,
    })


def reparer_nom(nom: str) -> str:
    """Corrige le double encodage UTF-8/Latin-1 du fichier brut et retire le
    préfixe en syllabaire (illisible par Arimo) : « ᒋᓵᓰᐲ Chisasibi » → « Chisasibi »."""
    if not isinstance(nom, str):
        return nom
    try:
        nom = nom.encode("latin1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    mots = [m for m in nom.split() if not any(0x1400 <= ord(ch) <= 0x167F for ch in m)]
    return " ".join(mots) or nom


def nom_affiche(nom: str) -> str:
    """Nom lisible en anglais pour les nœuds frontière et intermédiaires."""
    if nom.startswith("Noeud_"):
        return f"Junction ({nom.removeprefix('Noeud_').replace('_', ' ')})"
    return NOMS_EN.get(nom, nom)


def segment_retenu_m4(row: pd.Series) -> tuple[int, float] | None:
    """Identifiant et valeur du segment qui fixe le DJMA m4 de l'arc — même
    tri stable et même rang le plus proche que calcul_djma_methodes.calculer_m4."""
    if pd.isna(row["ids_segs_djma_val"]):
        return None
    ids = str(row["ids_segs_djma_id"]).split("|")
    vals = str(row["ids_segs_djma_val"]).split("|")
    paires = [(float(v.split("@")[0]), int(i)) for i, v in zip(ids, vals)
              if v.strip() and v.strip().upper() != "NA"]
    if not paires:
        return None
    n = len(paires)
    rang = min(max(round(PERCENTILE_M4 * n), 1), n)
    valeur, id_seg = sorted(paires, key=lambda p: p[0])[rang - 1]
    return id_seg, valeur


def charger_segments() -> pd.DataFrame:
    """Valeur 2025 complétée + méthode de complétion (debits_completes) et
    valeur 2025 brute publiée par le MTMD (DebitCirculation), par segment."""
    cols_comp = ["ide_sectn_trafc", "val_djma_annee_1", "val_cam_annee_1", "methode_djma", "methode_cam"]
    comp = pyogrio.read_dataframe(FICHIER_COMPLETES, columns=cols_comp, read_geometry=False)
    brut = pyogrio.read_dataframe(FICHIER_BRUT, columns=cols_comp[:3], read_geometry=False)
    for df in (comp, brut):
        for col in ("val_djma_annee_1", "val_cam_annee_1"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
    brut = brut.rename(columns={"val_djma_annee_1": "brut_djma", "val_cam_annee_1": "brut_cam"})
    return comp.merge(brut, on="ide_sectn_trafc", how="left").set_index("ide_sectn_trafc")


def classer_provenance(arcs: gpd.GeoDataFrame, segs: pd.DataFrame) -> gpd.GeoDataFrame:
    """Provenance de la valeur DJMA (et du % camions) réellement retenue par arc.

    direct   : le segment retenu a une valeur 2025 publiée par le MTMD, inchangée
    temporel : comblé à partir des autres années du segment (inter/extrapolation)
    spatial  : segment sans aucune année publiée, imputé par k-NN géographique
    emprunt  : arc sans segment retenu, valeur du plus proche arc voisin
    absent   : aucune donnée MTMD
    """
    arcs = arcs.copy()
    prov, prov_cam = [], []
    for _, row in arcs.iterrows():
        if row["statut"] == "aucun_djma":
            prov.append("emprunt"); prov_cam.append("Borrowed from the adjacent link"); continue
        if row["statut"] != "ok":
            prov.append("absent"); prov_cam.append("No MTMD data"); continue
        id_seg, valeur = segment_retenu_m4(row)
        assert round(valeur) == row["djma_m4"], f"arc {row['ID_ARC']} : rang m4 non reproduit"
        s = segs.loc[id_seg]
        if pd.notna(s["brut_djma"]) and abs(s["brut_djma"] - s["val_djma_annee_1"]) < 0.5:
            prov.append("direct")
        elif s["methode_djma"] == "geo_knn":
            prov.append("spatial")
        else:
            prov.append("temporel")
        if pd.notna(s["brut_cam"]) and abs(s["brut_cam"] - s["val_cam_annee_1"]) < 0.05:
            prov_cam.append("2025 MTMD estimate")
        else:
            prov_cam.append({"mice": "Modelled (random forest)",
                             "geo_knn": "Imputed from nearby segments (k-NN)"}
                            .get(s["methode_cam"], "Filled from earlier MTMD years"))
    arcs["prov"] = prov
    arcs["prov_cam"] = prov_cam
    return arcs


def charger_fond() -> dict[str, gpd.GeoDataFrame]:
    """Fond Natural Earth (cache du dépôt), découpé en WGS84 avant reprojection
    pour éviter les artefacts des polygones mondiaux en Lambert conique."""
    fenetre = box(-92, 38, -46, 66)
    admin = gpd.read_file(NE_ADMIN)
    admin = admin[admin["admin"].isin(["Canada", "United States of America"])]
    admin = gpd.clip(admin, fenetre).to_crs(CRS)
    ocean = gpd.clip(gpd.read_file(NE_OCEAN), fenetre).to_crs(CRS)
    lacs = gpd.clip(gpd.read_file(NE_LACS), fenetre).to_crs(CRS)
    return {"quebec": admin[admin["name"] == "Québec"], "autres": admin[admin["name"] != "Québec"],
            "ocean": ocean, "lacs": lacs}


def charger_donnees() -> Donnees:
    """Charge, nettoie et enrichit arcs et nœuds (aucune écriture sur les sources)."""
    arcs = gpd.read_file(FICHIER_ARCS, layer=COUCHE_ARCS)
    arcs["nom_a"] = arcs["VILLE_A"].map(reparer_nom).map(nom_affiche)
    arcs["nom_b"] = arcs["VILLE_B"].map(reparer_nom).map(nom_affiche)

    noeuds = gpd.read_file(FICHIER_GRAPHE, layer="noeuds").set_crs(CRS, allow_override=True)
    noeuds["nom"] = noeuds["NOM"].map(reparer_nom)
    noeuds["nom_en"] = noeuds["nom"].map(nom_affiche)
    noeuds["relie"] = noeuds["ID"].isin(set(arcs["ID_A"]) | set(arcs["ID_B"]))

    arcs = classer_provenance(arcs, charger_segments())

    m = arcs[["djma_m1", "djma_m2", "djma_m3", "djma_m4"]].astype(float)
    arcs["ecart_pct"] = np.where(arcs["statut"] == "ok",
                                 (m.max(axis=1) - m.min(axis=1)) / m.mean(axis=1) * 100, np.nan)

    montreal = noeuds.loc[noeuds["nom"] == "Montréal", "geometry"].iloc[0]
    quebec = noeuds.loc[noeuds["nom"] == "Québec", "geometry"].iloc[0]
    corridor = LineString([montreal, quebec]).buffer(RAYON_CORRIDOR_M)
    milieux = arcs.geometry.interpolate(0.5, normalized=True)
    arcs["dans_corridor"] = milieux.within(corridor)
    arcs["dist_mtl_m"] = milieux.distance(montreal)

    avec = arcs["djma_m4"].notna()
    fj = mapclassify.FisherJenks(arcs.loc[avec, "djma_m4"].astype(float).values, k=N_CLASSES)
    arcs["classe"] = pd.Series(pd.NA, index=arcs.index, dtype="Int64")
    arcs.loc[avec, "classe"] = fj.yb
    classes = []
    for k in range(N_CLASSES):
        v = arcs.loc[arcs["classe"] == k, "djma_m4"].astype(float)
        classes.append((v.min(), v.max(), len(v)))

    return Donnees(arcs=arcs, noeuds=noeuds, fond=charger_fond(), corridor=corridor,
                   montreal=montreal, classes=classes)


# ============================================================
# CHIFFRES (source unique de tous les nombres cités)
# ============================================================


def calculer_chiffres(d: Donnees) -> dict[str, tuple[object, str]]:
    """Calcule chaque nombre cité dans une figure ou une note → {clé: (valeur, description)}."""
    a, n = d.arcs, d.noeuds
    villes = n[n["TYPE"] == "municipalite"]
    villes_reliees = villes[villes["relie"]]
    avec = a[a["djma_m4"].notna()].copy()
    avec["vkm"] = avec["djma_m4"].astype(float) * avec["longueur_qc_km"]
    corr = avec["dans_corridor"]
    ok = a[a["statut"] == "ok"]
    ecart = ok["ecart_pct"]
    top = avec.loc[avec["djma_m4"].astype(float).idxmax()]
    prov = a["prov"].value_counts()

    c: dict[str, tuple[object, str]] = {
        "n_cities": (len(villes), "municipalities in the graph"),
        "n_cities_linked": (len(villes_reliees), "municipalities with at least one link"),
        "n_cities_unlinked": (len(villes) - len(villes_reliees), "municipalities with no link (mostly Nunavik)"),
        "n_junctions": (int((n["TYPE"] == "noeud_intermediaire").sum()), "junction nodes"),
        "n_borders_linked": (int((n["TYPE"].str.startswith("frontiere") & n["relie"]).sum()), "border gateways with a link"),
        "n_links": (len(a), "links in the graph"),
        "n_links_aadt": (len(avec), "links with an AADT value"),
        "corridor_km": (RAYON_CORRIDOR_M // 1000, "corridor half-width around the Montréal–Québec axis (km)"),
        "n_cities_corridor": (int(villes_reliees.within(d.corridor).sum()), "linked cities inside the corridor"),
        "n_links_corridor": (int(corr.sum()), "links with an AADT value whose midpoint is inside the corridor"),
        "median_aadt_corridor": (int(avec.loc[corr, "djma_m4"].median()), "median AADT, corridor links (veh/day)"),
        "median_aadt_rest": (int(avec.loc[~corr, "djma_m4"].median()), "median AADT, other links (veh/day)"),
        "pct_vkm_corridor": (round(100 * avec.loc[corr, "vkm"].sum() / avec["vkm"].sum(), 1),
                             "share of vehicle-km on corridor links (AADT × routed length in Quebec), %"),
        "pct_length_corridor": (round(100 * avec.loc[corr, "longueur_qc_km"].sum() / avec["longueur_qc_km"].sum(), 1),
                                "share of routed link length on corridor links, %"),
        "max_aadt": (int(top["djma_m4"]), f"highest link AADT ({top['nom_a']} – {top['nom_b']})"),
        "n_aadt_ge_50k": (int((avec["djma_m4"] >= 50_000).sum()), "links with AADT ≥ 50,000"),
        "n_aadt_ge_50k_mtl": (int(((avec["djma_m4"] >= 50_000) & (avec["dist_mtl_m"] < 45_000)).sum()),
                              "of which midpoint within 45 km of Montréal"),
        "median_segments": (int(ok["n_segs_djma"].median()), "median MTMD segments matched per routed link"),
    }
    for k, (vmin, vmax, nk) in enumerate(d.classes, start=1):
        c[f"jenks_class_{k}"] = (f"{vmin:,.0f}–{vmax:,.0f} ({nk} links)", f"AADT natural-breaks class {k}")
    for cle, (libelle, *_rest) in PROVENANCE.items():
        c[f"prov_{cle}"] = (int(prov.get(cle, 0)), f"links whose AADT is: {libelle}")
    c["n_imputed"] = (int(prov.get("temporel", 0) + prov.get("spatial", 0)), "links whose AADT is imputed (time or space)")
    c["pct_direct"] = (round(100 * prov.get("direct", 0) / len(a), 1), "share of links on a 2025 MTMD estimate, %")
    c["n_truck_2025"] = (int((ok["prov_cam"] == "2025 MTMD estimate").sum()), "routed links whose truck share is a 2025 MTMD estimate")
    c["n_truck_rf"] = (int((ok["prov_cam"] == "Modelled (random forest)").sum()), "routed links whose truck share is modelled (random forest)")
    c["n_links_compared"] = (len(ok), "links compared across methods (own matched segments)")
    c["n_spread_gt30"] = (int((ecart > SEUIL_ECART_PCT).sum()), f"links with spread > {SEUIL_ECART_PCT}%")
    c["n_spread_30_60"] = (int(((ecart > SEUIL_ECART_PCT) & (ecart <= SEUIL_ECART_FORT)).sum()),
                           f"links with spread {SEUIL_ECART_PCT}–{SEUIL_ECART_FORT}%")
    c["n_spread_gt60"] = (int((ecart > SEUIL_ECART_FORT).sum()), f"links with spread > {SEUIL_ECART_FORT}%")
    c["n_spread_le30"] = (int((ecart <= SEUIL_ECART_PCT).sum()), f"links with spread ≤ {SEUIL_ECART_PCT}%")
    c["median_spread"] = (round(float(ecart.median()), 1), "median spread across the 4 methods, %")
    c["median_segs_gt30"] = (int(ok.loc[ecart > SEUIL_ECART_PCT, "n_segs_djma"].median()), "median segments, links with spread > 30%")
    c["median_segs_le30"] = (int(ok.loc[ecart <= SEUIL_ECART_PCT, "n_segs_djma"].median()), "median segments, links with spread ≤ 30%")
    c["n_excluded_sensitivity"] = (len(a) - len(ok), "links excluded from the comparison (borrowed or no data)")
    return c


def ecrire_chiffres(c: dict[str, tuple[object, str]]) -> None:
    """portfolio/chiffres.txt — trace de chaque nombre cité dans le portfolio."""
    largeur = max(len(k) for k in c)
    lignes = ["# Every number quoted in the portfolio maps and notes, computed by make_maps.py",
              "# key = value    # description", ""]
    for cle, (valeur, desc) in c.items():
        v = f"{valeur:,}" if isinstance(valeur, int) else str(valeur)
        lignes.append(f"{cle:<{largeur}} = {v:<28} # {desc}")
    SORTIE_CHIFFRES.write_text("\n".join(lignes) + "\n", encoding="utf-8")


def v(c: dict[str, tuple[object, str]], cle: str) -> str:
    """Valeur formatée d'un chiffre (séparateur de milliers)."""
    valeur = c[cle][0]
    return f"{valeur:,}" if isinstance(valeur, int) else str(valeur)


# ============================================================
# HABILLAGE COMMUN
# ============================================================


def tracer_fond(ax: Axes, fond: dict[str, gpd.GeoDataFrame]) -> None:
    """Fond neutre : eau bleu pâle, terre crème, contour du Québec plus marqué."""
    ax.set_facecolor(EAU)
    fond["autres"].plot(ax=ax, color=TERRE, edgecolor=BORDURE, linewidth=0.5, zorder=0)
    fond["quebec"].plot(ax=ax, color=TERRE, edgecolor=BORDURE_QC, linewidth=1.0, zorder=0)
    fond["lacs"].plot(ax=ax, color=EAU, linewidth=0, zorder=0)


def cadrer(ax: Axes, emprise: tuple[float, float, float, float], couleur: str = BASELINE) -> None:
    """Emprise, aspect 1:1 et cadre fin sans graduations."""
    ax.set_xlim(emprise[0], emprise[2])
    ax.set_ylim(emprise[1], emprise[3])
    ax.set_aspect("equal", adjustable="datalim")
    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values():
        s.set_edgecolor(couleur); s.set_linewidth(0.8)


def emprise_autour(p: Point, demi: tuple[float, float]) -> tuple[float, float, float, float]:
    return (p.x - demi[0], p.y - demi[1], p.x + demi[0], p.y + demi[1])


def entete(fig: Figure | SubFigure, titre: str, sous_titre: str) -> None:
    fig.text(0.02, 0.955, titre, fontsize=17, fontweight="bold", color=ENCRE, va="top")
    fig.text(0.02, 0.905, sous_titre, fontsize=10.5, color=ENCRE_2, va="top")


def pied(fig: Figure | SubFigure, source: str = SOURCE_MTMD) -> None:
    fig.text(0.02, 0.028, source, fontsize=8, color=MUET, va="bottom")


def echelle(ax: Axes, x0: float, y0: float, km: int = 200) -> None:
    """Barre d'échelle en deux tronçons alternés (EPSG:32198, facteur d'échelle ≈ 1)."""
    h = (ax.get_ylim()[1] - ax.get_ylim()[0]) * 0.011
    pas = km * 1000 / 2
    for i in range(2):
        ax.add_patch(Rectangle((x0 + i * pas, y0), pas, h, facecolor=ENCRE if i == 0 else SURFACE,
                               edgecolor=ENCRE, linewidth=0.6, zorder=20))
    for i, lab in enumerate(["0", f"{km // 2}", f"{km} km"]):
        ax.text(x0 + i * pas, y0 - h * 0.9, lab, fontsize=8, color=ENCRE_2, ha="center", va="top",
                path_effects=HALO, zorder=20)


def fleche_nord(ax: Axes, x: float, y: float, longueur: float = 55_000) -> None:
    """Flèche vers le nord géographique au point (x, y) — en Lambert conique, le
    nord de la grille diffère du nord vrai selon la longitude, on le calcule."""
    vers_geo = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)
    vers_proj = Transformer.from_crs("EPSG:4326", CRS, always_xy=True)
    lon, lat = vers_geo.transform(x, y)
    x2, y2 = vers_proj.transform(lon, lat + 0.5)
    ux, uy = (x2 - x), (y2 - y)
    norme = np.hypot(ux, uy)
    xt, yt = x + ux / norme * longueur, y + uy / norme * longueur
    ax.annotate("", xy=(xt, yt), xytext=(x, y), zorder=20,
                arrowprops=dict(arrowstyle="-|>,head_width=0.35,head_length=0.7", color=ENCRE, lw=1.2))
    ax.text(xt, yt + longueur * 0.18, "N", fontsize=10, fontweight="bold", ha="center", va="bottom",
            color=ENCRE, path_effects=HALO, zorder=20)


def etiqueter(ax: Axes, noeuds: gpd.GeoDataFrame, places: dict[str, tuple[float, float, str]],
              taille: float = 8.5, point: bool = True) -> None:
    """Étiquettes sélectives de villes : {nom: (dx pt, dy pt, ha)}."""
    for nom, (dx, dy, ha) in places.items():
        sel = noeuds[noeuds["nom"] == nom]
        if sel.empty:
            continue
        p = sel.geometry.iloc[0]
        if point:
            ax.scatter([p.x], [p.y], s=9, color=ENCRE_2, edgecolor=SURFACE, linewidth=0.5, zorder=9)
        ax.annotate(nom, xy=(p.x, p.y), xytext=(dx, dy), textcoords="offset points", ha=ha, va="center",
                    fontsize=taille, color=ENCRE, path_effects=HALO, zorder=10)


def marquer_encart(ax: Axes, ax_encart: Axes) -> None:
    """Rectangle de l'emprise réelle de l'encart sur la carte principale."""
    ax_encart.apply_aspect()
    (x0, x1), (y0, y1) = ax_encart.get_xlim(), ax_encart.get_ylim()
    ax.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, fill=False, edgecolor=ENCRE_2, linewidth=0.9, zorder=15))


def legende(ax_leg: Axes, handles: list, titre: str) -> None:
    """Légende encadrée dans la colonne de droite (jamais par-dessus les données)."""
    ax_leg.set_axis_off()
    leg = ax_leg.legend(handles=handles, title=titre, loc="upper left", bbox_to_anchor=(0, 1),
                        frameon=True, facecolor=SURFACE, edgecolor=BASELINE, framealpha=1, fontsize=9,
                        title_fontsize=9.5, alignment="left", borderpad=0.9, labelspacing=0.7,
                        handlelength=2.6)
    leg.get_frame().set_linewidth(0.8)
    leg.get_title().set_fontweight("bold")


def gabarit(fig: Figure | SubFigure) -> tuple[Axes, Axes, Axes]:
    """Carte principale à gauche ; encart et légende dans la colonne de droite."""
    ax = fig.add_axes([0.015, 0.075, 0.69, 0.80])
    ax_enc = fig.add_axes([0.725, 0.47, 0.26, 0.38])
    ax_leg = fig.add_axes([0.725, 0.075, 0.26, 0.355])
    return ax, ax_enc, ax_leg


def habiller_principale(ax: Axes) -> None:
    cadrer(ax, EMPRISE_QC)
    echelle(ax, -850_000, 125_000)
    fleche_nord(ax, -820_000, 1_020_000)


def titre_encart(ax_enc: Axes, texte: str) -> None:
    ax_enc.set_title(texte, loc="left", fontsize=9.5, color=ENCRE_2, pad=4)


def tracer_arcs(ax: Axes, arcs: gpd.GeoDataFrame, couleur: str, largeur: float,
                style: object = "-", zorder: int = 4) -> None:
    if len(arcs):
        arcs.plot(ax=ax, color=couleur, linewidth=largeur, linestyle=style, zorder=zorder,
                  capstyle="round", joinstyle="round")


VILLES_PRINCIPALES = {
    "Montréal": (-6, -7, "right"), "Québec": (5, 5, "left"), "Gatineau": (-5, -6, "right"),
    "Sherbrooke": (5, -6, "left"), "Saguenay": (5, 4, "left"), "Rimouski": (5, -5, "left"),
    "Sept-Îles": (5, 4, "left"), "Gaspé": (5, 5, "left"), "Val-d'Or": (5, -6, "left"),
    "Rouyn-Noranda": (5, 6, "left"), "Chibougamau": (5, 4, "left"), "Radisson": (5, -4, "left"),
    "Baie-Comeau": (-5, 5, "right"), "Trois-Rivières": (-5, 6, "right"),
}
VILLES_MTL = {"Montréal": (-5, -6, "right"), "Laval": (-5, 6, "right"), "Longueuil": (5, 4, "left"),
              "Saint-Jérôme": (5, 0, "left"), "Châteauguay": (-5, -5, "right")}

# ============================================================
# FIGURE 1 — RÉSEAU
# ============================================================


def carte_reseau(fig: Figure | SubFigure, d: Donnees, c: dict) -> None:
    """Vue d'ensemble : villes, nœuds, arcs, corridor Montréal–Québec en encart."""
    ax, ax_enc, ax_leg = gabarit(fig)
    n = d.noeuds[d.noeuds["relie"]]
    villes = n[n["TYPE"] == "municipalite"]
    jonctions = n[n["TYPE"] == "noeud_intermediaire"]
    frontieres = n[n["TYPE"].str.startswith("frontiere")]
    corr = gpd.GeoSeries([d.corridor], crs=CRS)

    for axe, taille in ((ax, 1.0), (ax_enc, 1.6)):
        tracer_fond(axe, d.fond)
        corr.plot(ax=axe, facecolor=mcolors.to_rgba(COUL_CORR, 0.13), edgecolor=COUL_CORR, linewidth=0.8, zorder=2)
        tracer_arcs(axe, d.arcs, COUL_LIEN, 0.7 * taille)
        axe.scatter(villes.geometry.x, villes.geometry.y, s=7 * taille, color=COUL_VILLE,
                    edgecolor=SURFACE, linewidth=0.4, zorder=6)
        axe.scatter(jonctions.geometry.x, jonctions.geometry.y, s=9 * taille, marker="s", facecolor=SURFACE,
                    edgecolor=COUL_VILLE, linewidth=0.8, zorder=6)
        axe.scatter(frontieres.geometry.x, frontieres.geometry.y, s=30 * taille, marker="^", color=COUL_FRONT,
                    edgecolor=SURFACE, linewidth=0.6, zorder=6)

    habiller_principale(ax)
    etiqueter(ax, d.noeuds, VILLES_PRINCIPALES, point=False)
    xmin, ymin, xmax, ymax = d.corridor.bounds
    cadrer(ax_enc, (xmin - 6_000, ymin - 6_000, xmax + 6_000, ymax + 6_000), couleur=ENCRE_2)
    etiqueter(ax_enc, d.noeuds, {"Montréal": (-5, -7, "right"), "Québec": (5, 5, "left"),
                                 "Trois-Rivières": (-5, 6, "right"), "Drummondville": (5, -6, "left"),
                                 "Saint-Hyacinthe": (5, -6, "left")}, taille=8, point=False)
    titre_encart(ax_enc, "Zoom: Montréal–Québec corridor")
    marquer_encart(ax, ax_enc)

    handles = [
        Line2D([], [], color=COUL_VILLE, marker="o", linestyle="", markersize=5, markeredgecolor=SURFACE,
               label=f"City ({v(c, 'n_cities_linked')})"),
        Line2D([], [], color=COUL_VILLE, marker="s", linestyle="", markersize=5, markerfacecolor=SURFACE,
               label=f"Junction node ({v(c, 'n_junctions')})"),
        Line2D([], [], color=COUL_FRONT, marker="^", linestyle="", markersize=7, markeredgecolor=SURFACE,
               label=f"Border gateway ({v(c, 'n_borders_linked')})"),
        Line2D([], [], color=COUL_LIEN, linewidth=1.2, label=f"Road link ({v(c, 'n_links')})"),
        Patch(facecolor=mcolors.to_rgba(COUL_CORR, 0.13), edgecolor=COUL_CORR,
              label=f"Corridor: {v(c, 'corridor_km')} km either side\nof the Montréal–Québec axis"),
    ]
    legende(ax_leg, handles, "Network elements")
    pct = round(100 * c["n_cities_corridor"][0] / c["n_cities_linked"][0])
    entete(fig, f"{v(c, 'n_cities_corridor')} of {v(c, 'n_cities_linked')} linked cities ({pct}%) "
                f"lie within {v(c, 'corridor_km')} km of the Montréal–Québec axis",
           f"Cities (nodes) joined by direct inter-city road links, drawn as straight lines · "
           f"{v(c, 'n_cities_unlinked')} unlinked communities, mostly in Nunavik, not shown")
    pied(fig, SOURCE_MTMD.replace("Data: MTMD traffic counts (AADT), Données Québec, CC BY 4.0",
                                  "Network: CIRANO road graph"))


# ============================================================
# FIGURE 2 — TRAFIC
# ============================================================


def tracer_trafic(ax: Axes, d: Donnees, facteur: float = 1.0) -> None:
    a = d.arcs
    tracer_arcs(ax, a[a["classe"].isna()], GRIS_VIDE, 0.9 * facteur, (0, (1, 1.6)), zorder=3)
    for k in range(N_CLASSES):
        tracer_arcs(ax, a[a["classe"] == k], COUL_TRAFIC[k], LARG_TRAFIC[k] * facteur, zorder=4 + k)


def carte_trafic(fig: Figure | SubFigure, d: Donnees, c: dict) -> None:
    """DJMA m4 par arc, 5 classes de Jenks (épaisseur + couleur)."""
    ax, ax_enc, ax_leg = gabarit(fig)
    for axe, f in ((ax, 1.0), (ax_enc, 1.25)):
        tracer_fond(axe, d.fond)
        tracer_trafic(axe, d, f)
    gpd.GeoSeries([d.corridor], crs=CRS).plot(ax=ax, facecolor="none", edgecolor=ENCRE_2, linewidth=0.7, zorder=2)
    habiller_principale(ax)
    etiqueter(ax, d.noeuds, VILLES_PRINCIPALES)
    cadrer(ax_enc, emprise_autour(d.montreal, DEMI_ENCART_MTL), couleur=ENCRE_2)
    etiqueter(ax_enc, d.noeuds, VILLES_MTL, taille=8)
    titre_encart(ax_enc, "Zoom: Greater Montréal")
    marquer_encart(ax, ax_enc)

    handles = [Line2D([], [], color=COUL_TRAFIC[k], linewidth=LARG_TRAFIC[k] + 0.4,
                      label=f"{vmin:,.0f} – {vmax:,.0f}   ({nk})")
               for k, (vmin, vmax, nk) in reversed(list(enumerate(d.classes)))]
    handles += [Line2D([], [], color=GRIS_VIDE, linewidth=1.2, linestyle=(0, (1, 1.6)),
                       label=f"No MTMD data   ({v(c, 'prov_absent')})"),
                Line2D([], [], color=ENCRE_2, linewidth=0.8,
                       label=f"Montréal–Québec corridor\n({v(c, 'corridor_km')} km either side of the axis)")]
    legende(ax_leg, handles, "AADT, vehicles/day   (links)")
    entete(fig, f"Montréal–Québec corridor links carry {round(c['pct_vkm_corridor'][0])}% of vehicle-km "
                f"on {round(c['pct_length_corridor'][0])}% of network length",
           "Annual average daily traffic (AADT) per link, 2025, vehicles/day, both directions · "
           "5 natural-breaks (Jenks) classes, line width and colour")
    pied(fig)


# ============================================================
# FIGURE 3 — PROVENANCE
# ============================================================


def tracer_provenance(ax: Axes, d: Donnees, facteur: float = 1.0) -> None:
    for z, (cle, (_lib, coul, larg, style)) in enumerate(PROVENANCE.items()):
        tracer_arcs(ax, d.arcs[d.arcs["prov"] == cle], coul, larg * facteur, style, zorder=4 + z)


def carte_provenance(fig: Figure | SubFigure, d: Donnees, c: dict) -> None:
    """D'où vient la valeur DJMA retenue pour chaque arc."""
    ax, ax_enc, ax_leg = gabarit(fig)
    for axe, f in ((ax, 1.0), (ax_enc, 1.3)):
        tracer_fond(axe, d.fond)
        tracer_provenance(axe, d, f)
    habiller_principale(ax)
    etiqueter(ax, d.noeuds, VILLES_PRINCIPALES)
    cadrer(ax_enc, emprise_autour(d.montreal, DEMI_ENCART_MTL), couleur=ENCRE_2)
    etiqueter(ax_enc, d.noeuds, VILLES_MTL, taille=8)
    titre_encart(ax_enc, "Zoom: Greater Montréal")
    marquer_encart(ax, ax_enc)

    handles = [Line2D([], [], color=coul, linewidth=larg + 0.3, linestyle=style,
                      label=f"{textwrap.fill(lib, 26)}   ({v(c, 'prov_' + cle)})")
               for cle, (lib, coul, larg, style) in PROVENANCE.items()]
    legende(ax_leg, handles, "Source of the link's AADT   (links)")
    entete(fig, f"{v(c, 'prov_direct')} of {v(c, 'n_links')} links rest on a 2025 MTMD traffic estimate; "
                f"{v(c, 'n_imputed')} rely on imputation",
           "Source of the AADT value used for each link, traced back to the MTMD segment that sets it "
           "(the link's 80th-percentile segment)")
    pied(fig)


# ============================================================
# FIGURE 4 — SENSIBILITÉ
# ============================================================


def tracer_ecart(ax: Axes, d: Donnees, facteur: float = 1.0) -> None:
    a = d.arcs
    e = a["ecart_pct"]
    tracer_arcs(ax, a[e.isna()], GRIS_LIEN, 0.6 * facteur, (0, (3, 1.6)), zorder=3)
    tracer_arcs(ax, a[e <= SEUIL_ECART_PCT], GRIS_LIEN, 0.8 * facteur, zorder=4)
    tracer_arcs(ax, a[(e > SEUIL_ECART_PCT) & (e <= SEUIL_ECART_FORT)], COUL_ECART_MOYEN, 1.6 * facteur, zorder=5)
    tracer_arcs(ax, a[e > SEUIL_ECART_FORT], COUL_ECART_FORT, 2.4 * facteur, zorder=6)


def carte_sensibilite(fig: Figure | SubFigure, d: Donnees, c: dict) -> None:
    """Arcs dont le DJMA varie de plus de 30 % selon la méthode d'agrégation."""
    ax, ax_enc, ax_leg = gabarit(fig)
    for axe, f in ((ax, 1.0), (ax_enc, 1.3)):
        tracer_fond(axe, d.fond)
        tracer_ecart(axe, d, f)
    habiller_principale(ax)
    etiqueter(ax, d.noeuds, VILLES_PRINCIPALES)
    cadrer(ax_enc, emprise_autour(d.montreal, DEMI_ENCART_MTL), couleur=ENCRE_2)
    etiqueter(ax_enc, d.noeuds, VILLES_MTL, taille=8)
    titre_encart(ax_enc, "Zoom: Greater Montréal")
    marquer_encart(ax, ax_enc)

    handles = [
        Line2D([], [], color=COUL_ECART_FORT, linewidth=2.7, label=f"> {SEUIL_ECART_FORT}%   ({v(c, 'n_spread_gt60')})"),
        Line2D([], [], color=COUL_ECART_MOYEN, linewidth=1.9,
               label=f"{SEUIL_ECART_PCT} – {SEUIL_ECART_FORT}%   ({v(c, 'n_spread_30_60')})"),
        Line2D([], [], color=GRIS_LIEN, linewidth=1.1, label=f"≤ {SEUIL_ECART_PCT}%   ({v(c, 'n_spread_le30')})"),
        Line2D([], [], color=GRIS_LIEN, linewidth=0.9, linestyle=(0, (3, 1.6)),
               label=f"Not compared: borrowed\nor no MTMD data   ({v(c, 'n_excluded_sensitivity')})"),
    ]
    legende(ax_leg, handles, "Spread across the 4 methods   (links)")
    entete(fig, f"{v(c, 'n_spread_gt30')} of {v(c, 'n_links_compared')} links shift by more than "
                f"{SEUIL_ECART_PCT}% depending on the aggregation method",
           "Spread of link AADT across 4 aggregation rules, (max − min) / mean, % · rules: mean, "
           "length-weighted mean, road-class-weighted mean, 80th percentile")
    pied(fig)


# ============================================================
# FIGURE 5 — SCHÉMA DE MÉTHODE (PHASE 2, RÉSEAU FICTIF)
# ============================================================

NOEUDS_FICTIFS = {"A": (0.0, 1.2), "B": (1.3, 2.2), "C": (1.2, 0.2), "D": (2.6, 1.2),
                  "E": (4.0, 2.1), "F": (3.9, 0.2), "G": (5.2, 1.2)}
LIENS_FICTIFS = [("A", "B"), ("A", "C"), ("B", "D"), ("C", "D"), ("D", "E"), ("D", "F"), ("E", "G"), ("F", "G")]
# Trois camions interceptés au poste D–E (exemple illustratif, aucune donnée réelle)
CAMIONS_FICTIFS = [
    {"chemin": "ABDEG", "od": "A → G", "cargo": "lumber",  "valeur": "tonnes × $/t (lumber)", "rel": 2.0},
    {"chemin": "CDE",   "od": "C → E", "cargo": "food",    "valeur": "tonnes × $/t (food)",   "rel": 3.0},
    {"chemin": "BDE",   "od": "B → E", "cargo": "empty",   "valeur": "empty → $0",            "rel": 0.0},
]
POSTE = (3.3, 1.65)   # milieu du lien D–E


def _reseau_fictif(ax: Axes, couleur: str = "#d9d6cb", largeur: float = 3.0, lettres: bool = True) -> None:
    for a, b in LIENS_FICTIFS:
        (x0, y0), (x1, y1) = NOEUDS_FICTIFS[a], NOEUDS_FICTIFS[b]
        ax.plot([x0, x1], [y0, y1], color=couleur, linewidth=largeur, solid_capstyle="round", zorder=1)
    for nom, (x, y) in NOEUDS_FICTIFS.items():
        ax.scatter([x], [y], s=150, color=SURFACE, edgecolor=ENCRE_2, linewidth=1.0, zorder=5)
        if lettres:
            ax.text(x, y, nom, fontsize=8, ha="center", va="center", color=ENCRE, zorder=6)


def _poste(ax: Axes, etiquette: bool = True) -> None:
    ax.scatter([POSTE[0]], [POSTE[1]], s=70, marker="D", color=ENCRE, edgecolor=SURFACE, linewidth=1.0, zorder=7)
    if etiquette:
        ax.annotate("survey\nstation", xy=POSTE, xytext=(0, 13), textcoords="offset points", ha="center",
                    va="bottom", fontsize=8, color=ENCRE, path_effects=HALO, zorder=8)


def _camion(ax: Axes, x: float, y: float, couleur: str, echelle_c: float = 1.0, alpha: float = 1.0) -> None:
    """Pictogramme de camion (remorque + cabine + roues), en coordonnées de données."""
    e = 0.11 * echelle_c
    ax.add_patch(FancyBboxPatch((x - 2.2 * e, y - 0.55 * e), 2.6 * e, 1.25 * e, boxstyle="round,pad=0,rounding_size=0.02",
                                facecolor=couleur, edgecolor="none", alpha=alpha, zorder=8))
    ax.add_patch(FancyBboxPatch((x + 0.55 * e, y - 0.55 * e), 0.95 * e, 0.95 * e, boxstyle="round,pad=0,rounding_size=0.02",
                                facecolor=couleur, edgecolor="none", alpha=alpha, zorder=8))
    for xr in (x - 1.7 * e, x - 0.6 * e, x + 1.05 * e):
        ax.scatter([xr], [y - 0.62 * e], s=7 * echelle_c ** 2, color=ENCRE, alpha=alpha, zorder=9, linewidth=0)


def _ligne(ax: Axes, y: float, i: int, texte: str, x_texte: float = 0.55) -> None:
    _camion(ax, 0.05, y, COUL_CAMIONS[i])
    ax.text(x_texte, y, texte, fontsize=8.5, va="center", color=ENCRE)


def _chemin_decale(chemin: str, decalage: float) -> LineString:
    ligne = LineString([NOEUDS_FICTIFS[n] for n in chemin])
    return ligne.offset_curve(decalage, join_style="mitre") if decalage else ligne


def _panneau_vide(ax: Axes) -> None:
    ax.set_xlim(-0.45, 5.65); ax.set_ylim(-2.35, 3.0)
    ax.set_aspect("equal", adjustable="datalim")
    ax.set_axis_off()


def schema_methode(fig: Figure | SubFigure, d: Donnees, c: dict) -> None:
    """Quatre étapes : enquête → valeur et poids → affectation → agrégation par lien."""
    largeur, ecart = 0.228, 0.017
    # Hauteur des panneaux calée sur leur emprise (6,1 × 5,35 unités) pour éviter le vide vertical
    axes = [fig.add_axes([0.02 + i * (largeur + ecart), 0.335, largeur, 0.335]) for i in range(4)]
    etapes = [
        ("1  Survey", "Trucks are interviewed at roadside\nstations: origin, destination,\ncommodity, tonnes."),
        ("2  Value and weight", "Each truck's cargo is valued in 2026 $;\nempty trucks count as $0. Each\ninterview stands for w trucks a year."),
        ("3  Route", "Each truck is routed from origin to\ndestination on the graph and leaves\nits value on every link it uses."),
        ("4  Aggregate per link", "Weighted mean value per truck on each\nlink, then × truck AADT × 365.\nLinks no surveyed truck uses are flagged."),
    ]
    # Titre et légende de chaque étape, ancrés en coordonnées de figure pour un alignement exact
    for i, (ax, (tete, texte)) in enumerate(zip(axes, etapes)):
        _panneau_vide(ax)
        x = 0.02 + i * (largeur + ecart)
        fig.text(x, 0.835, tete, fontsize=11.5, fontweight="bold", color=ENCRE, va="top")
        fig.text(x, 0.795, texte, fontsize=8.8, color=ENCRE_2, va="top", linespacing=1.35)

    # 1 — enquête : file de camions à côté du poste, entre les liens D–E et D–F
    ax = axes[0]
    _reseau_fictif(ax)
    _poste(ax)
    for i, cam in enumerate(CAMIONS_FICTIFS):
        _camion(ax, 3.25 + i * 0.5, 1.12, COUL_CAMIONS[i])
        _ligne(ax, -0.75 - i * 0.55, i, f"{cam['od']} · {cam['cargo']}")

    # 2 — valeur et poids : une entrevue représente w camions par an
    ax = axes[1]
    _camion(ax, 0.75, 1.45, COUL_CAMIONS[0], 1.7)
    ax.text(0.75, 0.75, "1 interview", fontsize=8.5, ha="center", color=ENCRE)
    ax.annotate("", xy=(2.35, 1.45), xytext=(1.5, 1.45),
                arrowprops=dict(arrowstyle="-|>", color=ENCRE_2, lw=1.2), zorder=9)
    for j in range(9):
        _camion(ax, 3.0 + (j % 3) * 0.75, 2.05 - (j // 3) * 0.55, COUL_CAMIONS[0], 0.8, alpha=0.55)
    ax.text(3.75, 0.55, "= w trucks / year", fontsize=8.5, ha="center", color=ENCRE)
    for i, cam in enumerate(CAMIONS_FICTIFS):
        _ligne(ax, -0.75 - i * 0.55, i, f"{cam['valeur']}   × w{'₁₂₃'[i]}")

    # 3 — affectation (chemins décalés pour rester visibles sur les liens partagés)
    ax = axes[2]
    _reseau_fictif(ax)
    _poste(ax, etiquette=False)
    for i, cam in enumerate(CAMIONS_FICTIFS):
        xs, ys = _chemin_decale(cam["chemin"], (i - 1) * 0.09).xy
        ax.plot(xs, ys, color=COUL_CAMIONS[i], linewidth=2.0, solid_capstyle="round", zorder=2 + i * 0.1)
        _ligne(ax, -0.75 - i * 0.55, i, " → ".join(cam["chemin"]))

    # 4 — agrégation : valeur relative moyenne par lien (exemple, poids égaux)
    ax = axes[3]
    usages: dict[tuple[str, str], list[float]] = {}
    for cam in CAMIONS_FICTIFS:
        for a, b in zip(cam["chemin"], cam["chemin"][1:]):
            usages.setdefault(tuple(sorted((a, b))), []).append(cam["rel"])
    moyennes = {k: np.mean(vals) for k, vals in usages.items()}
    vmin, vmax = min(moyennes.values()), max(moyennes.values())
    for a, b in LIENS_FICTIFS:
        (x0, y0), (x1, y1) = NOEUDS_FICTIFS[a], NOEUDS_FICTIFS[b]
        cle = tuple(sorted((a, b)))
        if cle in moyennes:
            t = (moyennes[cle] - vmin) / (vmax - vmin)
            ax.plot([x0, x1], [y0, y1], color=_viridis(0.70 - 0.68 * t), linewidth=1.5 + 3.0 * t,
                    solid_capstyle="round", zorder=2)
        else:
            ax.plot([x0, x1], [y0, y1], color=GRIS_VIDE, linewidth=1.3, linestyle=(0, (1, 1.8)), zorder=2)
    for nom, (x, y) in NOEUDS_FICTIFS.items():
        ax.scatter([x], [y], s=150, color=SURFACE, edgecolor=ENCRE_2, linewidth=1.0, zorder=5)
        ax.text(x, y, nom, fontsize=8, ha="center", va="center", color=ENCRE, zorder=6)
    for y, coul, larg, style, texte in [
        (-0.75, _viridis(0.02), 4.5, "-", "higher $ per truck"),
        (-1.30, _viridis(0.70), 1.5, "-", "lower $ per truck"),
        (-1.85, GRIS_VIDE, 1.3, (0, (1, 1.8)), "no surveyed truck: flagged"),
    ]:
        ax.plot([-0.25, 0.35], [y, y], color=coul, linewidth=larg, linestyle=style, solid_capstyle="round")
        ax.text(0.55, y, texte, fontsize=8.5, va="center", color=ENCRE)

    # Bandeau formule : la formule à gauche, ses définitions à droite
    fig.add_artist(FancyBboxPatch((0.02, 0.095), 0.96, 0.185, boxstyle="round,pad=0,rounding_size=0.012",
                                  transform=fig.transSubfigure if isinstance(fig, SubFigure) else fig.transFigure,
                                  facecolor="#f4f3ee", edgecolor=BASELINE, linewidth=0.8))
    fig.text(0.04, 0.1875, r"$V_\ell \;=\; \dfrac{\sum_{i \in \ell} w_i \, t_i \, p_{c(i)}}{\sum_{i \in \ell} w_i}"
                           r"\;\times\; \mathrm{AADT}^{\,\mathrm{truck}}_{\ell} \;\times\; 365$",
             fontsize=13, ha="left", va="center", color=ENCRE)
    fig.text(0.47, 0.1875,
             "V  annual value of goods on link ℓ ($/year)          i  surveyed trucks routed through ℓ\n"
             "w  survey expansion weight (trucks/year)           t  tonnes carried (0 if empty)\n"
             "p  price per tonne ($/t) for commodity c, Statistics Canada CFAF, in 2026 dollars",
             fontsize=8.8, ha="left", va="center", color=ENCRE_2, linespacing=1.6)
    entete(fig, "Phase 2 preview: turning a roadside truck survey into a dollar value per link",
           "Method schematic on an illustrative network (no survey data shown) · trucks, commodities and paths are examples")
    # Bandeau « travail en cours » sur la ligne du sous-titre (plus courte que le titre)
    fig.text(0.98, 0.905, "PRELIMINARY – WORK IN PROGRESS", fontsize=9, fontweight="bold", color=ENCRE_2,
             ha="right", va="top",
             bbox=dict(boxstyle="round,pad=0.4", facecolor=SURFACE, edgecolor=ENCRE_2, linewidth=0.9))
    pied(fig, "Method: A. Bouznad, CIRANO / Polytechnique Montréal · Planned inputs: 2006-07 National Roadside Study "
              "(MTQ / Transport Canada), Statistics Canada CFAF (table 23-10-0142-01), Phase 1 truck AADT")


# ============================================================
# EXPORTS
# ============================================================


@dataclass
class Fiche:
    """Une figure du portfolio et ses 3 lignes de note pour le PDF."""
    fichier: str
    dessiner: Callable[[Figure | SubFigure, Donnees, dict], None]
    question: str
    methode: str
    decision: str


def fiches(c: dict) -> list[Fiche]:
    return [
        Fiche("01_network_overview.png", carte_reseau,
              "Where are the cities and links of the inter-city road graph, and how concentrated is the network?",
              f"{v(c, 'n_cities')} municipalities ({v(c, 'n_cities_linked')} linked), {v(c, 'n_junctions')} junction nodes "
              f"and {v(c, 'n_links')} links built for the CIRANO resilience project; corridor = {v(c, 'corridor_km')} km "
              "either side of the straight Montréal–Québec axis.",
              "Sets the reading frame for every result that follows: corridor and remote-region links "
              "must be compared within their own context, not on one national scale."),
        Fiche("02_traffic_aadt.png", carte_trafic,
              "Which links carry the most traffic, and how unevenly is it spread?",
              f"2025 AADT from MTMD counts, matched to each link's road segments (median {v(c, 'median_segments')} per link); "
              "link value = 80th-percentile segment (nearest rank). Vehicle-km = AADT × routed length in Quebec. "
              f"Truck share is in the interactive map: only {v(c, 'n_truck_2025')} of {v(c, 'n_links_compared')} "
              "links have a 2025 MTMD truck count.",
              "Ranks links for closure scenarios: losing a top-class link disrupts far more daily trips than "
              f"a bottom-class one (median AADT {v(c, 'median_aadt_corridor')} in the corridor vs "
              f"{v(c, 'median_aadt_rest')} elsewhere)."),
        Fiche("03_data_provenance.png", carte_provenance,
              "How much of each link's traffic value is observed, and how much is filled in?",
              "For each link, the segment that sets its AADT is traced back to the MTMD file: a published 2025 value, "
              "a value filled from the segment's other years, or a k-nearest-neighbour imputation; links with no "
              "matched segment borrow the value of the adjacent link.",
              "Shows where new traffic counts would most raise confidence before an investment "
              "or closure decision rests on a specific link."),
        Fiche("04_method_sensitivity.png", carte_sensibilite,
              "Does the result depend on how segment counts are aggregated into one value per link?",
              "Four aggregation rules (mean, length-weighted mean, road-class-weighted mean, 80th percentile); "
              f"spread = (max − min) / mean. Links over {SEUIL_ECART_PCT}% have a median of "
              f"{v(c, 'median_segs_gt30')} matched segments, against {v(c, 'median_segs_le30')} for the others.",
              "Flags the links where the aggregation choice must be justified, or a value range reported "
              "instead of a single figure."),
        Fiche("05_phase2_method_preview.png", schema_methode,
              "How can a roadside truck survey be turned into a dollar value of goods on each link?",
              "2006-07 National Roadside Study for origin, destination, commodity and weight; Statistics Canada CFAF "
              "for $ per tonne; truck AADT from Phase 1. No results shown: survey microdata are held under a "
              "data-sharing agreement and estimates are still in progress.",
              "Once complete, gives each link an economic weight, so closures can be ranked by the value "
              "of goods disrupted and not only by vehicle counts."),
    ]


def verifier_debordements(fig: Figure, nom: str) -> list[str]:
    """Signale tout texte sorti de la figure et toute étiquette de ville sortie de
    sa carte (matplotlib ne coupe ni ne replie le texte : il faut le mesurer)."""
    fig.canvas.draw()
    rendu = fig.canvas.get_renderer()
    cadre = fig.bbox
    alertes = []
    for t in fig.findobj(matplotlib.text.Text):
        if not t.get_visible() or not t.get_text().strip():
            continue
        bb = t.get_window_extent(rendu)
        if bb.x0 < cadre.x0 - 1 or bb.x1 > cadre.x1 + 1 or bb.y0 < cadre.y0 - 1 or bb.y1 > cadre.y1 + 1:
            alertes.append(f"{nom} : texte hors figure : {t.get_text()[:50]!r}")
        elif isinstance(t, matplotlib.text.Annotation) and t.axes is not None:
            abb = t.axes.get_window_extent(rendu)
            if bb.x0 < abb.x0 or bb.x1 > abb.x1 or bb.y0 < abb.y0 or bb.y1 > abb.y1:
                alertes.append(f"{nom} : étiquette hors de sa carte : {t.get_text()[:50]!r}")
    return alertes


def exporter_png(fiche: Fiche, d: Donnees, c: dict) -> Path:
    fig = plt.figure(figsize=TAILLE_CARTE)
    fiche.dessiner(fig, d, c)
    for alerte in verifier_debordements(fig, fiche.fichier):
        print(f"      ⚠ {alerte}")
    chemin = DIR_PNG / fiche.fichier
    fig.savefig(chemin, dpi=DPI)
    plt.close(fig)
    return chemin


def _notes(sub: SubFigure, fiche: Fiche, page: int, total: int) -> None:
    """Les 3 lignes sous la carte : question, données et méthode, décision."""
    sub.add_artist(Line2D([0.02, 0.98], [0.97, 0.97], color=BASELINE, linewidth=0.8, transform=sub.transSubfigure))
    y = 0.86
    for etiquette, texte in (("Question", fiche.question), ("Data & method", fiche.methode),
                             ("What it helps decide", fiche.decision)):
        lignes = textwrap.wrap(texte, width=150)
        sub.text(0.02, y, etiquette, fontsize=9, fontweight="bold", color=ENCRE, va="top")
        sub.text(0.165, y, "\n".join(lignes), fontsize=9, color=ENCRE_2, va="top", linespacing=1.3)
        y -= 0.115 * (len(lignes) + 0.75)
    sub.text(0.02, 0.05, f"Akram Bouznad · Spatial data portfolio · code: {URL_DEPOT}", fontsize=7.5, color=MUET)
    sub.text(0.98, 0.05, f"{page} / {total}", fontsize=7.5, color=MUET, ha="right")


def exporter_pdf(liste: list[Fiche], d: Donnees, c: dict) -> None:
    with PdfPages(SORTIE_PDF, metadata={"Title": "Spatial data portfolio – Akram Bouznad",
                                        "Author": "Akram Bouznad"}) as pdf:
        for i, fiche in enumerate(liste, start=1):
            page = plt.figure(figsize=TAILLE_PAGE)
            haut, bas = page.subfigures(2, 1, height_ratios=[TAILLE_CARTE[1], HAUTEUR_NOTES])
            haut.set_facecolor(SURFACE); bas.set_facecolor(SURFACE)
            fiche.dessiner(haut, d, c)
            _notes(bas, fiche, i, len(liste))
            pdf.savefig(page)
            plt.close(page)


# ============================================================
# CARTE INTERACTIVE (folium)
# ============================================================


def _fmt(x: object, suffixe: str = "") -> str:
    return "–" if pd.isna(x) else f"{float(x):,.0f}{suffixe}"


def carte_interactive(d: Donnees, c: dict) -> None:
    """Carte Leaflet autonome (HTML) : infobulle par arc, deux couches basculables."""
    a = d.arcs.to_crs("EPSG:4326")
    props = pd.DataFrame({
        "link": a["nom_a"] + " – " + a["nom_b"],
        "aadt": a["djma_m4"].map(lambda x: _fmt(x, " veh/day")),
        "truck": [("–" if pd.isna(p) else f"{p:.1f}% (≈{_fmt(t)} trucks/day)")
                  for p, t in zip(a["pct_cam_m4"], a["djma_cam_m4"])],
        "aadt_src": a["prov"].map(lambda k: PROVENANCE[k][0]),
        "truck_src": a["prov_cam"],
        "spread": a["ecart_pct"].map(lambda x: "not compared" if pd.isna(x) else f"{x:.0f}%"),
        "segments": a["n_segs_djma"].astype(int).astype(str),
        "c_trafic": [GRIS_VIDE if pd.isna(k) else COUL_TRAFIC[int(k)] for k in a["classe"]],
        "w_trafic": [1.5 if pd.isna(k) else 1.5 + 1.2 * int(k) for k in a["classe"]],
        "c_prov": a["prov"].map(lambda k: PROVENANCE[k][1]),
        "dash_prov": a["prov"].map({"emprunt": "6 4", "absent": "2 4"}).fillna(""),
    })
    gdf = gpd.GeoDataFrame(props, geometry=a.geometry.values, crs="EPSG:4326")
    champs = ["link", "aadt", "truck", "aadt_src", "truck_src", "spread", "segments"]
    alias = ["Link", "AADT 2025", "Truck share", "AADT source", "Truck-share source",
             "Spread across 4 methods", "MTMD segments matched"]

    xmin, ymin, xmax, ymax = gdf.total_bounds
    m = folium.Map(tiles="CartoDB positron", control_scale=True, prefer_canvas=True)
    m.fit_bounds([[ymin, xmin], [ymax, xmax]])

    def infobulle() -> folium.GeoJsonTooltip:
        return folium.GeoJsonTooltip(fields=champs, aliases=alias, sticky=True,
                                     style="font-family: Arial, sans-serif; font-size: 12px;")

    folium.GeoJson(gdf, name="Traffic (AADT 2025)", tooltip=infobulle(),
                   style_function=lambda f: {"color": f["properties"]["c_trafic"],
                                             "weight": f["properties"]["w_trafic"], "opacity": 0.95},
                   highlight_function=lambda f: {"weight": 8, "color": "#eb6834"}).add_to(m)
    folium.GeoJson(gdf, name="Source of the AADT value", show=False, tooltip=infobulle(),
                   style_function=lambda f: {"color": f["properties"]["c_prov"], "weight": 3, "opacity": 0.95,
                                             "dashArray": f["properties"]["dash_prov"]},
                   highlight_function=lambda f: {"weight": 8, "color": "#eb6834"}).add_to(m)

    villes = d.noeuds[d.noeuds["relie"] & (d.noeuds["TYPE"] == "municipalite")].to_crs("EPSG:4326")
    groupe = folium.FeatureGroup(name="Cities", show=True)
    for _, r in villes.iterrows():
        folium.CircleMarker([r.geometry.y, r.geometry.x], radius=2.5, color=COUL_VILLE, weight=1,
                            fill=True, fill_opacity=1, tooltip=r["nom"]).add_to(groupe)
    groupe.add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)

    lignes_trafic = "".join(
        f'<div><span style="display:inline-block;width:26px;height:{1 + k}px;background:{COUL_TRAFIC[k]};'
        f'vertical-align:middle;margin-right:6px"></span>{vmin:,.0f} – {vmax:,.0f} ({nk})</div>'
        for k, (vmin, vmax, nk) in reversed(list(enumerate(d.classes))))
    lignes_prov = "".join(
        f'<div><span style="display:inline-block;width:26px;height:3px;background:{coul};'
        f'vertical-align:middle;margin-right:6px"></span>{lib} ({v(c, "prov_" + cle)})</div>'
        for cle, (lib, coul, *_r) in PROVENANCE.items())
    html = f"""
    <div style="position:fixed;top:12px;left:56px;z-index:9999;background:{SURFACE};padding:10px 14px;
                border:1px solid {BASELINE};border-radius:6px;font-family:Arial,sans-serif;max-width:430px;">
      <div style="font-size:15px;font-weight:bold;color:{ENCRE}">Quebec inter-city road links: traffic 2025</div>
      <div style="font-size:12px;color:{ENCRE_2};margin-top:3px">{v(c, 'n_links')} links between
        {v(c, 'n_cities_linked')} cities. Hover a link for its traffic, truck share and where each value comes from.</div>
    </div>
    <div style="position:fixed;bottom:28px;left:12px;z-index:9999;background:{SURFACE};padding:10px 12px;
                border:1px solid {BASELINE};border-radius:6px;font-family:Arial,sans-serif;font-size:11.5px;color:{ENCRE}">
      <div style="font-weight:bold;margin-bottom:4px">AADT, vehicles/day (links)</div>{lignes_trafic}
      <div style="font-weight:bold;margin:8px 0 4px">Source of the AADT value (links)</div>{lignes_prov}
      <div style="color:{MUET};margin-top:8px;max-width:330px">Data: MTMD traffic counts (AADT), Données Québec,
        CC BY 4.0. Analysis: A. Bouznad, CIRANO / Polytechnique Montréal.</div>
    </div>"""
    m.get_root().html.add_child(folium.Element(html))
    m.get_root().header.add_child(folium.Element("<title>Quebec road links – traffic 2025 · A. Bouznad</title>"))
    m.save(SORTIE_HTML)


# ============================================================
# MAIN
# ============================================================


def main() -> None:
    appliquer_theme()
    DIR_PNG.mkdir(parents=True, exist_ok=True)
    print("[1/5] Chargement et calculs")
    d = charger_donnees()
    c = calculer_chiffres(d)
    ecrire_chiffres(c)
    print(f"      {SORTIE_CHIFFRES.relative_to(RACINE)} ({len(c)} chiffres)")
    liste = fiches(c)
    print("[2/5] PNG")
    for f in liste:
        print(f"      {exporter_png(f, d, c).relative_to(RACINE)}")
    print("[3/5] PDF")
    exporter_pdf(liste, d, c)
    print(f"      {SORTIE_PDF.relative_to(RACINE)}")
    print("[4/5] Carte interactive")
    carte_interactive(d, c)
    print(f"      {SORTIE_HTML.relative_to(RACINE)}")
    print("[5/5] Terminé")


if __name__ == "__main__":
    main()
