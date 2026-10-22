"""
visualisation_volet2.py
=======================
Projet CIRANO — Volet 2 (phase II) : carte interactive, de l'ENR 2006-2007 à la
valeur économique par arc.

Premier jet (2026-10-22). Le script prépare toutes les couches de la carte
`outputs/volet2/visualisation_volet2.html` et les injecte dans le gabarit
`scripts/gabarits/visualisation_volet2.html`.

Pseudo-code
-----------
1. charger_enr()          114 734 camions : enquête + lieux de ramassage (CP) et de
                          livraison (CD) + trajets annualisés + postes québécois franchis
2. classer_population()   catégorie de chaque camion : qc_poste_qc, qc_poste_hors,
                          transit (aucun CP/CD au QC, mais au moins 1 poste QC franchi)
                          ou ecarte
3. localiser_points()     CSD 2001 → point représentatif de la CSD 2016 de même code ;
                          repli sur la division de recensement (4 premiers chiffres),
                          puis sur la province ; comtés américains → Gazetteer 2020
4. rattacher_noeuds()     point québécois → nœud municipal le plus proche du graphe ;
                          point hors Québec → nœuds frontières candidats
5. router_camions()       plus court chemin (km OSRM) entre nœuds ; pour un bout hors
                          Québec, la frontière qui minimise la distance totale ;
                          cumul des camions et des tonnes par arc et par groupe
6. valider_postes()       part des camions interceptés à un poste dont le chemin
                          calculé passe par l'arc de ce poste
7. exporter()             JSON compact injecté dans le gabarit HTML

Les étapes « $ par tonne », « actualisation » et « valeur annuelle par arc » sont
calculées dans la page (JavaScript), pour que les $/t et le facteur d'indice
restent modifiables à la volée.

Données ENR : lues hors du dépôt (variable d'environnement ENR_DIR), jamais
copiées dans Git. La page complète contient des agrégats tirés de l'ENR (flux
entre CSD) : elle reste locale, dans outputs/ (exclu de Git). L'option
--publique écrit en plus docs/volet2/carte_interactive.html, sans flux ni lieux
ENR : seuls les résultats par arc, par poste et les chiffres globaux y restent.

Usage : ~/.venvs/portfolio/bin/python scripts/visualisation_volet2.py [--publique]
"""

from __future__ import annotations

import argparse
import inspect
import json
import math
import os
import pickle
import re
import unicodedata
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
from shapely.geometry import LineString, box, mapping

RACINE      = Path(__file__).resolve().parent.parent
DIR_ENR     = Path(os.environ.get(
    "ENR_DIR",
    "/mnt/c/Users/Admin/Desktop/ProjetCirano2/MASTER_SURVEY_FILES_UPDATED/MASTER_SURVEY_FILES_UPDATED",
))
DIR_SORTIE  = RACINE / "outputs" / "volet2"
DIR_CACHE   = DIR_SORTIE / "cache"
GABARIT     = RACINE / "scripts" / "gabarits" / "visualisation_volet2.html"
SORTIE_HTML = DIR_SORTIE / "visualisation_volet2.html"
SORTIE_PUBLIQUE = RACINE / "docs" / "volet2" / "carte_interactive.html"

FICHIER_NOEUDS = RACINE / "data" / "raw" / "reseau_arcs.gpkg"
FICHIER_DJMA   = RACINE / "data" / "processed" / "graphe_routier_djma.gpkg"
FICHIER_TRACES = RACINE / "data" / "processed" / "graphe_routier.gpkg"
DIR_REFERENCE  = RACINE / "data" / "reference"

URL_CSD_2016  = ("https://ftp.maps.canada.ca/pub/statcan_statcan/Census_Recensement/"
                 "census_subdivisions_2016/census_subdivisions_2016_en.gdb.zip")
URL_COMTES_US = ("https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"
                 "2020_Gazetteer/2020_Gaz_counties_national.zip")

CRS_QC         = "EPSG:32198"
CRS_WGS84      = "EPSG:4326"
DETOUR_ROUTIER = 1.2      # distance routière ≈ 1,2 × vol d'oiseau, hors Québec
TOL_ARC_M      = 300      # simplification des tracés OSRM pour l'affichage
TOP_PAIRES     = 5        # paires O-D affichées par arc

# Nœuds frontières candidats selon la province ou le pays du lieu hors Québec.
# Le choix final se fait par trajet : la frontière qui minimise
# DETOUR_ROUTIER × vol d'oiseau + plus court chemin dans le graphe.
# F1 Ontario (nord), F11 Ontario-Sud, F2 N.-B., F3 T.-N.-L., F5 Î.-P.-É.,
# F6 N.-É., F7 Maine, F9 Vermont, F10 New York (F4 et F8 sont isolés).
FRONTIERES_CANDIDATES: dict[str, tuple[str, ...]] = {
    "CA-ON": ("F1", "F11"), "CA-MB": ("F1", "F11"), "CA-SK": ("F1", "F11"),
    "CA-AB": ("F1", "F11"), "CA-BC": ("F1", "F11"), "CA-YT": ("F1", "F11"),
    "CA-NT": ("F1", "F11"), "CA-NU": ("F1", "F11"),
    "CA-NB": ("F2",), "CA-NS": ("F6", "F2"), "CA-PE": ("F5", "F2"),
    "CA-NL": ("F3", "F6", "F2"),
    "US": ("F10", "F7", "F9", "F11"),
}

# Groupes de travail PROVISOIRES (12 + inconnu + vide). Ce ne sont PAS encore
# les 12 groupes de la CFAF : la table de passage officielle reste à faire
# dès que les métadonnées du tableau 23-10-0142-01 seront accessibles.
GROUPES: dict[str, str] = {
    "agri":      "Produits agricoles et animaux",
    "alim":      "Aliments et boissons",
    "mineraux":  "Minéraux et matériaux de construction",
    "petrole":   "Combustibles et produits pétroliers",
    "chimie":    "Chimie, plastiques et caoutchouc",
    "bois":      "Bois et produits du bois",
    "papier":    "Pâtes, papiers et imprimés",
    "metaux":    "Métaux et produits métalliques",
    "machines":  "Machinerie, électronique, instruments",
    "vehicules": "Véhicules et pièces",
    "manuf":     "Fret mixte, colis, autres produits finis",
    "dechets":   "Déchets et rebuts",
    "inconnu":   "Marchandise inconnue",
    "vide":      "Camion vide",
}

# Début du libellé `commodit00` (54 catégories ENR) → groupe de travail.
# Les préfixes les plus longs sont testés en premier.
MARCHANDISE_VERS_GROUPE: dict[str, str] = {
    "EMPTY": "vide", "Unknown": "inconnu",
    "Fresh fruit": "agri", "Other raw agricultural": "agri", "Cereal grains": "agri",
    "Live animals": "agri", "Animal feed": "agri",
    "Processed fruit": "alim", "Meat, poultry": "alim", "Bakery products": "alim",
    "Dairy products": "alim", "Alcoholic beverages": "alim", "Non-alcoholic beverages": "alim",
    "Stone and sand": "mineraux", "Other non-metallic mineral": "mineraux",
    "Metallic ores": "mineraux", "Hydraulic cement": "mineraux", "Ceramic and glass": "mineraux",
    "Coal": "petrole", "Crude petroleum": "petrole", "Gasoline": "petrole",
    "Fuel oils": "petrole", "Refined petroleum": "petrole",
    "Basic organic/inorganic": "chimie", "Fertilizers": "chimie", "Chemical products": "chimie",
    "Pharmaceuticals": "chimie", "Plastic and rubber raw": "chimie",
    "Plastic products": "chimie", "Rubber products": "chimie",
    "Logs/rough wood": "bois", "Wood chips": "bois", "Lumber": "bois", "Other wood products": "bois",
    "Wood pulp": "papier", "Newsprint": "papier", "Other bulk paper": "papier",
    "Paper/paperboard articles": "papier", "Printed products": "papier",
    "Iron & steel": "metaux", "Non-ferrous metal": "metaux", "Articles of base metal": "metaux",
    "Commercial machinery": "machines", "Electronic/electrical": "machines",
    "Precision instruments": "machines",
    "Automobiles": "vehicules", "Parts & accessories": "vehicules",
    "Mixed freight": "manuf", "Mail and Parcels": "manuf", "Furniture": "manuf",
    "Textiles": "manuf", "Miscellaneous manufactured": "manuf",
    "Waste and scrap": "dechets",
}

# Localité des 49 postes québécois de l'ENR (libellé `wp_dcs`, avant la
# parenthèse). Coordonnées APPROXIMATIVES saisies à la main (lat, lon, nom) : la
# localité du poste sur la route indiquée, à valider. Chaque poste est ensuite
# accroché au tracé OSRM le plus proche.
LOCALITES_POSTES: dict[str, tuple[float, float, str]] = {
    "BEAUMONT": (46.812, -71.020, "Beaumont"), "BELOEIL": (45.555, -73.215, "Belœil"),
    "LACOLLE": (45.030, -73.370, "Lacolle"), "VAUDREUIL": (45.405, -74.050, "Vaudreuil"),
    "VILLEROY": (46.400, -71.860, "Villeroy"), "SAINT AUGUSTIN": (46.745, -71.470, "Saint-Augustin-de-Desmaures"),
    "ST NICOLAS": (46.690, -71.420, "Saint-Nicolas"), "ST EUGENE": (45.795, -72.700, "Saint-Eugène"),
    "LES CEDRES": (45.305, -74.060, "Les Cèdres"), "MASKINONGE": (46.225, -73.020, "Maskinongé"),
    "POINTE DU LAC": (46.300, -72.690, "Pointe-du-Lac"), "DEAUVILLE": (45.360, -72.040, "Deauville"),
    "ROCK ISLAND": (45.010, -72.110, "Rock Island"), "PHILIPSBURG": (45.020, -73.085, "Philipsburg"),
    "ARMSTRONG": (45.800, -70.395, "Armstrong"), "CABANO": (47.680, -68.880, "Cabano"),
    "RAPIDE DANSEUR": (48.560, -79.320, "Rapide-Danseur"), "BAIE ST PAUL": (47.445, -70.500, "Baie-Saint-Paul"),
    "BAIE COMEAU": (49.220, -68.150, "Baie-Comeau"), "GRANDES BERGERONNES": (48.250, -69.550, "Grandes-Bergeronnes"),
    "CHARLESBOURG": (46.900, -71.290, "Charlesbourg"), "ST ETIENNE DE LAUZON": (46.650, -71.260, "Saint-Étienne-de-Lauzon"),
    "ST ETIENNE DES GRES": (46.435, -72.770, "Saint-Étienne-des-Grès"), "ST CELESTIN": (46.215, -72.440, "Saint-Célestin"),
    "ST WENCESLAS": (46.165, -72.335, "Saint-Wenceslas"), "ULVERTON": (45.710, -72.275, "Ulverton"),
    "THETFORD MINES": (46.095, -71.300, "Thetford Mines"), "BLACK LAKE": (46.045, -71.365, "Black Lake"),
    "VERCHERES": (45.750, -73.370, "Verchères"), "ST THOMAS": (46.020, -73.350, "Saint-Thomas"),
    "LOCHABER": (45.620, -75.260, "Lochaber"), "LOUVICOURT": (48.080, -77.400, "Louvicourt"),
    "SAINTE LUCE": (48.550, -68.390, "Sainte-Luce"), "NEW RICHMOND": (48.165, -65.860, "New Richmond"),
    "NOTRE DAME DU NORD": (47.595, -79.480, "Notre-Dame-du-Nord"), "BROSSARD": (45.450, -73.450, "Brossard"),
    "CHICOUTIMI": (48.330, -71.120, "Chicoutimi"),
}


# ---------------------------------------------------------------------------
# Outils
# ---------------------------------------------------------------------------

def reparer_nom(texte: str) -> str:
    """Corrige le double encodage UTF-8/Latin-1 des noms de `reseau_arcs.gpkg`."""
    if not isinstance(texte, str):
        return texte
    try:
        return texte.encode("latin1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return texte


def normaliser(texte: str) -> str:
    """Majuscules sans accents ni tirets, espaces simples (clé de comparaison)."""
    t = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", t.upper().replace("-", " ")).strip()


def haversine_km(lon1: np.ndarray, lat1: np.ndarray, lon2: float, lat2: float) -> np.ndarray:
    """Distance à vol d'oiseau (km) entre des points et un point de référence."""
    r = 6371.0
    p1, p2 = np.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlmb = math.radians(lon2) - np.radians(lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * math.cos(p2) * np.sin(dlmb / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(a))


def telecharger(url: str, cible: Path) -> Path:
    """Télécharge un fichier de référence une seule fois (cache local)."""
    if not cible.exists():
        cible.parent.mkdir(parents=True, exist_ok=True)
        requete = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(requete, timeout=180) as reponse:
            cible.write_bytes(reponse.read())
    return cible


def arrondir_coords(objet: object, nd: int) -> object:
    """Arrondit récursivement les coordonnées d'une géométrie GeoJSON."""
    if isinstance(objet, (list, tuple)):
        if objet and isinstance(objet[0], (int, float)):
            return [round(float(v), nd) for v in objet]
        return [arrondir_coords(o, nd) for o in objet]
    return objet


def geojson(gdf: gpd.GeoDataFrame, proprietes: list[str], nd: int) -> dict:
    """GeoDataFrame (EPSG:4326) → FeatureCollection compacte."""
    entites = []
    for _, ligne in gdf.iterrows():
        if ligne.geometry is None or ligne.geometry.is_empty:
            continue
        geom = mapping(ligne.geometry)
        entites.append({
            "type": "Feature",
            "geometry": {"type": geom["type"], "coordinates": arrondir_coords(geom["coordinates"], nd)},
            "properties": {p: ligne[p] for p in proprietes},
        })
    return {"type": "FeatureCollection", "features": entites}


# ---------------------------------------------------------------------------
# 1-2. ENR : chargement et population
# ---------------------------------------------------------------------------

def groupe_marchandise(libelle: str) -> str:
    """Libellé ENR `commodit00` → groupe de travail provisoire."""
    texte = re.sub(r"\s+", " ", str(libelle)).strip()
    for prefixe in sorted(MARCHANDISE_VERS_GROUPE, key=len, reverse=True):
        if texte.startswith(prefixe):
            return MARCHANDISE_VERS_GROUPE[prefixe]
    raise ValueError(f"Marchandise ENR sans groupe : {libelle!r}")


def charger_enr() -> pd.DataFrame:
    """Joint les 4 fichiers ENR (1 ligne = 1 camion enquêté) et annualise les tonnes."""
    cache = DIR_CACHE / "enr_minimal.pkl"
    if cache.exists():
        return pd.read_pickle(cache)

    enquete = pd.read_csv(DIR_ENR / "MASTER_NRS_SURVEY_MAY202009_EN.csv", dtype=str, encoding="latin1",
                          usecols=["id", "dcs_code", "wp_dcs", "commodit00", "weight_k01"])
    lieux = pd.read_csv(DIR_ENR / "MASTER_waypoint_list_lat_long.csv", dtype=str,
                        usecols=["Id", "CP_CSDUID", "CP_PROV", "CP_USCOUNTY",
                                 "CD_CSDUID", "CD_PROV", "CD_USCOUNTY"]).rename(columns={"Id": "id"})
    trajets = pd.read_csv(DIR_ENR / "MASTER_EXPANDED_VALUES_V2.csv", dtype={"id": str},
                          usecols=["id", "TRIPS_WGT3_ANNUAL", "TRIPS_WGT4_ANNUAL"])
    postes = pd.read_csv(DIR_ENR / "MASTER_SITES_PASSED_LIST.csv", dtype=str).rename(columns={"nrsid": "id"})
    colonnes_qc = [c for c in postes.columns if c.startswith("qc")]
    postes["passe_qc"] = (postes[colonnes_qc] == "T").any(axis=1)

    df = (enquete.merge(lieux, on="id", validate="one_to_one")
                 .merge(trajets, on="id", validate="one_to_one")
                 .merge(postes[["id", "passe_qc"]], on="id", validate="one_to_one"))
    df["dcs_code"] = df["dcs_code"].str.upper()
    df["groupe"] = df["commodit00"].map(groupe_marchandise)
    df["vide"] = df["groupe"] == "vide"

    # Poids du chargement : texte « 13,000 » en kg ; manquant chez 0,6 % des
    # chargés → médiane de leur groupe (signalé par kg_impute).
    df["kg"] = pd.to_numeric(df["weight_k01"].str.replace(",", "", regex=False), errors="coerce").astype(float)
    manquant = ~df["vide"] & ~(df["kg"] > 0)
    mediane = df.loc[~df["vide"] & (df["kg"] > 0)].groupby("groupe")["kg"].median()
    df["kg_impute"] = manquant
    df.loc[manquant, "kg"] = df.loc[manquant, "groupe"].map(mediane)
    df.loc[df["vide"], "kg"] = 0.0

    # Trajets annuels = adjwgt × 52,143 (déjà calculés par l'ENR) ; tonnes = kg/1000 × trajets
    df["n3"] = df["TRIPS_WGT3_ANNUAL"]
    df["n4"] = df["TRIPS_WGT4_ANNUAL"]
    df["t3"] = df["kg"] / 1000 * df["n3"]
    df["t4"] = df["kg"] / 1000 * df["n4"]
    df = df.drop(columns=["weight_k01", "TRIPS_WGT3_ANNUAL", "TRIPS_WGT4_ANNUAL"])

    DIR_CACHE.mkdir(parents=True, exist_ok=True)
    df.to_pickle(cache)
    return df


def classer_population(df: pd.DataFrame) -> pd.DataFrame:
    """Filtre du 2026-10-20 : CP ou CD au Québec, ou au moins un poste QC franchi."""
    touche_qc = (df["CP_PROV"] == "CA-QC") | (df["CD_PROV"] == "CA-QC")
    poste_qc = df["dcs_code"].str.startswith("QC")
    df["cat"] = np.select(
        [touche_qc & poste_qc, touche_qc & ~poste_qc, ~touche_qc & df["passe_qc"]],
        ["qc_poste_qc", "qc_poste_hors", "transit"],
        default="ecarte",
    )
    df["garde"] = df["cat"] != "ecarte"
    return df


# ---------------------------------------------------------------------------
# 3. Localisation des lieux de ramassage et de livraison
# ---------------------------------------------------------------------------

def charger_references_geo() -> tuple[gpd.GeoDataFrame, pd.DataFrame]:
    """Points représentatifs des CSD 2016 (StatCan) et des comtés américains (Census)."""
    zip_csd = telecharger(URL_CSD_2016, DIR_CACHE / "csd2016.gdb.zip")
    gdb = DIR_CACHE / "csd2016" / "census_subdivisions_2016_en.gdb"
    if not gdb.exists():
        zipfile.ZipFile(zip_csd).extractall(DIR_CACHE / "csd2016")
    csd = gpd.read_file(gdb)[["CSDUID", "CSDNAME", "CDUID", "CDNAME", "PRUID", "PRNAME", "geometry"]]

    zip_us = telecharger(URL_COMTES_US, DIR_CACHE / "us_counties_2020.zip")
    with zipfile.ZipFile(zip_us) as z:
        nom = [n for n in z.namelist() if n.endswith(".txt")][0]
        comtes = pd.read_csv(z.open(nom), sep="\t", dtype={"GEOID": str})
    comtes.columns = [c.strip() for c in comtes.columns]
    return csd, comtes[["USPS", "GEOID", "NAME", "INTPTLAT", "INTPTLONG"]]


def cle_lieu(prov: pd.Series, csd: pd.Series, comte: pd.Series) -> pd.Series:
    """Clé brute d'un lieu ENR : CSD (Canada), comté FIPS ou État (États-Unis)."""
    cle = pd.Series(None, index=prov.index, dtype=object)
    canada = prov.str.startswith("CA-") & (csd.str.len() == 7)
    usa = prov.str.startswith("US-")
    cle[canada] = "C" + csd[canada]
    avec_comte = usa & (comte.str.len() == 5)
    cle[avec_comte] = "U" + comte[avec_comte]
    cle[usa & ~avec_comte] = "S" + prov[usa & ~avec_comte].str[3:]
    return cle


def localiser_points(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """CSD 2001 → CSD 2016 de même code, sinon division de recensement, sinon province."""
    csd, comtes = charger_references_geo()
    rep = csd.copy()
    rep["geometry"] = rep.geometry.representative_point()
    rep = rep.to_crs(CRS_WGS84)
    rep["lon"], rep["lat"] = rep.geometry.x, rep.geometry.y
    par_csd = rep.set_index("CSDUID")

    cd = csd.dissolve(by="CDUID", aggfunc={"CDNAME": "first", "PRUID": "first"})
    cd["geometry"] = cd.geometry.representative_point()
    cd = cd.to_crs(CRS_WGS84)
    pr = csd.dissolve(by="PRUID", aggfunc={"PRNAME": "first"})
    pr["geometry"] = pr.geometry.representative_point()
    pr = pr.to_crs(CRS_WGS84)

    comtes = comtes.set_index("GEOID")
    etats = comtes.groupby("USPS")[["INTPTLAT", "INTPTLONG"]].mean()

    df["cle_cp"] = cle_lieu(df["CP_PROV"], df["CP_CSDUID"], df["CP_USCOUNTY"])
    df["cle_cd"] = cle_lieu(df["CD_PROV"], df["CD_CSDUID"], df["CD_USCOUNTY"])
    prov_par_cle = pd.concat([
        df[["cle_cp", "CP_PROV"]].set_axis(["cle", "prov"], axis=1),
        df[["cle_cd", "CD_PROV"]].set_axis(["cle", "prov"], axis=1),
    ]).dropna().drop_duplicates("cle").set_index("cle")["prov"]

    resolus: dict[str, tuple] = {}   # clé brute → (id_point, lon, lat, qualité, prov, nom)
    for cle, prov in prov_par_cle.items():
        code, pays = cle[1:], ("US" if prov.startswith("US-") else prov)
        if cle[0] == "C" and code in par_csd.index:
            r = par_csd.loc[code]
            resolus[cle] = (cle, r.lon, r.lat, "csd", prov, r.CSDNAME)
        elif cle[0] == "C" and code[:4] in cd.index:
            r = cd.loc[code[:4]]
            resolus[cle] = ("D" + code[:4], r.geometry.x, r.geometry.y, "cd", prov, f"{r.CDNAME} (division)")
        elif cle[0] == "C" and code[:2] in pr.index:
            r = pr.loc[code[:2]]
            resolus[cle] = ("P" + code[:2], r.geometry.x, r.geometry.y, "prov", prov, f"{r.PRNAME} (province)")
        elif cle[0] == "U" and code in comtes.index:
            r = comtes.loc[code]
            resolus[cle] = (cle, r.INTPTLONG, r.INTPTLAT, "comte", pays, f"{r.NAME}, {r.USPS}")
        elif prov[3:] in etats.index:
            r = etats.loc[prov[3:]]
            resolus[cle] = ("S" + prov[3:], r.INTPTLONG, r.INTPTLAT, "etat", pays, f"{prov[3:]} (État)")

    points = (pd.DataFrame(resolus.values(), columns=["pid", "lon", "lat", "qual", "prov", "nom"])
                .drop_duplicates("pid").reset_index(drop=True))
    index_point = {pid: i for i, pid in enumerate(points["pid"])}
    vers_id = {cle: index_point[v[0]] for cle, v in resolus.items()}
    df["o_pt"] = df["cle_cp"].map(vers_id).astype("Int64")
    df["d_pt"] = df["cle_cd"].map(vers_id).astype("Int64")
    df["localise"] = df["o_pt"].notna() & df["d_pt"].notna()
    return df, points


# ---------------------------------------------------------------------------
# 4. Rattachement aux nœuds du graphe
# ---------------------------------------------------------------------------

def charger_graphe() -> tuple[nx.Graph, gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Graphe du volet 1 : 307 arcs pondérés par la longueur du tracé OSRM (km)."""
    arcs = gpd.read_file(FICHIER_DJMA)
    noeuds = gpd.read_file(FICHIER_NOEUDS, layer="noeuds").set_crs(CRS_QC, allow_override=True)
    noeuds["NOM"] = noeuds["NOM"].map(reparer_nom)
    for c in ("VILLE_A", "VILLE_B"):
        arcs[c] = arcs[c].map(reparer_nom)
    paires = arcs.apply(lambda r: frozenset((r.ID_A, r.ID_B)), axis=1)
    assert not paires.duplicated().any(), "arcs parallèles : passer à un MultiGraph"

    G = nx.Graph()
    for r in arcs.itertuples():
        km = r.longueur_trace_km if pd.notna(r.longueur_trace_km) else r.DIST_KM
        G.add_edge(r.ID_A, r.ID_B, km=float(km), id_arc=int(r.ID_ARC))
    noeuds = noeuds[noeuds["ID"].isin(G.nodes)].copy()
    return G, arcs, noeuds


def rattacher_noeuds(points: pd.DataFrame, noeuds: gpd.GeoDataFrame) -> pd.DataFrame:
    """Lieu québécois → nœud municipal le plus proche ; lieu hors Québec → frontières candidates."""
    geo = gpd.GeoDataFrame(points, geometry=gpd.points_from_xy(points.lon, points.lat), crs=CRS_WGS84)
    xy = np.column_stack([geo.to_crs(CRS_QC).geometry.x, geo.to_crs(CRS_QC).geometry.y])

    municipaux = noeuds[noeuds["TYPE"] == "municipalite"]
    mxy = np.column_stack([municipaux.geometry.x, municipaux.geometry.y])
    au_qc = (points["prov"] == "CA-QC").to_numpy()
    d2 = ((xy[au_qc, None, :] - mxy[None, :, :]) ** 2).sum(axis=2)
    plus_proche = d2.argmin(axis=1)
    points["noeud"] = None
    points.loc[au_qc, "noeud"] = municipaux["ID"].to_numpy()[plus_proche]
    points["dist_noeud_km"] = np.nan
    points.loc[au_qc, "dist_noeud_km"] = np.sqrt(d2[np.arange(len(plus_proche)), plus_proche]) / 1000

    frontieres = noeuds[noeuds["TYPE"].str.startswith("frontiere")].to_crs(CRS_WGS84).set_index("ID")
    candidats: list[list[tuple[str, float]]] = []
    for p in points.itertuples():
        if p.prov == "CA-QC":
            candidats.append([(p.noeud, 0.0)])
            continue
        ids = [f for f in FRONTIERES_CANDIDATES.get(p.prov, ()) if f in frontieres.index]
        candidats.append([
            (f, float(DETOUR_ROUTIER * haversine_km(np.array([p.lon]), np.array([p.lat]),
                                                     frontieres.at[f, "geometry"].x,
                                                     frontieres.at[f, "geometry"].y)[0]))
            for f in ids
        ])
    points["candidats"] = candidats
    return points


# ---------------------------------------------------------------------------
# 5. Routage sur le graphe
# ---------------------------------------------------------------------------

def router_camions(df: pd.DataFrame, points: pd.DataFrame, G: nx.Graph) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Plus court chemin par paire de lieux ; frontière = celle qui minimise la distance totale."""
    longueurs, chemins = {}, {}
    for source, (dist, chem) in nx.all_pairs_dijkstra(G, weight="km"):
        longueurs[source], chemins[source] = dist, chem

    garde = df["garde"] & df["localise"]
    paires = df.loc[garde, ["o_pt", "d_pt"]].drop_duplicates().astype(int)
    lignes = []
    for o, d in paires.itertuples(index=False):
        meilleur = None
        for a, ea in points.at[o, "candidats"]:
            for b, eb in points.at[d, "candidats"]:
                cout = ea + longueurs[a][b] + eb
                if meilleur is None or cout < meilleur[0]:
                    meilleur = (cout, a, b)
        if meilleur is None:            # aucun nœud candidat (ex. Mexique)
            lignes.append((o, d, None, None, None, ()))
            continue
        _, a, b = meilleur
        chemin = chemins[a][b]
        arcs = tuple(G.edges[u, v]["id_arc"] for u, v in zip(chemin[:-1], chemin[1:]))
        lignes.append((o, d, a, b, longueurs[a][b], arcs))
    routes = pd.DataFrame(lignes, columns=["o_pt", "d_pt", "noeud_o", "noeud_d", "km", "arcs"])

    df = df.merge(routes, on=["o_pt", "d_pt"], how="left")
    df["local"] = df["garde"] & df["noeud_o"].notna() & (df["noeud_o"] == df["noeud_d"])
    df["route"] = df["garde"] & df["arcs"].map(lambda a: isinstance(a, tuple) and len(a) > 0)
    return df, routes


def cumuler_par_arc(df: pd.DataFrame, arcs: gpd.GeoDataFrame, noms: dict[str, str]) -> dict:
    """Camions et tonnes annuels par arc et par groupe ; principales paires de nœuds."""
    groupes = list(GROUPES)
    ids_arcs = arcs["ID_ARC"].astype(int).tolist()
    pos_arc = {a: i for i, a in enumerate(ids_arcs)}
    routes = df[df["route"]].copy()
    routes["g"] = routes["groupe"].map({g: i for i, g in enumerate(groupes)})

    agg = (routes.groupby(["arcs", "g"])[["n3", "n4", "t3", "t4"]].sum()
                 .join(routes.groupby(["arcs", "g"]).size().rename("nobs")).reset_index())
    cumul = {m: np.zeros((len(ids_arcs), len(groupes))) for m in ("n3", "n4", "t3", "t4", "nobs")}
    for ligne in agg.itertuples(index=False):
        for a in ligne.arcs:
            for m in cumul:
                cumul[m][pos_arc[a], ligne.g] += getattr(ligne, m)

    paires = routes.assign(paire=routes.apply(
        lambda r: " ↔ ".join(sorted((noms[r.noeud_o], noms[r.noeud_d]))), axis=1))
    par_paire = paires.groupby(["arcs", "paire"])["n3"].sum().reset_index()
    tops: dict[int, list] = {a: [] for a in ids_arcs}
    lignes = [(a, p, n) for arcs_, p, n in par_paire.itertuples(index=False) for a in arcs_]
    if lignes:
        eclate = pd.DataFrame(lignes, columns=["arc", "paire", "n3"]).groupby(["arc", "paire"])["n3"].sum()
        for arc, serie in eclate.groupby(level=0):
            total = serie.sum()
            meilleurs = serie.droplevel(0).nlargest(TOP_PAIRES)
            tops[arc] = [[p, round(float(n / total), 3)] for p, n in meilleurs.items()]

    return {
        "ids": ids_arcs,
        **{m: np.round(cumul[m]).astype(int).tolist() for m in cumul},
        "top": [tops[a] for a in ids_arcs],
    }


# ---------------------------------------------------------------------------
# 6. Postes d'enquête et validation du routage
# ---------------------------------------------------------------------------

def valider_postes(df: pd.DataFrame, traces: gpd.GeoDataFrame) -> tuple[list[dict], dict]:
    """Accroche chaque poste QC au tracé le plus proche et mesure si le chemin calculé y passe."""
    postes_qc = df[df["dcs_code"].str.startswith("QC")]
    libelles = postes_qc.groupby("dcs_code")["wp_dcs"].first().str.upper().str.strip()
    resultats, details = [], []
    for code, libelle in libelles.items():
        localite = normaliser(libelle.split(" (")[0])
        parenthese = re.search(r"\(([^()]*:[^()]*)\)\s*$", libelle)
        contenu = parenthese.group(1) if parenthese else ""
        route = re.match(r"\s*([AR]-[\d/A-Z-]+)", contenu)
        sens = re.search(r"(NORTH/NORD|SOUTH/SUD|EAST/EST|WEST/OUEST)", contenu)
        lat, lon, nom = LOCALITES_POSTES[localite]
        point = gpd.GeoSeries(gpd.points_from_xy([lon], [lat]), crs=CRS_WGS84).to_crs(CRS_QC).iloc[0]
        distances = traces.geometry.distance(point)
        arc = int(traces.loc[distances.idxmin(), "ID_ARC"])

        ici = postes_qc[postes_qc["dcs_code"] == code]
        routes = ici[ici["route"]]
        passe = routes["arcs"].map(lambda a: arc in a)
        part_route = float((routes["n3"] * passe).sum() / routes["n3"].sum()) if len(routes) else None
        charges = ici[~ici["vide"]]
        top = (charges.groupby("groupe")["n3"].sum() / charges["n3"].sum()).nlargest(3) if len(charges) else pd.Series(dtype=float)
        resultats.append({
            "code": code, "localite": nom, "libelle": libelle.title(),
            "route": route.group(1) if route else "", "sens": sens.group(1).split("/")[1].title() if sens else "",
            "troncon": contenu.split(":", 1)[1].strip().title() if ":" in contenu else "",
            "lon": lon, "lat": lat, "arc": arc, "dist_arc_km": round(float(distances.min()) / 1000, 1),
            "nobs": int(len(ici)), "n3": round(float(ici["n3"].sum())),
            "pct_vides": round(float(ici["vide"].mean()), 3),
            "t_par_charge": round(float(charges["kg"].mean() / 1000), 1) if len(charges) else None,
            "top": [[GROUPES[g], round(float(p), 3)] for g, p in top.items()],
            "part_chemin": None if part_route is None else round(part_route, 3),
            "nobs_route": int(len(routes)),
        })
        details.append((routes["n3"] * passe).sum())

    poids_total = df.loc[df["dcs_code"].str.startswith("QC") & df["route"], "n3"].sum()
    hors = df[df["garde"] & ~df["dcs_code"].str.startswith("QC")]
    par_prov = hors.groupby(hors["dcs_code"].str[:2])["id"].count().sort_values(ascending=False)
    synthese = {
        "part_chemin_global": round(float(sum(details) / poids_total), 3) if poids_total else None,
        "hors_qc_par_prov": [[k, int(v)] for k, v in par_prov.items()],
    }
    return resultats, synthese


# ---------------------------------------------------------------------------
# 7. Agrégats d'affichage et export
# ---------------------------------------------------------------------------

def agreger_flux(df: pd.DataFrame) -> dict:
    """Flux ramassage → livraison entre points, par catégorie et groupe (colonnes compactes)."""
    loc = df[df["localise"]].copy()
    loc["g"] = loc["groupe"].map({g: i for i, g in enumerate(GROUPES)})
    loc["c"] = loc["cat"].map({c: i for i, c in enumerate(["qc_poste_qc", "qc_poste_hors", "transit", "ecarte"])})
    flux = (loc.groupby(["o_pt", "d_pt", "c", "g"])
               .agg(nobs=("id", "size"), n3=("n3", "sum"), t3=("t3", "sum")).reset_index())
    flux = flux[flux["o_pt"] != flux["d_pt"]]
    return {
        "o": flux["o_pt"].astype(int).tolist(), "d": flux["d_pt"].astype(int).tolist(),
        "c": flux["c"].astype(int).tolist(), "g": flux["g"].astype(int).tolist(),
        "nobs": flux["nobs"].astype(int).tolist(),
        "n3": flux["n3"].round().astype(int).tolist(), "t3": flux["t3"].round().astype(int).tolist(),
    }


def agreger_noeuds(df: pd.DataFrame, points: pd.DataFrame, noeuds: gpd.GeoDataFrame) -> tuple[list, list]:
    """Bouts de trajet (ramassages + livraisons) par nœud, et nœud retenu pour chaque point."""
    routes = df[df["garde"] & df["noeud_o"].notna()]
    bouts = pd.concat([
        routes[["o_pt", "noeud_o", "n3", "t3", "vide", "local"]].set_axis(["pt", "noeud", "n3", "t3", "vide", "local"], axis=1),
        routes[["d_pt", "noeud_d", "n3", "t3", "vide", "local"]].set_axis(["pt", "noeud", "n3", "t3", "vide", "local"], axis=1),
    ])
    par_noeud = bouts.groupby("noeud").agg(n3=("n3", "sum"), t3=("t3", "sum"), npts=("pt", "nunique"))
    par_noeud["n3_vides"] = bouts[bouts["vide"]].groupby("noeud")["n3"].sum()
    par_noeud["n3_local"] = bouts[bouts["local"]].groupby("noeud")["n3"].sum()
    par_noeud = par_noeud.fillna(0)

    # Point → nœud le plus souvent retenu (pondéré), pour les lignes de rattachement
    vers = bouts.groupby(["pt", "noeud"])["n3"].sum().reset_index().sort_values("n3", ascending=False)
    vers = vers.drop_duplicates("pt").set_index("pt")
    poids_pt = bouts.groupby("pt")["n3"].sum()

    g = noeuds.to_crs(CRS_WGS84)
    liste_noeuds = []
    for r in g.itertuples():
        a = par_noeud.loc[r.ID] if r.ID in par_noeud.index else None
        liste_noeuds.append({
            "id": r.ID, "nom": r.NOM, "type": r.TYPE, "lon": round(r.geometry.x, 4), "lat": round(r.geometry.y, 4),
            "n3": 0 if a is None else round(float(a.n3)), "t3": 0 if a is None else round(float(a.t3)),
            "n3_vides": 0 if a is None else round(float(a.n3_vides)),
            "n3_local": 0 if a is None else round(float(a.n3_local)), "npts": 0 if a is None else int(a.npts),
        })
    rattache = [vers.at[i, "noeud"] if i in vers.index else None for i in points.index]
    poids = [round(float(poids_pt.get(i, 0))) for i in points.index]
    return liste_noeuds, list(zip(rattache, poids))


def fond_de_carte() -> dict:
    """Natural Earth 50 m (provinces/États, océan, lacs), simplifié pour le web."""
    emprise = box(-175, 0, -20, 85)
    admin = gpd.read_file(f"zip://{DIR_REFERENCE / 'ne_50m_admin_1_states_provinces.zip'}")
    admin = admin[admin["adm0_a3"].isin(["CAN", "USA"])].copy()
    admin["qc"] = admin["name"].str.contains("Qu.bec", regex=True)
    admin["geometry"] = admin.geometry.simplify(0.02, preserve_topology=True)
    ocean = gpd.read_file(f"zip://{DIR_REFERENCE / 'ne_50m_ocean.zip'}").clip(emprise)
    ocean["geometry"] = ocean.geometry.simplify(0.02, preserve_topology=True)
    lacs = gpd.read_file(f"zip://{DIR_REFERENCE / 'ne_50m_lakes.zip'}").clip(emprise)
    lacs["geometry"] = lacs.geometry.simplify(0.01, preserve_topology=True)
    lacs = lacs[lacs.to_crs("EPSG:3978").geometry.area > 2e8]   # lacs de plus de 200 km²
    return {
        "admin": geojson(admin, ["qc"], 3),
        "ocean": geojson(ocean.assign(k=1), ["k"], 3),
        "lacs": geojson(lacs.assign(k=1), ["k"], 3),
    }


def couche_arcs(arcs: gpd.GeoDataFrame) -> dict:
    """Tracés OSRM simplifiés (repli : ligne droite) + DJMA du volet 1 par arc."""
    traces = gpd.read_file(FICHIER_TRACES, layer="trace_osrm").drop_duplicates("ID_ARC").set_index("ID_ARC")
    sortie = arcs.to_crs(CRS_QC).copy()
    for i, r in sortie.iterrows():
        t = traces.geometry.get(r.ID_ARC)
        if t is not None and not t.is_empty:
            sortie.at[i, "geometry"] = t.simplify(TOL_ARC_M)
    sortie = sortie.to_crs(CRS_WGS84)
    props = {
        "id": sortie["ID_ARC"].astype(int), "a": sortie["VILLE_A"], "b": sortie["VILLE_B"],
        "km": sortie["longueur_trace_km"].fillna(sortie["DIST_KM"]).round(1), "statut": sortie["statut"],
    }
    for m in (1, 2, 3, 4):
        props[f"djma_m{m}"] = sortie[f"djma_m{m}"].round()
        props[f"cam_m{m}"] = sortie[f"djma_cam_m{m}"].round()
    props["pct_cam_m4"] = sortie["pct_cam_m4"].round(1)
    tableau = gpd.GeoDataFrame(pd.DataFrame(props), geometry=sortie.geometry, crs=CRS_WGS84)
    tableau = tableau.astype({c: object for c in tableau.columns if c != "geometry"})
    tableau = tableau.where(pd.notna(tableau), None)
    return geojson(tableau, list(props), 4)


def verifier_statcan() -> dict:
    """Teste l'API WDS de StatCan (CFAF 23-10-0142-01, IPPI 18-10-0266-01).

    Si la CFAF répond, on garde les libellés de ses groupes de marchandises pour
    préparer la table de passage ENR → CFAF ; sinon on note l'indisponibilité.
    """
    url = "https://www150.statcan.gc.ca/t1/wds/rest/getCubeMetadata"
    etat: dict = {"verifie_le": date.today().isoformat()}
    for nom, pid in (("cfaf", 23100142), ("ippi", 18100266)):
        corps = json.dumps([{"productId": pid}]).encode()
        requete = urllib.request.Request(url, data=corps, headers={
            "Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(requete, timeout=30) as reponse:
                objet = json.loads(reponse.read())[0]["object"]
        except (OSError, ValueError, KeyError, IndexError):
            etat[nom] = "indisponible"
            continue
        etat[nom] = "disponible"
        if nom == "cfaf":
            for dim in objet["dimension"]:
                if "commodit" in dim["dimensionNameEn"].lower():
                    etat["cfaf_groupes"] = [m["memberNameFr"] for m in dim["member"]]
    return etat


def extraits_code() -> dict[str, str]:
    """Code source réel de chaque étape, affiché dans le panneau « Logique »."""
    source = inspect.getsource(inspect.getmodule(extraits_code))
    debut = source.index("MARCHANDISE_VERS_GROUPE: dict")
    table = source[debut: source.index("\n}\n", debut) + 2]
    return {
        "graphe": inspect.getsource(charger_graphe),
        "table": table,
        "statcan": inspect.getsource(verifier_statcan),
        "population": inspect.getsource(classer_population),
        "localisation": inspect.getsource(cle_lieu) + "\n\n" + inspect.getsource(localiser_points),
        "rattachement": inspect.getsource(rattacher_noeuds),
        "routage": inspect.getsource(router_camions) + "\n\n" + inspect.getsource(cumuler_par_arc),
        "postes": inspect.getsource(valider_postes),
        "chargement": inspect.getsource(charger_enr) + "\n\n" + inspect.getsource(groupe_marchandise),
    }


def version_publique(donnees: dict) -> dict:
    """Retire les données ENR détaillées (flux entre lieux, lieux, bouts par nœud).

    Restent les chiffres globaux, les résultats par arc et par poste, et le code.
    """
    publique = dict(donnees)
    publique["flux"] = {k: [] for k in donnees["flux"]}
    publique["points"] = {k: [] for k in donnees["points"]}
    publique["noeuds"] = [{**n, "n3": 0, "t3": 0, "n3_vides": 0, "n3_local": 0, "npts": 0} for n in donnees["noeuds"]]
    publique["meta"] = {**donnees["meta"], "publique": True}
    return publique


def exporter(donnees: dict, cible: Path) -> None:
    """Injecte les données dans le gabarit HTML (page autonome)."""
    gabarit = GABARIT.read_text(encoding="utf-8")
    charge = json.dumps(donnees, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    charge = charge.replace("</", "<\\/")
    html = gabarit.replace("/*__DONNEES__*/null", charge, 1)
    cible.parent.mkdir(parents=True, exist_ok=True)
    cible.write_text(html, encoding="utf-8")
    print(f"→ {cible.relative_to(RACINE)} ({cible.stat().st_size / 1e6:.1f} Mo)")


def main(publique: bool = False) -> None:
    """Exécute la chaîne complète et écrit la page (et sa version publique si demandé)."""
    print("[1/7] ENR")
    df = classer_population(charger_enr())
    print("[2/7] localisation des CSD et des comtés")
    df, points = localiser_points(df)
    print("[3/7] graphe et rattachement aux nœuds")
    G, arcs, noeuds = charger_graphe()
    points = rattacher_noeuds(points, noeuds)
    noms = dict(zip(noeuds["ID"], noeuds["NOM"]))
    print("[4/7] routage")
    df, routes = router_camions(df, points, G)
    par_arc = cumuler_par_arc(df, arcs, noms)
    print("[5/7] postes d'enquête")
    traces = gpd.read_file(FICHIER_TRACES, layer="trace_osrm").drop_duplicates("ID_ARC")
    postes, synthese_postes = valider_postes(df, traces)
    print("[6/7] agrégats d'affichage")
    liste_noeuds, rattache = agreger_noeuds(df, points, noeuds)

    garde = df[df["garde"]]
    bouts = pd.concat([garde[["o_pt", "n3"]].rename(columns={"o_pt": "pt"}),
                       garde[["d_pt", "n3"]].rename(columns={"d_pt": "pt"})])
    qual = points["qual"].reindex(bouts["pt"].astype("float").fillna(-1).astype(int)).fillna("non_localise").to_numpy()
    qualite_bouts = pd.Series(bouts["n3"].to_numpy()).groupby(qual).sum()
    qualite_bouts = (qualite_bouts / qualite_bouts.sum()).round(4).to_dict()

    charges = garde[~garde["vide"]]
    meta = {
        "genere_le": date.today().isoformat(),
        "n_total": int(len(df)), "n_garde": int(garde.shape[0]), "n_vides_garde": int(garde["vide"].sum()),
        "cats": {c: int((df["cat"] == c).sum()) for c in ["qc_poste_qc", "qc_poste_hors", "transit", "ecarte"]},
        "n3_par_cat": {c: round(float(df.loc[df["cat"] == c, "n3"].sum())) for c in ["qc_poste_qc", "qc_poste_hors", "transit", "ecarte"]},
        "n3_total": round(float(df["n3"].sum())), "n3_garde": round(float(garde["n3"].sum())),
        "t3_garde": round(float(garde["t3"].sum())),
        "pct_vides_total": round(float(df["vide"].mean()), 3),
        "pct_vides_garde_pondere": round(float(garde.loc[garde["vide"], "n3"].sum() / garde["n3"].sum()), 3),
        "t_par_charge": round(float((charges["t3"].sum() / charges["n3"].sum())), 2),
        "n_kg_impute": int(garde["kg_impute"].sum()),
        "n_localises_garde": int(garde["localise"].sum()),
        "n_points": int(len(points)),
        "qualite_bouts": qualite_bouts,
        "n_local": int(df["local"].sum()), "n3_local": round(float(df.loc[df["local"], "n3"].sum())),
        "n_route": int(df["route"].sum()), "n3_route": round(float(df.loc[df["route"], "n3"].sum())),
        "n_paires_routees": int(routes["arcs"].map(len).gt(0).sum()),
        "detour": DETOUR_ROUTIER,
        "groupes_obs": {g: int((garde["groupe"] == g).sum()) for g in GROUPES},
        "groupes_t3": {g: round(float(garde.loc[garde["groupe"] == g, "t3"].sum())) for g in GROUPES},
        **synthese_postes,
        "statcan": verifier_statcan(),
    }
    donnees = {
        "meta": meta,
        "groupes": [[k, v] for k, v in GROUPES.items()],
        "fond": fond_de_carte(),
        "arcs": couche_arcs(arcs),
        "par_arc": par_arc,
        "noeuds": liste_noeuds,
        "points": {
            "lon": points["lon"].round(4).tolist(), "lat": points["lat"].round(4).tolist(),
            "qual": points["qual"].tolist(), "prov": points["prov"].tolist(), "nom": points["nom"].tolist(),
            "noeud": [r[0] for r in rattache], "n3": [r[1] for r in rattache],
            "dist": [None if pd.isna(v) else round(float(v), 1) for v in points["dist_noeud_km"]],
        },
        "flux": agreger_flux(df),
        "postes": postes,
        "code": extraits_code(),
    }
    print("[7/7] export")
    exporter(donnees, SORTIE_HTML)
    if publique:
        exporter(version_publique(donnees), SORTIE_PUBLIQUE)


if __name__ == "__main__":
    analyseur = argparse.ArgumentParser(description=__doc__.splitlines()[3])
    analyseur.add_argument("--publique", action="store_true",
                           help="écrire aussi docs/volet2/carte_interactive.html, sans données ENR détaillées")
    main(analyseur.parse_args().publique)
