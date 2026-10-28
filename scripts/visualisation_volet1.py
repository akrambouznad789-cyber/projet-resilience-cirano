"""
visualisation_volet1.py
=======================
Projet CIRANO — Volet 1 en ligne, avec une carte au centre, au même format que
la carte du Volet 2 (étapes à gauche, carte au centre, panneau à droite).

Remplace la page texte de generer_site_volet1.py (conservé tel quel) : le texte
des 5 pages docs/*.md reste disponible dans l'onglet « Texte complet ».

Pseudo-code
-----------
1. charger_arcs()        307 arcs de graphe_routier_djma.gpkg (DJMA m1-m4, statut,
                         arc donneur des 19 arcs complétés) ; écart relatif m1-m4
2. couche_graphe()       liens bruts de reseau_arcs.gpkg (lignes droites A–B)
3. couche_traces()       tracés OSRM simplifiés + attributs DJMA par arc
4. segments_par_arc()    sections de comptage retenues par arc, avec la valeur utilisée
5. couche_noeuds()       229 nœuds (municipalités, nœuds intermédiaires, frontières)
6. couche_sections()     7 823 sections de comptage : méthode de complétion, nombre
                         d'années mesurées et séries 2016-2025 (mesuré / complété)
7. couche_rtss()         12 567 sous-routes RTSS fusionnées par route et par classe
8. textes_etapes()       rendu des 5 pages docs/*.md (API Markdown de GitHub)
9. exporter()            JSON injecté dans scripts/gabarits/visualisation_volet1.html

Sorties (les données du Volet 1 sont toutes publiques, seuls les liens changent) :
  docs/volet1/index.html      version publiée, liée à la carte publique du Volet 2
  outputs/volet1/index.html   version locale, liée à la carte complète du Volet 2

Usage : ~/.venvs/portfolio/bin/python scripts/visualisation_volet1.py   (nécessite `gh`)
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

from generer_site_volet1 import ETAPES_V1, nettoyer_markdown, reecrire, rendre_gfm
from visualisation_volet2 import fond_de_carte, geojson, reparer_nom

RACINE   = Path(__file__).resolve().parent.parent
DIR_DOCS = RACINE / "docs"
GABARIT  = RACINE / "scripts" / "gabarits" / "visualisation_volet1.html"

# (cible, lien vers la carte du Volet 2, version publique ?)
SORTIES: list[tuple[Path, str, bool]] = [
    (RACINE / "docs" / "volet1" / "index.html", "../volet2/carte_interactive.html", True),
    (RACINE / "outputs" / "volet1" / "index.html", "../volet2/visualisation_volet2.html", False),
]

FICHIER_LIENS    = RACINE / "data" / "raw" / "reseau_arcs.gpkg"
FICHIER_RTSS     = RACINE / "data" / "raw" / "ReseauRoutier_RTSS.gpkg"
FICHIER_BRUT     = RACINE / "data" / "raw" / "DebitCirculation.gpkg"
FICHIER_DJMA     = RACINE / "data" / "processed" / "graphe_routier_djma.gpkg"
FICHIER_TRACES   = RACINE / "data" / "processed" / "graphe_routier.gpkg"
FICHIER_COMPLETS = RACINE / "data" / "processed" / "debits_completes.gpkg"

CRS_QC    = "EPSG:32198"
CRS_WGS84 = "EPSG:4326"
TOL_ARC_M      = 150      # simplification des tracés OSRM
TOL_SECTION_M  = 40       # simplification des sections de comptage
TOL_RTSS_M     = 100      # simplification du réseau RTSS
ANNEES         = list(range(2016, 2026))   # djma_annee_10 = 2016 … djma_annee_1 = 2025

# Méthode de complétion d'une section (methode_djma / methode_cam) → code d'une lettre
CODES_METHODE: dict[str, str] = {"complet": "c", "interpolation": "i", "extrapolation": "e", "mice": "m", "geo_knn": "g"}

# Classes RTSS regroupées pour l'affichage (code court → libellés MTQ)
CLASSES_RTSS: dict[str, tuple[str, ...]] = {
    "auto": ("Autoroute",), "nat": ("Nationale",), "reg": ("Régionale",), "col": ("Collectrice",),
    "autre": ("Sans classe", "Accès aux ressources", "Accès aux ressources et aux localités isolées",
              "Local 1", "Local 2", "Local 3"),
}


def proprietes_propres(df: pd.DataFrame) -> pd.DataFrame:
    """NaN → None et types numpy → objets Python, pour un JSON valide."""
    df = df.astype({c: object for c in df.columns if c != "geometry"})
    return df.where(pd.notna(df), None)


# ---------------------------------------------------------------------------
# 1-4. Arcs du graphe
# ---------------------------------------------------------------------------

def charger_arcs() -> gpd.GeoDataFrame:
    """307 arcs enrichis (DJMA m1-m4) ; écart relatif entre méthodes, hors arcs complétés."""
    arcs = gpd.read_file(FICHIER_DJMA)
    for c in ("VILLE_A", "VILLE_B"):
        arcs[c] = arcs[c].map(reparer_nom)
    cols = [f"djma_m{m}" for m in (1, 2, 3, 4)]
    mesure = arcs[cols].notna().all(axis=1) & ~arcs["djma_complete"].fillna(False).astype(bool)
    arcs["ecart_pct"] = np.where(
        mesure, (arcs[cols].max(axis=1) - arcs[cols].min(axis=1)) / arcs[cols].mean(axis=1) * 100, np.nan)
    return arcs


def couche_graphe() -> dict:
    """Liens bruts du graphe : lignes droites entre A et B (reseau_arcs.gpkg)."""
    liens = gpd.read_file(FICHIER_LIENS, layer="arcs").set_crs(CRS_QC, allow_override=True).to_crs(CRS_WGS84)
    liens["SOURCE"] = liens["SOURCE"].fillna("")
    tableau = proprietes_propres(pd.DataFrame({
        "id": liens["ID_ARC"].astype(int), "vol": liens["DIST_KM"].round(1), "src": liens["SOURCE"],
    }))
    return geojson(gpd.GeoDataFrame(tableau, geometry=liens.geometry, crs=CRS_WGS84), list(tableau.columns), 4)


def couche_traces(arcs: gpd.GeoDataFrame) -> dict:
    """Tracés OSRM simplifiés (repli : ligne droite) et attributs DJMA de chaque arc."""
    traces = gpd.read_file(FICHIER_TRACES, layer="trace_osrm").drop_duplicates("ID_ARC").set_index("ID_ARC")
    sortie = arcs.to_crs(CRS_QC).copy()
    for i, r in sortie.iterrows():
        t = traces.geometry.get(r.ID_ARC)
        if t is not None and not t.is_empty:
            sortie.at[i, "geometry"] = t.simplify(TOL_ARC_M)
    sortie = sortie.to_crs(CRS_WGS84)
    props = {
        "id": sortie["ID_ARC"].astype(int), "a": sortie["VILLE_A"], "b": sortie["VILLE_B"],
        "ida": sortie["ID_A"], "idb": sortie["ID_B"],
        "km": sortie["longueur_trace_km"].round(1), "kmqc": sortie["longueur_qc_km"].round(1),
        "vol": sortie["DIST_KM"].round(1), "src": sortie["SOURCE"].fillna(""), "statut": sortie["statut"],
        "nseg": sortie["n_segs_djma"].fillna(0).astype(int),
        "comp": sortie["djma_complete"].fillna(False).astype(bool),
        "don": sortie["djma_arc_source"].map(lambda v: None if pd.isna(v) else int(v)),
        "ecart": sortie["ecart_pct"].round(1),
    }
    for m in (1, 2, 3, 4):
        props[f"d{m}"] = sortie[f"djma_m{m}"].round()
        props[f"c{m}"] = sortie[f"djma_cam_m{m}"].round()
        props[f"p{m}"] = sortie[f"pct_cam_m{m}"].round(1)
    tableau = proprietes_propres(pd.DataFrame(props))
    return geojson(gpd.GeoDataFrame(tableau, geometry=sortie.geometry, crs=CRS_WGS84), list(props), 4)


def segments_par_arc(arcs: gpd.GeoDataFrame) -> dict[str, list[list]]:
    """Sections de comptage retenues par arc : [id, DJMA utilisé, % camions utilisé, année]."""
    def decouper(texte: object) -> list[str]:
        return [] if not isinstance(texte, str) or not texte else texte.split("|")

    resultat: dict[str, list[list]] = {}
    for r in arcs.itertuples():
        ids, djma, cam = decouper(r.ids_segs_djma_id), decouper(r.ids_segs_djma_val), decouper(r.ids_segs_djma_val_cam)
        lignes = []
        for k, sid in enumerate(ids):
            v, _, annee = (djma[k] if k < len(djma) else "").partition("@")
            c = (cam[k] if k < len(cam) else "").partition("@")[0]
            lignes.append([int(sid), round(float(v)) if v else None, round(float(c), 1) if c else None,
                           int(annee) if annee else None])
        if lignes:
            resultat[str(int(r.ID_ARC))] = lignes
    return resultat


def couche_noeuds() -> list[dict]:
    """229 nœuds du graphe, en WGS84."""
    noeuds = gpd.read_file(FICHIER_LIENS, layer="noeuds").set_crs(CRS_QC, allow_override=True).to_crs(CRS_WGS84)
    return [{"id": r.ID, "nom": reparer_nom(r.NOM), "type": r.TYPE, "nb": int(r.NB_ARCS),
             "lon": round(r.geometry.x, 4), "lat": round(r.geometry.y, 4)} for r in noeuds.itertuples()]


# ---------------------------------------------------------------------------
# 5-6. Sections de comptage et réseau RTSS
# ---------------------------------------------------------------------------

def couche_sections() -> tuple[dict, dict]:
    """Sections de comptage : géométrie, méthode de complétion et séries 2016-2025.

    Le masque « mesuré » compare la table complétée à la table brute du MTQ :
    bit j allumé = année ANNEES[j] mesurée par le MTQ (sinon : valeur complétée).
    """
    rang = {a: 11 - (a - 2015) for a in ANNEES}               # 2016 → annee_10, 2025 → annee_1
    cols_d = [f"val_djma_annee_{rang[a]}" for a in ANNEES]
    cols_c = [f"val_cam_annee_{rang[a]}" for a in ANNEES]
    comp = gpd.read_file(FICHIER_COMPLETS, columns=["ide_sectn_trafc", "des_debut_sous_route", "des_fin_sous_route",
                                                    "rtss_debut", "methode_djma", "methode_cam",
                                                    "n_djma_original", "n_cam_original"] + cols_d + cols_c)
    brut = gpd.read_file(FICHIER_BRUT, columns=["ide_sectn_trafc"] + cols_d + cols_c, ignore_geometry=True)
    assert (comp["ide_sectn_trafc"].to_numpy() == brut["ide_sectn_trafc"].to_numpy()).all(), "ordre des sections différent"

    def masque(cols: list[str]) -> np.ndarray:
        connu = brut[cols].apply(pd.to_numeric, errors="coerce").notna().to_numpy()
        return (connu * (1 << np.arange(len(cols)))).sum(axis=1)

    djma = comp[cols_d].apply(pd.to_numeric, errors="coerce").round().to_numpy()
    cam = comp[cols_c].apply(pd.to_numeric, errors="coerce").round(1).to_numpy()
    # Colonnes lues par la page (index = propriété « i » de la section)
    series = {
        "annees": ANNEES,
        "id": comp["ide_sectn_trafc"].astype(int).tolist(),
        "de": comp["des_debut_sous_route"].fillna("").tolist(), "a": comp["des_fin_sous_route"].fillna("").tolist(),
        "route": comp["rtss_debut"].fillna("").astype(str).str[:5].str.lstrip("0").tolist(),
        "djma": [[None if np.isnan(v) else int(v) for v in ligne] for ligne in djma],
        "cam": [[None if np.isnan(v) else float(v) for v in ligne] for ligne in cam],
        "md": masque(cols_d).astype(int).tolist(), "mc": masque(cols_c).astype(int).tolist(),
    }

    # Géométrie : seules les propriétés qui servent au style restent dans chaque entité
    geo = comp.to_crs(CRS_QC)
    geo["geometry"] = geo.geometry.simplify(TOL_SECTION_M)
    geo = geo.to_crs(CRS_WGS84)
    tableau = proprietes_propres(pd.DataFrame({
        "i": np.arange(len(geo)),
        "md": geo["methode_djma"].map(CODES_METHODE), "mc": geo["methode_cam"].map(CODES_METHODE),
        "nd": geo["n_djma_original"].astype(int), "nc": geo["n_cam_original"].astype(int),
    }))
    return geojson(gpd.GeoDataFrame(tableau, geometry=geo.geometry, crs=CRS_WGS84), list(tableau.columns), 4), series


def couche_rtss() -> tuple[dict, dict[str, int]]:
    """Réseau RTSS fusionné par route et par classe (contexte de la carte), et effectifs par classe."""
    rtss = gpd.read_file(FICHIER_RTSS, columns=["num_route", "des_clasf_"])
    vers_code = {lib: code for code, libs in CLASSES_RTSS.items() for lib in libs}
    rtss["cl"] = rtss["des_clasf_"].map(vers_code).fillna("autre")
    effectifs = rtss["cl"].value_counts().to_dict()
    geo = rtss.to_crs(CRS_QC)
    geo["geometry"] = geo.geometry.simplify(TOL_RTSS_M)
    geo = geo.dissolve(by=["num_route", "cl"]).reset_index().to_crs(CRS_WGS84)
    geo["r"] = geo["num_route"].fillna("").astype(str).str.lstrip("0")
    return geojson(geo, ["r", "cl"], 4), {k: int(v) for k, v in effectifs.items()}


# ---------------------------------------------------------------------------
# 7-8. Textes et export
# ---------------------------------------------------------------------------

def textes_etapes() -> list[str]:
    """Les 5 pages docs/*.md rendues en HTML, avec les liens réécrits pour la page."""
    textes = []
    for k, (fichier, *_ ) in enumerate(ETAPES_V1, start=1):
        print(f"    texte {k}/{len(ETAPES_V1)} : {fichier}")
        texte = nettoyer_markdown((DIR_DOCS / fichier).read_text(encoding="utf-8"))
        textes.append(reecrire(rendre_gfm(texte), k))
    return textes


def exporter(donnees: dict, cible: Path, url_v2: str, publique: bool) -> None:
    """Injecte les données dans le gabarit HTML (page autonome)."""
    charge = {**donnees, "meta": {**donnees["meta"], "url_v2": url_v2, "publique": publique}}
    texte = json.dumps(charge, ensure_ascii=False, separators=(",", ":"), allow_nan=False).replace("</", "<\\/")
    html = GABARIT.read_text(encoding="utf-8").replace("/*__DONNEES__*/null", texte, 1)
    cible.parent.mkdir(parents=True, exist_ok=True)
    cible.write_text(html, encoding="utf-8")
    print(f"→ {cible.relative_to(RACINE)} ({cible.stat().st_size / 1e6:.1f} Mo)")


def main() -> None:
    """Prépare toutes les couches et écrit les deux versions de la page."""
    print("[1/6] arcs, liens et nœuds")
    arcs = charger_arcs()
    print("[2/6] sections de comptage")
    sections, series = couche_sections()
    print("[3/6] réseau RTSS")
    rtss, effectifs_rtss = couche_rtss()
    print("[4/6] fond de carte")
    fond = fond_de_carte()
    print("[5/6] textes des étapes")
    textes = textes_etapes()

    sp = sections["features"]
    meta = {
        "genere_le": date.today().isoformat(),
        "n_rtss": int(sum(effectifs_rtss.values())), "rtss_classes": effectifs_rtss,
        "n_sections": len(sp),
        "n_djma_complet": sum(f["properties"]["nd"] == 10 for f in sp),
        "n_cam_complet": sum(f["properties"]["nc"] == 10 for f in sp),
        "methodes_djma": {k: int(v) for k, v in pd.Series([f["properties"]["md"] for f in sp]).value_counts().items()},
        "methodes_cam": {k: int(v) for k, v in pd.Series([f["properties"]["mc"] for f in sp]).value_counts().items()},
        "statuts": arcs["statut"].value_counts().to_dict(),
        "n_rattachements": int(arcs["n_segs_djma"].fillna(0).sum()),
        "mediane_segs": float(arcs.loc[arcs["statut"] == "ok", "n_segs_djma"].median()),
        "mediane_km": float(arcs.loc[arcs["statut"] == "ok", "longueur_trace_km"].median()),
        "etapes": [{"court": court, "sur": sur, "intro": intro} for _, court, sur, intro in ETAPES_V1],
    }
    donnees = {
        "meta": meta, "fond": fond, "graphe": couche_graphe(), "arcs": couche_traces(arcs),
        "segs_arc": segments_par_arc(arcs), "noeuds": couche_noeuds(),
        "sections": sections, "series": series, "rtss": rtss, "textes": textes,
    }
    print("[6/6] export")
    for cible, url_v2, publique in SORTIES:
        exporter(donnees, cible, url_v2, publique)


if __name__ == "__main__":
    main()
