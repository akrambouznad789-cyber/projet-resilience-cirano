"""
algo_jointure_routes_liens.py
==============================
Projet CIRANO II — Construction du graphe routier enrichi (jointure routage ↔ arcs)

DIFFÉRENCES PAR RAPPORT À V2
------------------------------
  1. geometry.distance() au lieu de centroid.distance() au filtre 1
     → capture les segments longs dont le centroïde est décalé du tracé

  2. Filtre 4 (nouveau) — exclusion intraurbaine A et B
     → exclut les segments à < 3km du centroïde de A ou B
     → élimine le trafic de distribution locale près des villes d'origine/dest

  3. Clip Québec (nouveau) — masque RTSS 2km
     → clip le tracé OSRM aux portions couvertes par le réseau RTSS
     → supprime les détours par d'autres provinces avant la recherche DJMA
     → résultat peut être MultiLineString (route sort et rentre au QC)

  4. Source DJMA → debits_completes.gpkg
     → toutes les valeurs garanties renseignées (annee_1 toujours valide)

  5. angle_local_trace() gère MultiLineString en entrée

PARAMÈTRES AJUSTABLES
---------------------
BUFFER_QC_RTSS_M    : buffer RTSS → masque territoire québécois (défaut 2000m)
BUFFER_RECHERCHE_M  : zone de candidats autour du tracé (défaut 1500m)
DIST_MAX_TRACE_M    : distance max segment→tracé (défaut 400m)
ANGLE_MAX_DEG       : écart angulaire maximal toléré (défaut 45°)
BUFFER_EXCLUSION_M  : zone d'exclusion autour de chaque nœud tiers (défaut 2000m)
BUFFER_NOEUDS_AB_M  : exclusion intraurbaine autour de A et B (défaut 3000m)
SAMPLE_N_ARCS       : nombre d'arcs à traiter (None = tous les 307)
PILOT_ID_ARC        : tester un seul arc (prioritaire sur SAMPLE_N_ARCS)
PAUSE_API_S         : pause entre requêtes OSRM (défaut 0.5s)

SORTIES (3 couches dans le gpkg)
---------------------------------
arcs_enrichis     Arcs avec segments DJMA et métadonnées v4
trajets_segments  Un enregistrement par segment DJMA retenu
trace_osrm        Tracé OSRM complet A→B
"""

from pathlib import Path
import math
import geopandas as gpd
import pandas as pd
import numpy as np
import requests
import time
from shapely.geometry import LineString, Point, MultiLineString
from shapely.ops import unary_union
import warnings

warnings.filterwarnings("ignore")


# ============================================================
# CONFIGURATION
# ============================================================

RACINE      = Path(__file__).resolve().parent.parent
PATH_ARCS   = RACINE / "data" / "raw" / "reseau_arcs.gpkg"
PATH_RTSS   = RACINE / "data" / "raw" / "ReseauRoutier_RTSS.gpkg"
PATH_DJMA   = RACINE / "data" / "processed" / "debits_completes.gpkg"
OUTPUT_FILE = RACINE / "data" / "processed" / "graphe_routier.gpkg"

LAYER_ARCS   = "arcs"
LAYER_NOEUDS = "noeuds"
LAYER_RTSS   = "bgr_v_sous_route_res_sup_act"
LAYER_DJMA   = "debits_completes"

CRS_WORK = "EPSG:32198"
CRS_WGS  = "EPSG:4326"

BUFFER_QC_RTSS_M    = 2000
BUFFER_RECHERCHE_M  = 1500
DIST_MAX_TRACE_M    = 400
ANGLE_MAX_DEG       = 45
BUFFER_EXCLUSION_M  = 2000
BUFFER_NOEUDS_AB_M  = 3000
PAUSE_API_S         = 0.5
SAMPLE_N_ARCS       = None  # None = tous les 307 arcs
PILOT_ID_ARC        = None  # ex: 50 pour tester un seul arc
SAMPLE_IDS          = None  # None = pas de filtre par IDs


# ============================================================
# CHARGEMENT
# ============================================================

def charger_djma(path: str, layer: str, crs: str) -> gpd.GeoDataFrame:
    """Charge debits_completes — annee_1 toujours renseigné après complétion."""
    print("  Chargement debits_completes...")
    djma = gpd.read_file(path, layer=layer).to_crs(crs)
    djma["longueur_m"] = djma.geometry.length
    djma["djma_val"]   = pd.to_numeric(djma["val_djma_annee_1"], errors="coerce")
    djma["djma_annee"] = pd.to_numeric(djma["djma_annee_1"],     errors="coerce")
    djma["cam_val"]    = pd.to_numeric(djma["val_cam_annee_1"],  errors="coerce")
    djma["cam_annee"]  = pd.to_numeric(djma["cam_annee_1"],      errors="coerce")
    n_avec = djma["djma_val"].notna().sum()
    print(f"  DJMA : {n_avec}/{len(djma)} segments avec valeur (annee_1)")
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
    """Appelle l'API OSRM publique. Retourne (LineString WGS84, statut, distance_m)."""
    url = (f"http://router.project-osrm.org/route/v1/driving/"
           f"{lon_a},{lat_a};{lon_b},{lat_b}"
           f"?overview=full&geometries=geojson")
    try:
        resp = requests.get(url, timeout=10)
        data = resp.json()
        if data.get("code") != "Ok":
            return None, "echec_osrm", 0
        coords     = data["routes"][0]["geometry"]["coordinates"]
        distance_m = data["routes"][0]["distance"]
        return LineString(coords), "ok", distance_m
    except Exception:
        return None, "erreur_api", 0


# ============================================================
# UTILITAIRES GÉOMÉTRIQUES
# ============================================================

def angle_linestring(geom) -> float:
    if geom.geom_type == "MultiLineString":
        geom = max(geom.geoms, key=lambda g: g.length)
    coords = list(geom.coords)
    dx = coords[-1][0] - coords[0][0]
    dy = coords[-1][1] - coords[0][1]
    return math.degrees(math.atan2(dy, dx)) % 180


def angle_local_trace(trace, pt: Point) -> float:
    """Angle local du tracé au point le plus proche de pt. Gère MultiLineString."""
    if trace.geom_type == "MultiLineString":
        trace = min(trace.geoms, key=lambda g: g.distance(pt))
    coords   = list(trace.coords)
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
    diff = abs(a1 - a2) % 180
    return min(diff, 180 - diff)


def longueur_geom(geom) -> float:
    if geom is None or geom.is_empty:
        return 0.0
    if geom.geom_type == "MultiLineString":
        return sum(g.length for g in geom.geoms)
    return geom.length


# ============================================================
# CLIP QUÉBEC — NOUVEAUTÉ V4
# ============================================================

def clipper_trace_a_quebec(trace, rtss_gdf: gpd.GeoDataFrame, rtss_sindex):
    """
    Clip le tracé OSRM aux portions couvertes par le réseau RTSS québécois.
    Supprime les détours par d'autres provinces.
    Retourne None si le résultat est vide.
    """
    zone_recherche = trace.buffer(5000)
    cands = list(rtss_sindex.intersection(zone_recherche.bounds))
    if not cands:
        return None

    rtss_proches = rtss_gdf.iloc[cands]
    rtss_proches = rtss_proches[rtss_proches.geometry.intersects(zone_recherche)]
    if rtss_proches.empty:
        return None

    masque_qc = rtss_proches.geometry.unary_union.buffer(BUFFER_QC_RTSS_M)
    trace_qc  = trace.intersection(masque_qc)

    if trace_qc.is_empty:
        return None

    if trace_qc.geom_type == "GeometryCollection":
        parties = [
            g for g in trace_qc.geoms
            if g.geom_type in ("LineString", "MultiLineString") and g.length > 100
        ]
        if not parties:
            return None
        trace_qc = unary_union(parties)

    return trace_qc


# ============================================================
# FILTRES
# ============================================================

def filtre_distance_trace(segs: gpd.GeoDataFrame,
                           trace, dist_max_m: float) -> gpd.GeoDataFrame:
    """Filtre 1 — distance segment complet → tracé (pas du centroïde)."""
    if segs.empty:
        return segs
    distances = segs.geometry.distance(trace)
    return segs[distances <= dist_max_m].copy()


def filtre_direction(segs: gpd.GeoDataFrame,
                     trace, angle_max_deg: float) -> gpd.GeoDataFrame:
    """Filtre 2 — alignement directionnel segment vs tracé."""
    if segs.empty:
        return segs
    masque = []
    for _, row in segs.iterrows():
        centroid  = row.geometry.centroid
        angle_seg = angle_linestring(row.geometry)
        angle_tr  = angle_local_trace(trace, centroid)
        masque.append(diff_angle(angle_seg, angle_tr) <= angle_max_deg)
    return segs[masque].copy()


def filtre_noeud_proximite(segs: gpd.GeoDataFrame,
                            pt_a: Point, pt_b: Point,
                            tous_noeuds: gpd.GeoDataFrame,
                            exclusion_m: float) -> gpd.GeoDataFrame:
    """Filtre 3 — exclut les segments plus proches d'un nœud tiers que de A ou B."""
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
        masque.append(not (dist_tiers < dist_ab_min and dist_tiers < exclusion_m))
    return segs[masque].copy()


def filtre_proximite_ab(segs: gpd.GeoDataFrame,
                         pt_a: Point, pt_b: Point,
                         buffer_ab_m: float) -> gpd.GeoDataFrame:
    """Filtre 4 — exclut les segments trop proches de A ou B (trafic intraurbain)."""
    if segs.empty:
        return segs
    if pt_a.distance(pt_b) < 2 * buffer_ab_m:
        return segs
    masque = []
    for _, row in segs.iterrows():
        centroid = row.geometry.centroid
        trop_proche = (
            centroid.distance(pt_a) < buffer_ab_m
            or centroid.distance(pt_b) < buffer_ab_m
        )
        masque.append(not trop_proche)
    return segs[masque].copy()


# ============================================================
# EXTRACTION DES SEGMENTS
# ============================================================

def extraire_rtss(zone_recherche, rtss_gdf: gpd.GeoDataFrame, rtss_sindex) -> list:
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


def extraire_djma_v4(trace,
                      djma_gdf: gpd.GeoDataFrame,
                      djma_sindex,
                      pt_a: Point, pt_b: Point) -> list:
    """3 filtres séquentiels sur le tracé clippé au Québec."""
    zone_recherche = trace.buffer(BUFFER_RECHERCHE_M)
    cands = list(djma_sindex.intersection(zone_recherche.bounds))
    if not cands:
        return []

    candidats = djma_gdf.iloc[cands][
        djma_gdf.iloc[cands].geometry.intersects(zone_recherche)
    ].copy()

    candidats = filtre_distance_trace(candidats, trace, DIST_MAX_TRACE_M)
    candidats = filtre_direction(candidats, trace, ANGLE_MAX_DEG)
    candidats = filtre_proximite_ab(candidats, pt_a, pt_b, BUFFER_NOEUDS_AB_M)

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
    print("GRAPHE ROUTIER MTQ — Projet CIRANO II — VERSION 4")
    print("Réseau OSM · clip QC · geometry.distance · buffer AB")
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
    elif SAMPLE_IDS is not None:
        arcs_traiter = arcs[arcs["ID_ARC"].isin(SAMPLE_IDS)]
    elif SAMPLE_N_ARCS is not None:
        arcs_traiter = arcs.head(SAMPLE_N_ARCS)
    else:
        arcs_traiter = arcs

    print(f"\n[2/4] Routage OSRM + extraction v4 ({len(arcs_traiter)} arcs)...")

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
                "ids_segs_rtss_id"     : None,
                "ids_segs_rtss_dist"   : None,
                "ids_segs_rtss_type"   : None,
                "ids_segs_djma_id"     : None,
                "ids_segs_djma_val"    : None,
                "ids_segs_djma_val_cam": None,
                "statut_djma_pct"      : None,
                "longueur_trace_km"    : None,
                "longueur_qc_km"       : None,
                "n_segs_djma"          : 0,
                "statut"               : statut,
                "methode"              : "v4",
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

        # ── CLIP QUÉBEC ──────────────────────────────────────────
        trace_qc = clipper_trace_a_quebec(trace_lambert, rtss, rtss_sindex)
        if trace_qc is None:
            print("[hors_quebec]")
            echec("hors_quebec")
            continue

        longueur_qc_km = round(longueur_geom(trace_qc) / 1000, 2)

        # ── EXTRACTION RTSS & DJMA ───────────────────────────────
        zone_recherche = trace_qc.buffer(BUFFER_RECHERCHE_M)
        segs_rtss = extraire_rtss(zone_recherche, rtss, rtss_sindex)

        segs_djma = extraire_djma_v4(
            trace_qc, djma, djma_sindex,
            pt_a_lambert, pt_b_lambert
        )

        if not segs_djma:
            print("[aucun_djma]")
            base.update({
                "ids_segs_rtss_id"     : formater_liste([s["num_rts"] for s in segs_rtss]),
                "ids_segs_rtss_dist"   : formater_liste([s["long_m"]  for s in segs_rtss]),
                "ids_segs_rtss_type"   : formater_liste([s["type"]    for s in segs_rtss]),
                "ids_segs_djma_id"     : None,
                "ids_segs_djma_val"    : None,
                "ids_segs_djma_val_cam": None,
                "statut_djma_pct"      : 0.0,
                "longueur_trace_km"    : longueur_trace_km,
                "longueur_qc_km"       : longueur_qc_km,
                "n_segs_djma"          : 0,
                "statut"               : "aucun_djma",
                "methode"              : "v4",
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
            "ids_segs_rtss_id"     : formater_liste([s["num_rts"] for s in segs_rtss]),
            "ids_segs_rtss_dist"   : formater_liste([s["long_m"]  for s in segs_rtss]),
            "ids_segs_rtss_type"   : formater_liste([s["type"]    for s in segs_rtss]),
            "ids_segs_djma_id"     : formater_liste([s["ide_sectn_trafc"] for s in segs_djma]),
            "ids_segs_djma_val"    : formater_liste(djma_vals),
            "ids_segs_djma_val_cam": formater_liste(cam_vals),
            "statut_djma_pct"      : statut_pct,
            "longueur_trace_km"    : longueur_trace_km,
            "longueur_qc_km"       : longueur_qc_km,
            "n_segs_djma"          : len(segs_djma),
            "statut"               : "ok",
            "methode"              : "v4",
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
        print(f"[OK] {len(segs_rtss)} RTSS | {len(segs_djma)} DJMA | "
              f"qualité {pct} | {longueur_trace_km:.1f}km → QC {longueur_qc_km:.1f}km")

    # ── EXPORT ──────────────────────────────────────────────────
    print(f"\n[3/4] Export → {OUTPUT_FILE}")

    gdf_arcs  = gpd.GeoDataFrame(rows_arcs,      crs=CRS_WORK)
    gdf_segs  = gpd.GeoDataFrame(rows_segs,       crs=CRS_WORK)
    gdf_trace = gpd.GeoDataFrame(rows_trace_osrm, crs=CRS_WORK)

    for gdf in [gdf_arcs, gdf_segs, gdf_trace]:
        if "fid" in gdf.columns:
            gdf.drop(columns=["fid"], inplace=True)

    cols_arcs = ["ID_ARC"] + [c for c in gdf_arcs.columns if c not in ("ID_ARC", "geometry")] + ["geometry"]
    gdf_arcs  = gdf_arcs[cols_arcs]

    gdf_arcs.to_file( OUTPUT_FILE, layer="arcs_enrichis",   driver="GPKG")
    gdf_segs.to_file( OUTPUT_FILE, layer="trajets_segments", driver="GPKG", mode="a")
    gdf_trace.to_file(OUTPUT_FILE, layer="trace_osrm",       driver="GPKG", mode="a")

    # ── RÉSUMÉ ──────────────────────────────────────────────────
    print()
    print("=" * 65)
    print("RÉSUMÉ — VERSION 4")
    print("=" * 65)
    ok    = gdf_arcs[gdf_arcs["statut"] == "ok"]
    n_err = (gdf_arcs["statut"] != "ok").sum()

    print(f"Arcs traités         : {len(gdf_arcs)}")
    print(f"Arcs ok              : {len(ok)}")
    print(f"Arcs en échec/exclus : {n_err}")

    if n_err > 0:
        print("\nDétail des échecs :")
        for statut, grp in gdf_arcs[gdf_arcs["statut"] != "ok"].groupby("statut"):
            print(f"  {statut:<25} : {len(grp)} arc(s)")

    if len(ok) > 0:
        print(f"\nLongueurs :")
        print(f"  Tracé OSRM médiane  : {ok['longueur_trace_km'].median():.1f} km")
        print(f"  Portion QC médiane  : {ok['longueur_qc_km'].median():.1f} km")
        red = (1 - ok['longueur_qc_km'].mean() / ok['longueur_trace_km'].mean()) * 100
        print(f"  Exclusion hors-QC   : {red:.1f}% du tracé moyen")
        print(f"\nQualité DJMA (arcs ok) :")
        print(f"  Médiane statut_djma_pct : {ok['statut_djma_pct'].median():.0f}%")
        print(f"  Arcs à 100%             : {(ok['statut_djma_pct'] == 100).sum()}")
        print(f"  Arcs < 50%              : {(ok['statut_djma_pct'] < 50).sum()}")
        print(f"  Médiane segs DJMA/arc   : {ok['n_segs_djma'].median():.0f}")

    print(f"\nSegments DJMA exportés : {len(gdf_segs)}")
    print(f"\nParamètres v4 utilisés :")
    print(f"  BUFFER_QC_RTSS_M    = {BUFFER_QC_RTSS_M}m")
    print(f"  BUFFER_RECHERCHE_M  = {BUFFER_RECHERCHE_M}m")
    print(f"  DIST_MAX_TRACE_M    = {DIST_MAX_TRACE_M}m")
    print(f"  BUFFER_NOEUDS_AB_M  = {BUFFER_NOEUDS_AB_M}m")
    print(f"  ANGLE_MAX_DEG       = {ANGLE_MAX_DEG}°")
    print(f"  BUFFER_EXCLUSION_M  = {BUFFER_EXCLUSION_M}m")
    print(f"  SAMPLE_N_ARCS       = {SAMPLE_N_ARCS}")
    print(f"\nTerminé — résultat dans {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
