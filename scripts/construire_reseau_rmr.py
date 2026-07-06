"""
construire_reseau_rmr.py
========================
Projet CIRANO II — Étape 1 : Construction du graphe RMR québécois

Remplace reseau_arcs.gpkg (graphe OSM ville-à-ville) par un graphe fondé sur
les Régions Métropolitaines de Recensement (RMR/AR) de Statistique Canada 2021.

LOGIQUE DES ARCS (Option B — connexions directes)
--------------------------------------------------
Deux RMR A et B sont reliées par un arc si le trajet OSRM entre leurs centroïdes
ne traverse PAS une troisième RMR (intersection > MIN_TRAVERSE_M mètres).
Avantage : le DJMA compté = seulement les segments hors zones urbaines → trafic
interurbain pur, sans pollution par le trafic local intra-RMR.

COMPATIBILITÉ AVAL
------------------
La structure de sortie reproduit exactement reseau_arcs.gpkg :
  couche arcs   : ID_ARC, ID_A, VILLE_A, ID_B, VILLE_B, DIST_KM, SOURCE, TYPE_A, TYPE_B
  couche noeuds : ID, NOM, TYPE, NB_ARCS, CAS, DETAIL
  couche rmr_zones : polygones RMR (RMRIDU, NOM, RMRGENRE) — pour clipping étape 2

ENTRÉES
-------
  data/raw/rmr/lrmr000b21a_f.shp  (Statistique Canada, RMR 2021)

SORTIE
------
  data/raw/reseau_rmr.gpkg
"""

import os
import time
from itertools import combinations

import geopandas as gpd
import pandas as pd
import requests
from shapely.geometry import LineString
import warnings

warnings.filterwarnings("ignore")


# ================================================================
# CONFIGURATION
# ================================================================

INPUT_RMR    = os.path.expanduser(
    "~/projects/projet-resilience-cirano/data/raw/rmr/lrmr000b21a_f.shp"
)
OUTPUT_FILE  = os.path.expanduser(
    "~/projects/projet-resilience-cirano/data/raw/reseau_rmr.gpkg"
)

CRS_WORK         = "EPSG:32198"
CRS_WGS          = "EPSG:4326"
PRUID_QC         = "24"
MIN_TRAVERSE_M   = 200    # longueur d'intersection min pour déclarer une RMR "traversée"
PAUSE_API_S      = 0.5    # pause entre requêtes OSRM (496 paires ≈ 4 min)

# Mapping RMRGENRE → TYPE compatible avec reseau_arcs.gpkg
GENRE_VERS_TYPE: dict[str, str] = {
    "B": "rmr",   # RMR (≥ 100 000 hab.)
    "K": "rmr",   # RMR à régime mixte
    "D": "ar",    # Agglomération de recensement (10 000–99 999 hab.)
}


# ================================================================
# OSRM
# ================================================================

def obtenir_trace_osrm(
    lon_a: float, lat_a: float, lon_b: float, lat_b: float
) -> tuple[LineString | None, str, float]:
    """
    Appelle l'API OSRM publique et retourne (tracé WGS84, statut, distance_m).
    """
    url = (
        f"http://router.project-osrm.org/route/v1/driving/"
        f"{lon_a},{lat_a};{lon_b},{lat_b}"
        f"?overview=full&geometries=geojson"
    )
    try:
        resp = requests.get(url, timeout=15)
        data = resp.json()
        if data.get("code") != "Ok":
            return None, "echec_osrm", 0.0
        coords     = data["routes"][0]["geometry"]["coordinates"]
        distance_m = data["routes"][0]["distance"]
        return LineString(coords), "ok", distance_m
    except Exception as exc:
        return None, f"erreur:{exc}", 0.0


# ================================================================
# PIPELINE PRINCIPAL
# ================================================================

def main() -> None:
    print("=" * 65)
    print("CONSTRUCTION RÉSEAU RMR — Québec 2021")
    print("=" * 65)

    # ── 1. Chargement & préparation ─────────────────────────────
    print(f"\n[1/4] Chargement {INPUT_RMR}...")
    gdf_raw = gpd.read_file(INPUT_RMR)
    gdf_qc  = gdf_raw[gdf_raw["PRIDU"] == PRUID_QC].copy().reset_index(drop=True)
    gdf_qc  = gdf_qc.to_crs(CRS_WORK)
    print(f"  {len(gdf_qc)} zones RMR/AR retenues (Québec, PRIDU='{PRUID_QC}')")

    # Colonnes dérivées
    gdf_qc["_id"]   = "RMR" + gdf_qc["RMRIDU"].astype(str)
    gdf_qc["_type"] = gdf_qc["RMRGENRE"].map(lambda g: GENRE_VERS_TYPE.get(g, "ar"))

    # Centroïdes 32198 (pour noeuds + tests spatiaux)
    gdf_qc["_centroid"] = gdf_qc.geometry.centroid

    # Centroïdes WGS84 (pour OSRM)
    gdf_cent_wgs = gpd.GeoDataFrame(
        geometry=gdf_qc["_centroid"], crs=CRS_WORK
    ).to_crs(CRS_WGS)
    gdf_qc["_lon"] = gdf_cent_wgs.geometry.x
    gdf_qc["_lat"] = gdf_cent_wgs.geometry.y

    # Index rapide par RMRIDU
    idx = gdf_qc.set_index("RMRIDU")

    # ── 2. Routage OSRM — 496 paires ────────────────────────────
    paires = list(combinations(gdf_qc["RMRIDU"].tolist(), 2))
    print(f"\n[2/4] Routage OSRM — {len(paires)} paires candidates (~{len(paires)*PAUSE_API_S/60:.1f} min)...")

    rows_arcs  = []
    arc_id     = 1
    n_direct   = 0
    n_traverse = 0
    n_echec    = 0

    for i, (rid_a, rid_b) in enumerate(paires, 1):
        a = idx.loc[rid_a]
        b = idx.loc[rid_b]

        tracé_wgs, statut, dist_m = obtenir_trace_osrm(
            a["_lon"], a["_lat"], b["_lon"], b["_lat"]
        )

        if tracé_wgs is None:
            n_echec += 1
            print(f"  [{i:3d}/{len(paires)}] {a['RMRNOM'][:18]:<18} → {b['RMRNOM'][:18]:<18} ✗ {statut}")
            time.sleep(PAUSE_API_S)
            continue

        # Reprojection en 32198 pour les tests spatiaux
        tracé_32198 = (
            gpd.GeoDataFrame(geometry=[tracé_wgs], crs=CRS_WGS)
            .to_crs(CRS_WORK)
            .geometry.iloc[0]
        )

        # Filtre "connexion directe" : la route ne doit pas traverser une RMR tierce
        autres   = gdf_qc[(gdf_qc["RMRIDU"] != rid_a) & (gdf_qc["RMRIDU"] != rid_b)]
        est_direct = True
        via_nom    = None

        for _, rmr_c in autres.iterrows():
            inter = tracé_32198.intersection(rmr_c.geometry)
            if inter.length > MIN_TRAVERSE_M:
                est_direct = False
                via_nom    = rmr_c["RMRNOM"]
                break

        if est_direct:
            rows_arcs.append({
                "ID_ARC"  : arc_id,
                "ID_A"    : a["_id"],
                "VILLE_A" : a["RMRNOM"],
                "ID_B"    : b["_id"],
                "VILLE_B" : b["RMRNOM"],
                "DIST_KM" : round(dist_m / 1000, 2),
                "SOURCE"  : "rmr",
                "TYPE_A"  : a["_type"],
                "TYPE_B"  : b["_type"],
                "geometry": tracé_32198,
            })
            arc_id  += 1
            n_direct += 1

        else:
            n_traverse += 1

        # Log toutes les 25 paires + les 5 premières
        if i % 25 == 0 or i <= 5:
            tag = "✓ direct" if est_direct else f"↷ via {(via_nom or '')[:20]}"
            print(f"  [{i:3d}/{len(paires)}] {a['RMRNOM'][:18]:<18} → {b['RMRNOM'][:18]:<18} {tag}")

        time.sleep(PAUSE_API_S)

    print(f"\n  Résultat : {n_direct} arcs directs | {n_traverse} via tiers | {n_echec} échecs OSRM")

    # ── 3. Construction des couches ──────────────────────────────
    print("\n[3/4] Construction des couches GeoDataFrame...")

    # Comptage des arcs par nœud
    nb_arcs: dict[str, int] = {}
    for row in rows_arcs:
        nb_arcs[row["ID_A"]] = nb_arcs.get(row["ID_A"], 0) + 1
        nb_arcs[row["ID_B"]] = nb_arcs.get(row["ID_B"], 0) + 1

    # Couche noeuds (centroïdes — compatible reseau_arcs.gpkg)
    rows_noeuds = [
        {
            "ID"      : row["_id"],
            "NOM"     : row["RMRNOM"],
            "TYPE"    : row["_type"],
            "NB_ARCS" : nb_arcs.get(row["_id"], 0),
            "CAS"     : None,
            "DETAIL"  : f"RMRIDU={row['RMRIDU']} · RMRGENRE={row['RMRGENRE']} · {row['SUPTERRE']:.1f} km²",
            "geometry": row["_centroid"],
        }
        for _, row in gdf_qc.iterrows()
    ]
    gdf_noeuds = gpd.GeoDataFrame(rows_noeuds, geometry="geometry", crs=CRS_WORK)

    # Couche arcs
    gdf_arcs = (
        gpd.GeoDataFrame(rows_arcs, geometry="geometry", crs=CRS_WORK)
        if rows_arcs
        else gpd.GeoDataFrame(
            columns=["ID_ARC","ID_A","VILLE_A","ID_B","VILLE_B",
                     "DIST_KM","SOURCE","TYPE_A","TYPE_B","geometry"]
        )
    )

    # Couche rmr_zones (polygones — pour clipping étape 2)
    gdf_zones = gpd.GeoDataFrame(
        {
            "RMRIDU"   : gdf_qc["RMRIDU"].values,
            "NOM"      : gdf_qc["RMRNOM"].values,
            "RMRGENRE" : gdf_qc["RMRGENRE"].values,
            "SUPTERRE" : gdf_qc["SUPTERRE"].values,
            "geometry" : gdf_qc["geometry"].values,
        },
        crs=CRS_WORK,
    )

    # ── 4. Export ────────────────────────────────────────────────
    print(f"\n[4/4] Export → {OUTPUT_FILE}")
    gdf_arcs.to_file(  OUTPUT_FILE, layer="arcs",      driver="GPKG")
    gdf_noeuds.to_file(OUTPUT_FILE, layer="noeuds",    driver="GPKG")
    gdf_zones.to_file( OUTPUT_FILE, layer="rmr_zones", driver="GPKG")

    print(f"\n  couche arcs      : {len(gdf_arcs)} arcs directs inter-RMR")
    print(f"  couche noeuds    : {len(gdf_noeuds)} nœuds (centroïdes RMR/AR)")
    print(f"  couche rmr_zones : {len(gdf_zones)} polygones RMR/AR")
    print("\n  Terminé.")

    # Résumé rapide
    if not gdf_arcs.empty:
        print(f"\n  Distance médiane  : {gdf_arcs['DIST_KM'].median():.0f} km")
        print(f"  Distance min/max  : {gdf_arcs['DIST_KM'].min():.0f} / {gdf_arcs['DIST_KM'].max():.0f} km")
        print(f"  Arcs RMR↔RMR     : {((gdf_arcs['TYPE_A']=='rmr') & (gdf_arcs['TYPE_B']=='rmr')).sum()}")
        print(f"  Arcs RMR↔AR      : {((gdf_arcs['TYPE_A']!=gdf_arcs['TYPE_B'])).sum()}")
        print(f"  Arcs AR↔AR       : {((gdf_arcs['TYPE_A']=='ar') & (gdf_arcs['TYPE_B']=='ar')).sum()}")


if __name__ == "__main__":
    main()
