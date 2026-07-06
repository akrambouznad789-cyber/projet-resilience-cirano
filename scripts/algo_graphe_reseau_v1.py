"""
algo_graphe_reseau_v1.py
========================
Projet CIRANO II — Construction du graphe routier enrichi
VERSION 1 — Sample 30 premiers arcs

OBJECTIF
--------
Pour chaque arc du réseau simplifié ville-à-ville :
  1. Obtenir le tracé routier optimal via l'API OSRM.
  2. Clipper le corridor au polygone A-B pour éviter le débordement
     au-delà des noeuds.
  3. Identifier les segments RTSS et DJMA intersectant ce corridor clippé.
  4. Enrichir la couche d'arcs avec identifiants, valeurs et métadonnées.

AMÉLIORATIONS v2 (héritées)
----------------------------
- Suppression du champ 'fid' généré automatiquement par geopandas
- Clip du corridor au polygone orienté A-B
- Champ longueur_trace_km : longueur réelle du tracé OSRM (km)
- Exclusion des segments DJMA dont le centroïde est plus proche d'un
  autre noeud du réseau que du noeud A ou B de l'arc courant

SORTIES (3 couches dans le gpkg)
---------------------------------
arcs_enrichis
    ids_segs_rtss          IDs num_rts des segments RTSS (séparés par |)
    ids_segs_rtss_dist     Longueur de chaque segment RTSS en m (séparés par |)
    ids_segs_rtss_type     Type de chaque segment RTSS (séparés par |)
    ids_segs_djma          IDs ide_sectn_trafc des segments DJMA (séparés par |)
    ids_segs_djma_val      DJMA + année ex: 12500@2024 (séparés par |, NA si absent)
    ids_segs_djma_val_cam  %camions + année ex: 18.5@2024 (séparés par |, NA si absent)
    statut_djma_pct        % segments DJMA avec valeur de comptage réelle
    longueur_trace_km      Longueur du tracé OSRM en km
    statut                 ok | echec_osrm | erreur_api | aucun_djma | noeud_manquant

trajets_segments
    Un enregistrement par segment DJMA intersecté (validation QGIS via filtre ID_ARC).

trace_osrm
    Tracé brut OSRM par arc (vérification visuelle du routage).

PARAMÈTRES AJUSTABLES
---------------------
BUFFER_TRACE_M      : largeur buffer autour du tracé OSRM (défaut 500m)
BUFFER_EXCLUSION_M  : zone d'exclusion autour de chaque noeud (défaut 2000m)
SAMPLE_N_ARCS       : nombre d'arcs à traiter (None = tous, 30 = sample V1)
PILOT_ID_ARC        : tester sur un seul arc (None = tous)
PAUSE_API_S         : pause entre requêtes OSRM (défaut 0.5s)
"""

import os
import geopandas as gpd
import pandas as pd
import numpy as np
import requests
import time
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union
import warnings

warnings.filterwarnings("ignore")

# ============================================================
# CONFIGURATION
# ============================================================

PATH_ARCS   = os.path.expanduser("~/projects/projet-resilience-cirano/data/raw/reseau_arcs.gpkg")
PATH_RTSS   = os.path.expanduser("~/projects/projet-resilience-cirano/data/raw/ReseauRoutier_RTSS.gpkg")
PATH_DJMA   = os.path.expanduser("~/projects/projet-resilience-cirano/data/raw/DebitCirculation.gpkg")
OUTPUT_FILE = os.path.expanduser("~/projects/projet-resilience-cirano/data/processed/graphe_routier_v1_sample.gpkg")

LAYER_ARCS   = "arcs"
LAYER_NOEUDS = "noeuds"
LAYER_RTSS   = "bgr_v_sous_route_res_sup_act"
LAYER_DJMA   = "circulation_routier"

CRS_WORK = "EPSG:32198"
CRS_WGS  = "EPSG:4326"

BUFFER_TRACE_M     = 500    # largeur corridor autour du tracé OSRM
BUFFER_EXCLUSION_M = 2000   # zone autour de chaque noeud exclue du corridor
PAUSE_API_S        = 0.5    # pause entre requêtes OSRM
SAMPLE_N_ARCS      = 30     # None = tous les arcs | 30 = sample V1
PILOT_ID_ARC       = None   # ex: 50 pour Warwick→Victoriaville (prioritaire sur SAMPLE_N_ARCS)


# ============================================================
# CHARGEMENT
# ============================================================

def charger_djma(path, layer, crs):
    """
    Charge DebitCirculation.
    Extrait la valeur DJMA et camion la plus récente disponible par segment
    en parcourant les 10 colonnes annuelles (annee_1 à annee_10).
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


def charger_rtss(path, layer, crs):
    """Charge ReseauRoutier_RTSS."""
    print("  Chargement ReseauRoutier_RTSS...")
    rtss = gpd.read_file(path, layer=layer).to_crs(crs)
    rtss["seg_long_m"] = rtss.geometry.length
    print(f"  RTSS : {len(rtss)} segments chargés")
    return rtss


# ============================================================
# ROUTAGE OSRM
# ============================================================

def obtenir_trace_osrm(lon_a, lat_a, lon_b, lat_b):
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
        coords = data["routes"][0]["geometry"]["coordinates"]
        distance_m = data["routes"][0]["distance"]
        return LineString(coords), "ok", distance_m
    except Exception:
        return None, "erreur_api", 0


# ============================================================
# CONSTRUCTION DU CORRIDOR CLIPPÉ
# ============================================================

def construire_corridor(trace_lambert, pt_a_lambert, pt_b_lambert,
                         buffer_m, exclusion_m):
    """
    Construit le corridor d'intersection en deux étapes :

    1. Buffer autour du tracé OSRM (buffer_m).
    2. Clip par un polygone orienté A-B avec caps serrés aux extrémités
       pour éviter le débordement au-delà des noeuds.
    3. Si la distance A-B > 2 * exclusion_m, exclure un cercle autour
       de chaque noeud (segments purement locaux non pertinents).
       Sinon, désactiver l'exclusion (villes très proches).

    Retourne le polygone corridor final.
    """
    corridor_brut = trace_lambert.buffer(buffer_m)

    dx = pt_b_lambert.x - pt_a_lambert.x
    dy = pt_b_lambert.y - pt_a_lambert.y
    dist_ab = (dx**2 + dy**2) ** 0.5
    ux, uy  = -dy / dist_ab, dx / dist_ab
    ex, ey  = dx / dist_ab, dy / dist_ab
    marge   = buffer_m

    rect = Polygon([
        (pt_a_lambert.x - ex*marge + ux*buffer_m,
         pt_a_lambert.y - ey*marge + uy*buffer_m),
        (pt_b_lambert.x + ex*marge + ux*buffer_m,
         pt_b_lambert.y + ey*marge + uy*buffer_m),
        (pt_b_lambert.x + ex*marge - ux*buffer_m,
         pt_b_lambert.y + ey*marge - uy*buffer_m),
        (pt_a_lambert.x - ex*marge - ux*buffer_m,
         pt_a_lambert.y - ey*marge - uy*buffer_m),
    ])
    corridor_clipped = corridor_brut.intersection(rect)

    if dist_ab > 2 * exclusion_m:
        zone_exclue = unary_union([
            pt_a_lambert.buffer(exclusion_m),
            pt_b_lambert.buffer(exclusion_m),
        ])
        corridor_final = corridor_clipped.difference(zone_exclue)
        if corridor_final.is_empty:
            corridor_final = corridor_clipped
    else:
        corridor_final = corridor_clipped

    return corridor_final


# ============================================================
# EXTRACTION DES SEGMENTS
# ============================================================

def extraire_rtss(corridor, rtss_gdf):
    segs = rtss_gdf[rtss_gdf.geometry.intersects(corridor)].copy()
    if segs.empty:
        return []
    return [
        {"num_rts": row["num_rts"],
         "long_m" : round(row["seg_long_m"], 1),
         "type"   : row["des_clasf_"]}
        for _, row in segs.iterrows()
    ]


def extraire_djma(corridor, djma_gdf):
    segs = djma_gdf[djma_gdf.geometry.intersects(corridor)].copy()
    if segs.empty:
        return []
    return [
        {"ide_sectn_trafc": row["ide_sectn_trafc"],
         "djma_val"       : row["djma_val"],
         "djma_annee"     : row["djma_annee"],
         "cam_val"        : row["cam_val"],
         "cam_annee"      : row["cam_annee"],
         "longueur_m"     : row["longueur_m"],
         "geometry"       : row.geometry}
        for _, row in segs.iterrows()
    ]


def formater_liste(valeurs, sep="|"):
    return sep.join(str(v) for v in valeurs) if valeurs else None


def calculer_statut_djma_pct(segs_djma):
    if not segs_djma:
        return None
    n_avec = sum(1 for s in segs_djma if pd.notna(s["djma_val"]))
    return round(n_avec / len(segs_djma) * 100, 1)


# ============================================================
# PIPELINE PRINCIPAL
# ============================================================

def main():
    print("=" * 65)
    print("GRAPHE ROUTIER MTQ — Projet CIRANO II — VERSION 1 (sample)")
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

    # Sélection des arcs à traiter
    if PILOT_ID_ARC is not None:
        arcs_traiter = arcs[arcs["ID_ARC"] == PILOT_ID_ARC]
    elif SAMPLE_N_ARCS is not None:
        arcs_traiter = arcs.head(SAMPLE_N_ARCS)
    else:
        arcs_traiter = arcs

    print(f"\n[2/4] Routage OSRM + extraction segments ({len(arcs_traiter)} arcs)...")

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

        def echec(statut):
            base.update({
                "ids_segs_rtss"        : None,
                "ids_segs_rtss_dist"   : None,
                "ids_segs_rtss_type"   : None,
                "ids_segs_djma"        : None,
                "ids_segs_djma_val"    : None,
                "ids_segs_djma_val_cam": None,
                "statut_djma_pct"      : None,
                "longueur_trace_km"    : None,
                "statut"               : statut,
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

        result = obtenir_trace_osrm(pt_a_wgs.x, pt_a_wgs.y, pt_b_wgs.x, pt_b_wgs.y)
        time.sleep(PAUSE_API_S)

        trace_wgs, statut_osrm, distance_osrm_m = result
        if trace_wgs is None:
            print(f"[{statut_osrm}]")
            echec(statut_osrm)
            continue

        trace_lambert = gpd.GeoSeries([trace_wgs], crs=CRS_WGS).to_crs(CRS_WORK).iloc[0]
        longueur_trace_km = round(distance_osrm_m / 1000, 2)

        corridor = construire_corridor(
            trace_lambert, pt_a_lambert, pt_b_lambert,
            BUFFER_TRACE_M, BUFFER_EXCLUSION_M
        )

        rows_trace_osrm.append({
            "ID_ARC"           : arc_id,
            "VILLE_A"          : ville_a,
            "VILLE_B"          : ville_b,
            "longueur_trace_km": longueur_trace_km,
            "geometry"         : trace_lambert,
        })

        cand_rtss = list(rtss_sindex.intersection(corridor.bounds))
        segs_rtss = extraire_rtss(corridor, rtss.iloc[cand_rtss])

        cand_djma = list(djma_sindex.intersection(corridor.bounds))
        segs_djma = extraire_djma(corridor, djma.iloc[cand_djma])

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
                "statut"               : "aucun_djma",
            })
            rows_arcs.append(base.copy())
            continue

        ids_rtss   = formater_liste([s["num_rts"] for s in segs_rtss])
        dists_rtss = formater_liste([s["long_m"]  for s in segs_rtss])
        types_rtss = formater_liste([s["type"]    for s in segs_rtss])

        ids_djma = formater_liste([s["ide_sectn_trafc"] for s in segs_djma])

        djma_vals = []
        for s in segs_djma:
            if pd.notna(s["djma_val"]):
                annee = int(s["djma_annee"]) if pd.notna(s["djma_annee"]) else "?"
                djma_vals.append(f"{int(s['djma_val'])}@{annee}")
            else:
                djma_vals.append("NA")

        cam_vals = []
        for s in segs_djma:
            if pd.notna(s["cam_val"]):
                annee = int(s["cam_annee"]) if pd.notna(s["cam_annee"]) else "?"
                cam_vals.append(f"{s['cam_val']}@{annee}")
            else:
                cam_vals.append("NA")

        statut_pct = calculer_statut_djma_pct(segs_djma)

        base.update({
            "ids_segs_rtss"        : ids_rtss,
            "ids_segs_rtss_dist"   : dists_rtss,
            "ids_segs_rtss_type"   : types_rtss,
            "ids_segs_djma"        : ids_djma,
            "ids_segs_djma_val"    : formater_liste(djma_vals),
            "ids_segs_djma_val_cam": formater_liste(cam_vals),
            "statut_djma_pct"      : statut_pct,
            "longueur_trace_km"    : longueur_trace_km,
            "statut"               : "ok",
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

    gdf_arcs.to_file( OUTPUT_FILE, layer="arcs_enrichis",   driver="GPKG")
    gdf_segs.to_file( OUTPUT_FILE, layer="trajets_segments", driver="GPKG", mode="a")
    gdf_trace.to_file(OUTPUT_FILE, layer="trace_osrm",       driver="GPKG", mode="a")

    print()
    print("=" * 65)
    print("RÉSUMÉ — VERSION 1 SAMPLE")
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
    print(f"\nSegments DJMA exportés : {len(gdf_segs)}")
    print("\nTerminé — résultat dans data/processed/graphe_routier_v1_sample.gpkg")


if __name__ == "__main__":
    main()