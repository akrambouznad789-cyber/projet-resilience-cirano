"""
algo_graphe_reseau_v2.py
========================
Projet CIRANO II — Construction du graphe routier enrichi
VERSION 2 — Correction de l'algorithme de liaison segment-arc

PROBLÈMES CORRIGÉS PAR RAPPORT À V1
-------------------------------------
V1 utilisait un corridor rectangulaire orienté A→B + intersection booléenne.
Deux défauts observés visuellement dans QGIS :

  1. SOUS-CAPTURE : le clip rectangulaire éliminait des segments DJMA sur
     la route réelle dès que celle-ci s'écartait de l'axe direct A→B
     (virage, contournement). Des portions de route avec comptage disponible
     étaient ignorées, biaisant le djma_1 à la baisse.

  2. FAUX POSITIFS : certains segments entraient dans le corridor rectangulaire
     géographiquement sans être sur le bon chemin (route adjacente ou
     perpendiculaire). Leur inclusion polluait le calcul DJMA.

MÉTHODE V2 — Trois filtres séquentiels
----------------------------------------
  Filtre 1 — Distance au tracé OSRM (remplace le corridor rectangulaire)
    On mesure la distance du centroïde de chaque segment à la polyline OSRM.
    Seuil : 400 m. Capture les segments géométriquement décalés par rapport
    à OSRM mais représentant la même route (divergence entre référentiels MTQ
    et OSRM fréquente hors zones urbaines).

  Filtre 2 — Alignement directionnel (élimine les faux positifs)
    On compare l'orientation locale du segment DJMA à celle du tracé OSRM
    au point le plus proche. Si l'angle dépasse 45°, le segment est sur une
    autre route (perpendiculaire ou parallèle adjacente) → exclu.

  Filtre 3 — Proximité aux noeuds (conservé de v1)
    Exclut les segments dont le centroïde est plus proche d'un noeud du
    réseau autre que A ou B, évitant l'attribution à un arc voisin.

PARAMÈTRES AJUSTABLES
---------------------
BUFFER_RECHERCHE_M   : zone de candidats autour du tracé OSRM (défaut 1500m)
DIST_MAX_TRACE_M     : distance max centroïde→tracé OSRM (défaut 400m)
ANGLE_MAX_DEG        : écart angulaire maximal toléré (défaut 45°)
BUFFER_EXCLUSION_M   : zone d'exclusion autour de chaque noeud (défaut 2000m)
SAMPLE_N_ARCS        : nombre d'arcs à traiter (None = tous)
PILOT_ID_ARC         : tester sur un seul arc (prioritaire sur SAMPLE_N_ARCS)
PAUSE_API_S          : pause entre requêtes OSRM (défaut 0.5s)

SORTIES (3 couches dans le gpkg)
---------------------------------
arcs_enrichis_v2     Arcs avec segments DJMA et métadonnées v2
trajets_segments_v2  Un enregistrement par segment DJMA retenu
trace_osrm_v2        Tracé brut OSRM par arc
"""

import os
import math
import geopandas as gpd
import pandas as pd
import numpy as np
import requests
import time
from shapely.geometry import LineString, Point
from shapely.ops import nearest_points, unary_union
import warnings

warnings.filterwarnings("ignore")

# ============================================================
# CONFIGURATION
# ============================================================

PATH_ARCS   = os.path.expanduser("~/projects/projet-resilience-cirano/data/raw/reseau_arcs.gpkg")
PATH_RTSS   = os.path.expanduser("~/projects/projet-resilience-cirano/data/raw/ReseauRoutier_RTSS.gpkg")
PATH_DJMA   = os.path.expanduser("~/projects/projet-resilience-cirano/data/raw/DebitCirculation.gpkg")
OUTPUT_FILE = os.path.expanduser("~/projects/projet-resilience-cirano/data/processed/graphe_routier_v2_sample.gpkg")

LAYER_ARCS   = "arcs"
LAYER_NOEUDS = "noeuds"
LAYER_RTSS   = "bgr_v_sous_route_res_sup_act"
LAYER_DJMA   = "circulation_routier"

CRS_WORK = "EPSG:32198"
CRS_WGS  = "EPSG:4326"

BUFFER_RECHERCHE_M = 1500   # zone de candidats autour du tracé OSRM
DIST_MAX_TRACE_M   = 400    # distance max centroïde → tracé OSRM (filtre 1)
ANGLE_MAX_DEG      = 45     # écart angulaire max segment vs OSRM (filtre 2)
BUFFER_EXCLUSION_M = 2000   # zone autour de chaque noeud exclue (filtre 3)
PAUSE_API_S        = 0.5
SAMPLE_N_ARCS      = 30     # None = tous les arcs
PILOT_ID_ARC       = None   # ex: 50 pour tester un seul arc


# ============================================================
# CHARGEMENT
# ============================================================

def charger_djma(path: str, layer: str, crs: str) -> gpd.GeoDataFrame:
    """
    Charge DebitCirculation et extrait la valeur DJMA et camion
    la plus récente disponible par segment (colonnes annee_1 à annee_10).
    """
    print("  Chargement DebitCirculation...")
    djma = gpd.read_file(path, layer=layer).to_crs(crs)
    djma["longueur_m"] = djma.geometry.length
    djma["djma_val"]   = np.nan
    djma["djma_annee"] = None
    djma["cam_val"]    = np.nan
    djma["cam_annee"]  = None

    for yr in range(1, 11):
        col_v = f"val_djma_annee_{yr}"
        col_a = f"djma_annee_{yr}"
        if col_v in djma.columns:
            mask = djma["djma_val"].isna()
            vals = pd.to_numeric(djma.loc[mask, col_v], errors="coerce")
            djma.loc[mask & vals.notna(), "djma_val"]   = vals[vals.notna()]
            djma.loc[mask & vals.notna(), "djma_annee"] = djma.loc[mask & vals.notna(), col_a]

        col_v = f"val_cam_annee_{yr}"
        col_a = f"cam_annee_{yr}"
        if col_v in djma.columns:
            mask = djma["cam_val"].isna()
            vals = pd.to_numeric(djma.loc[mask, col_v], errors="coerce")
            djma.loc[mask & vals.notna(), "cam_val"]   = vals[vals.notna()]
            djma.loc[mask & vals.notna(), "cam_annee"] = djma.loc[mask & vals.notna(), col_a]

    n_avec = djma["djma_val"].notna().sum()
    print(f"  DJMA : {n_avec}/{len(djma)} segments avec valeur de comptage")
    return djma


def charger_rtss(path: str, layer: str, crs: str) -> gpd.GeoDataFrame:
    """Charge ReseauRoutier_RTSS."""
    print("  Chargement ReseauRoutier_RTSS...")
    rtss = gpd.read_file(path, layer=layer).to_crs(crs)
    rtss["seg_long_m"] = rtss.geometry.length
    print(f"  RTSS : {len(rtss)} segments chargés")
    return rtss


# ============================================================
# ROUTAGE OSRM
# ============================================================

def obtenir_trace_osrm(lon_a: float, lat_a: float,
                        lon_b: float, lat_b: float) -> tuple:
    """
    Appelle l'API OSRM publique.
    Retourne (LineString WGS84, statut, distance_m) ou (None, code_erreur, 0).
    """
    url = (f"http://router.project-osrm.org/route/v1/driving/"
           f"{lon_a},{lat_a};{lon_b},{lat_b}"
           f"?overview=full&geometries=geojson")
    try:
        resp = requests.get(url, timeout=10)
        data = resp.json()
        if data.get("code") != "Ok":
            return None, "echec_osrm", 0
        coords    = data["routes"][0]["geometry"]["coordinates"]
        distance_m = data["routes"][0]["distance"]
        return LineString(coords), "ok", distance_m
    except Exception:
        return None, "erreur_api", 0


# ============================================================
# UTILITAIRES GÉOMÉTRIQUES
# ============================================================

def angle_linestring(geom: LineString) -> float:
    """
    Retourne l'angle dominant d'une LineString en degrés [0, 180[.
    Calculé entre le premier et le dernier sommet pour représenter
    l'orientation générale du segment.
    """
    coords = list(geom.coords)
    dx = coords[-1][0] - coords[0][0]
    dy = coords[-1][1] - coords[0][1]
    angle = math.degrees(math.atan2(dy, dx)) % 180
    return angle


def angle_local_trace(trace: LineString, pt: Point) -> float:
    """
    Retourne l'angle local du tracé OSRM au point le plus proche de pt.
    On prend les deux sommets encadrant le point de projection.
    """
    coords = list(trace.coords)
    min_dist = float("inf")
    best_i   = 0
    for i in range(len(coords) - 1):
        seg = LineString([coords[i], coords[i + 1]])
        d   = seg.distance(pt)
        if d < min_dist:
            min_dist = d
            best_i   = i
    dx = coords[best_i + 1][0] - coords[best_i][0]
    dy = coords[best_i + 1][1] - coords[best_i][1]
    return math.degrees(math.atan2(dy, dx)) % 180


def diff_angle(a1: float, a2: float) -> float:
    """Différence angulaire minimale entre deux angles [0, 180[ en degrés."""
    diff = abs(a1 - a2) % 180
    return min(diff, 180 - diff)


# ============================================================
# FILTRES V2
# ============================================================

def filtre_distance_trace(segs: gpd.GeoDataFrame,
                           trace: LineString,
                           dist_max_m: float) -> gpd.GeoDataFrame:
    """
    Filtre 1 — Distance centroïde → tracé OSRM.

    Conserve les segments dont le centroïde est à moins de dist_max_m
    de la polyline OSRM. Remplace le corridor rectangulaire de v1 qui
    éliminait des segments légitimes sur les portions de route déviant
    de l'axe direct A→B.
    """
    if segs.empty:
        return segs
    distances = segs.geometry.centroid.distance(trace)
    return segs[distances <= dist_max_m].copy()


def filtre_direction(segs: gpd.GeoDataFrame,
                     trace: LineString,
                     angle_max_deg: float) -> gpd.GeoDataFrame:
    """
    Filtre 2 — Alignement directionnel segment vs tracé OSRM.

    Exclut les segments dont l'orientation diverge de plus de angle_max_deg
    par rapport à l'orientation locale du tracé OSRM au point le plus proche.
    Élimine les faux positifs (routes perpendiculaires ou parallèles adjacentes)
    qui passaient le filtre de distance malgré un tracé incohérent.
    """
    if segs.empty:
        return segs
    masque = []
    for _, row in segs.iterrows():
        centroid    = row.geometry.centroid
        angle_seg   = angle_linestring(row.geometry)
        angle_osrm  = angle_local_trace(trace, centroid)
        masque.append(diff_angle(angle_seg, angle_osrm) <= angle_max_deg)
    return segs[masque].copy()


def filtre_noeud_proximite(segs: gpd.GeoDataFrame,
                            pt_a: Point, pt_b: Point,
                            tous_noeuds: gpd.GeoDataFrame,
                            exclusion_m: float) -> gpd.GeoDataFrame:
    """
    Filtre 3 — Proximité aux noeuds (conservé de v1).

    Exclut les segments dont le centroïde est plus proche d'un noeud du
    réseau autre que A ou B, et dont la distance à ce noeud tiers est
    inférieure à exclusion_m. Évite d'attribuer à l'arc A→B des segments
    qui appartiennent logiquement à un arc voisin.

    Désactivé si la distance A-B est inférieure à 2 × exclusion_m
    (villes très proches où la zone d'exclusion couvrirait tout le trajet).
    """
    if segs.empty:
        return segs
    dist_ab = pt_a.distance(pt_b)
    if dist_ab <= 2 * exclusion_m:
        return segs

    autres_noeuds = tous_noeuds[
        ~tous_noeuds.geometry.isin([pt_a, pt_b])
    ].geometry

    if autres_noeuds.empty:
        return segs

    masque = []
    for _, row in segs.iterrows():
        centroid    = row.geometry.centroid
        dist_a      = centroid.distance(pt_a)
        dist_b      = centroid.distance(pt_b)
        dist_ab_min = min(dist_a, dist_b)
        dist_tiers  = autres_noeuds.distance(centroid).min()
        # Exclure seulement si un noeud tiers est plus proche ET dans la zone d'exclusion
        masque.append(not (dist_tiers < dist_ab_min and dist_tiers < exclusion_m))

    return segs[masque].copy()


# ============================================================
# EXTRACTION DES SEGMENTS
# ============================================================

def extraire_rtss(zone_recherche, rtss_gdf: gpd.GeoDataFrame,
                   rtss_sindex) -> list:
    """Extraction simple des segments RTSS intersectant la zone de recherche."""
    cands = list(rtss_sindex.intersection(zone_recherche.bounds))
    segs  = rtss_gdf.iloc[cands][rtss_gdf.iloc[cands].geometry.intersects(zone_recherche)]
    if segs.empty:
        return []
    return [
        {"num_rts": row["num_rts"],
         "long_m" : round(row["seg_long_m"], 1),
         "type"   : row["des_clasf_"]}
        for _, row in segs.iterrows()
    ]


def extraire_djma_v2(trace: LineString,
                      djma_gdf: gpd.GeoDataFrame,
                      djma_sindex,
                      pt_a: Point, pt_b: Point,
                      tous_noeuds: gpd.GeoDataFrame) -> list:
    """
    Extraction des segments DJMA avec les trois filtres v2.

    Étape 1 : candidats dans le buffer de recherche (1500m)
    Étape 2 : filtre distance centroïde → tracé (400m)
    Étape 3 : filtre directionnel (45°)
    Étape 4 : filtre proximité noeud (2000m)
    """
    zone_recherche = trace.buffer(BUFFER_RECHERCHE_M)
    cands = list(djma_sindex.intersection(zone_recherche.bounds))
    if not cands:
        return []

    candidats = djma_gdf.iloc[cands][
        djma_gdf.iloc[cands].geometry.intersects(zone_recherche)
    ].copy()

    candidats = filtre_distance_trace(candidats, trace, DIST_MAX_TRACE_M)
    candidats = filtre_direction(candidats, trace, ANGLE_MAX_DEG)
    candidats = filtre_noeud_proximite(candidats, pt_a, pt_b,
                                        tous_noeuds, BUFFER_EXCLUSION_M)

    if candidats.empty:
        return []

    return [
        {"ide_sectn_trafc": row["ide_sectn_trafc"],
         "djma_val"       : row["djma_val"],
         "djma_annee"     : row["djma_annee"],
         "cam_val"        : row["cam_val"],
         "cam_annee"      : row["cam_annee"],
         "longueur_m"     : row["longueur_m"],
         "geometry"       : row.geometry}
        for _, row in candidats.iterrows()
    ]


def formater_liste(valeurs: list, sep: str = "|") -> str | None:
    return sep.join(str(v) for v in valeurs) if valeurs else None


def calculer_statut_djma_pct(segs_djma: list) -> float | None:
    if not segs_djma:
        return None
    n_avec = sum(1 for s in segs_djma if pd.notna(s["djma_val"]))
    return round(n_avec / len(segs_djma) * 100, 1)


# ============================================================
# PIPELINE PRINCIPAL
# ============================================================

def main():
    print("=" * 65)
    print("GRAPHE ROUTIER MTQ — Projet CIRANO II — VERSION 2")
    print("Méthode : distance tracé + filtre directionnel + filtre noeud")
    print("=" * 65)

    print("\n[1/4] Chargement des données...")
    arcs   = gpd.read_file(PATH_ARCS, layer=LAYER_ARCS).set_crs(CRS_WORK, allow_override=True)
    noeuds = gpd.read_file(PATH_ARCS, layer=LAYER_NOEUDS).set_crs(CRS_WORK, allow_override=True)
    rtss   = charger_rtss(PATH_RTSS, LAYER_RTSS, CRS_WORK)
    djma   = charger_djma(PATH_DJMA, LAYER_DJMA, CRS_WORK)

    rtss_sindex = rtss.sindex
    djma_sindex = djma.sindex

    noeuds_wgs     = noeuds.to_crs(CRS_WGS).set_index("ID")
    noeuds_lambert = noeuds.set_index("ID")

    if PILOT_ID_ARC is not None:
        arcs_traiter = arcs[arcs["ID_ARC"] == PILOT_ID_ARC]
    elif SAMPLE_N_ARCS is not None:
        arcs_traiter = arcs.head(SAMPLE_N_ARCS)
    else:
        arcs_traiter = arcs

    print(f"\n[2/4] Routage OSRM + extraction v2 ({len(arcs_traiter)} arcs)...")

    rows_arcs       = []
    rows_segs       = []
    rows_trace_osrm = []

    CHAMPS_EXCLUS = {"geometry", "fid"}

    for _, arc in arcs_traiter.iterrows():
        arc_id  = arc["ID_ARC"]
        id_a    = arc["ID_A"]
        id_b    = arc["ID_B"]
        ville_a = arc.get("VILLE_A", id_a)
        ville_b = arc.get("VILLE_B", id_b)

        print(f"  Arc {arc_id:4d} | {str(ville_a)[:20]:<20} → {str(ville_b)[:20]:<20}", end=" ")

        base = {k: arc[k] for k in arc.index if k not in CHAMPS_EXCLUS}
        base["geometry"] = arc["geometry"]

        def echec(statut: str):
            base.update({
                "ids_segs_rtss"        : None,
                "ids_segs_rtss_dist"   : None,
                "ids_segs_rtss_type"   : None,
                "ids_segs_djma"        : None,
                "ids_segs_djma_val"    : None,
                "ids_segs_djma_val_cam": None,
                "statut_djma_pct"      : None,
                "longueur_trace_km"    : None,
                "n_segs_djma"          : 0,
                "statut"               : statut,
                "methode"              : "v2",
            })
            rows_arcs.append(base.copy())

        if id_a not in noeuds_wgs.index or id_b not in noeuds_wgs.index:
            print("[noeud_manquant]")
            echec("noeud_manquant")
            continue

        pt_a_wgs     = noeuds_wgs.loc[id_a, "geometry"]
        pt_b_wgs     = noeuds_wgs.loc[id_b, "geometry"]
        pt_a_lambert = noeuds_lambert.loc[id_a, "geometry"]
        pt_b_lambert = noeuds_lambert.loc[id_b, "geometry"]

        trace_wgs, statut_osrm, distance_osrm_m = obtenir_trace_osrm(
            pt_a_wgs.x, pt_a_wgs.y, pt_b_wgs.x, pt_b_wgs.y
        )
        time.sleep(PAUSE_API_S)

        if trace_wgs is None:
            print(f"[{statut_osrm}]")
            echec(statut_osrm)
            continue

        trace_lambert     = gpd.GeoSeries([trace_wgs], crs=CRS_WGS).to_crs(CRS_WORK).iloc[0]
        longueur_trace_km = round(distance_osrm_m / 1000, 2)

        rows_trace_osrm.append({
            "ID_ARC"           : arc_id,
            "VILLE_A"          : ville_a,
            "VILLE_B"          : ville_b,
            "longueur_trace_km": longueur_trace_km,
            "geometry"         : trace_lambert,
        })

        zone_recherche = trace_lambert.buffer(BUFFER_RECHERCHE_M)
        segs_rtss = extraire_rtss(zone_recherche, rtss, rtss_sindex)

        segs_djma = extraire_djma_v2(
            trace_lambert, djma, djma_sindex,
            pt_a_lambert, pt_b_lambert, noeuds_lambert
        )

        if not segs_djma:
            print("[aucun_djma]")
            base.update({
                "ids_segs_rtss"        : formater_liste([s["num_rts"] for s in segs_rtss]),
                "ids_segs_rtss_dist"   : formater_liste([s["long_m"]  for s in segs_rtss]),
                "ids_segs_rtss_type"   : formater_liste([s["type"]    for s in segs_rtss]),
                "ids_segs_djma"        : None,
                "ids_segs_djma_val"    : None,
                "ids_segs_djma_val_cam": None,
                "statut_djma_pct"      : 0.0,
                "longueur_trace_km"    : longueur_trace_km,
                "n_segs_djma"          : 0,
                "statut"               : "aucun_djma",
                "methode"              : "v2",
            })
            rows_arcs.append(base.copy())
            continue

        djma_vals = []
        cam_vals  = []
        for s in segs_djma:
            if pd.notna(s["djma_val"]):
                annee = int(s["djma_annee"]) if pd.notna(s["djma_annee"]) else "?"
                djma_vals.append(f"{int(s['djma_val'])}@{annee}")
            else:
                djma_vals.append("NA")
            if pd.notna(s["cam_val"]):
                annee = int(s["cam_annee"]) if pd.notna(s["cam_annee"]) else "?"
                cam_vals.append(f"{s['cam_val']}@{annee}")
            else:
                cam_vals.append("NA")

        statut_pct = calculer_statut_djma_pct(segs_djma)

        base.update({
            "ids_segs_rtss"        : formater_liste([s["num_rts"] for s in segs_rtss]),
            "ids_segs_rtss_dist"   : formater_liste([s["long_m"]  for s in segs_rtss]),
            "ids_segs_rtss_type"   : formater_liste([s["type"]    for s in segs_rtss]),
            "ids_segs_djma"        : formater_liste([s["ide_sectn_trafc"] for s in segs_djma]),
            "ids_segs_djma_val"    : formater_liste(djma_vals),
            "ids_segs_djma_val_cam": formater_liste(cam_vals),
            "statut_djma_pct"      : statut_pct,
            "longueur_trace_km"    : longueur_trace_km,
            "n_segs_djma"          : len(segs_djma),
            "statut"               : "ok",
            "methode"              : "v2",
        })
        rows_arcs.append(base.copy())

        for s in segs_djma:
            rows_segs.append({
                "ID_ARC"          : arc_id,
                "VILLE_A"         : ville_a,
                "VILLE_B"         : ville_b,
                "ide_sectn_trafc" : s["ide_sectn_trafc"],
                "djma_val"        : s["djma_val"],
                "djma_annee"      : s["djma_annee"],
                "cam_val"         : s["cam_val"],
                "cam_annee"       : s["cam_annee"],
                "geometry"        : s["geometry"],
            })

        pct = f"{statut_pct:.0f}%" if statut_pct is not None else "N/A"
        print(f"[OK] {len(segs_rtss)} segs RTSS | "
              f"{len(segs_djma)} segs DJMA | qualité {pct} | "
              f"{longueur_trace_km:.1f}km")

    # ============================================================
    # EXPORT
    # ============================================================
    print(f"\n[3/4] Export vers {OUTPUT_FILE}...")

    gdf_arcs  = gpd.GeoDataFrame(rows_arcs,      crs=CRS_WORK)
    gdf_segs  = gpd.GeoDataFrame(rows_segs,       crs=CRS_WORK)
    gdf_trace = gpd.GeoDataFrame(rows_trace_osrm, crs=CRS_WORK)

    for gdf in [gdf_arcs, gdf_segs, gdf_trace]:
        if "fid" in gdf.columns:
            gdf.drop(columns=["fid"], inplace=True)

    gdf_arcs.to_file( OUTPUT_FILE, layer="arcs_enrichis_v2",   driver="GPKG")
    gdf_segs.to_file( OUTPUT_FILE, layer="trajets_segments_v2", driver="GPKG", mode="a")
    gdf_trace.to_file(OUTPUT_FILE, layer="trace_osrm_v2",       driver="GPKG", mode="a")

    print()
    print("=" * 65)
    print("RÉSUMÉ — VERSION 2")
    print("=" * 65)
    ok    = gdf_arcs[gdf_arcs["statut"] == "ok"]
    n_err = (gdf_arcs["statut"] != "ok").sum()
    print(f"Arcs traités         : {len(gdf_arcs)}")
    print(f"Arcs ok              : {len(ok)}")
    print(f"Arcs en échec        : {n_err}")
    if len(ok) > 0:
        print(f"\nLongueur tracés OSRM :")
        print(f"  Médiane : {ok['longueur_trace_km'].median():.1f} km")
        print(f"  Max     : {ok['longueur_trace_km'].max():.1f} km")
        print(f"\nQualité DJMA (arcs ok) :")
        print(f"  Médiane statut_djma_pct : {ok['statut_djma_pct'].median():.0f}%")
        print(f"  Arcs à 100%             : {(ok['statut_djma_pct'] == 100).sum()}")
        print(f"  Arcs < 50%              : {(ok['statut_djma_pct'] < 50).sum()}")
        print(f"  Médiane segs DJMA/arc   : {ok['n_segs_djma'].median():.0f}")
        print(f"  Max segs DJMA/arc       : {ok['n_segs_djma'].max():.0f}")
    print(f"\nSegments DJMA exportés : {len(gdf_segs)}")
    print(f"\nParamètres v2 utilisés :")
    print(f"  BUFFER_RECHERCHE_M = {BUFFER_RECHERCHE_M}m")
    print(f"  DIST_MAX_TRACE_M   = {DIST_MAX_TRACE_M}m")
    print(f"  ANGLE_MAX_DEG      = {ANGLE_MAX_DEG}°")
    print(f"  BUFFER_EXCLUSION_M = {BUFFER_EXCLUSION_M}m")
    print("\nTerminé — résultat dans data/processed/graphe_routier_v2_sample.gpkg")


if __name__ == "__main__":
    main()
