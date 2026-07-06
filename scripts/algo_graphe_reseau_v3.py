"""
algo_graphe_reseau_v3.py
========================
Projet CIRANO II — Construction du graphe routier enrichi
VERSION 3 — Nœuds RMR (Régions Métropolitaines de Recensement)

DIFFÉRENCES PAR RAPPORT À V2
------------------------------
V2  : nœuds = centroïdes de villes OSM (reseau_arcs.gpkg)
      → les segments RTSS capturés incluent le trafic intra-urbain local

V3  : nœuds = zones RMR/AR (reseau_rmr.gpkg, Statistique Canada 2021)
      → CLIPPING : le tracé OSRM est tronqué à la portion hors des deux
        polygones RMR avant tout filtrage. Seuls les segments RTSS sur ce
        corridor interurbain sont retenus → DJMA camion exempt de trafic local.

AVANTAGES DU CLIPPING RMR
--------------------------
  - Le débit poids-lourd inter-RMR est isolé du trafic de distribution urbaine.
  - Le nombre d'arcs est fixe et défendable (74 liaisons directes QC).
  - Les itinéraires empruntent naturellement les routes principales.

TRACES OSRM
-----------
  Les tracés OSRM sont déjà stockés dans reseau_rmr.gpkg (couche arcs) depuis
  l'étape construire_reseau_rmr.py. Aucun appel API n'est nécessaire ici.

PARAMÈTRES AJUSTABLES
---------------------
BUFFER_RECHERCHE_M   : zone de candidats autour du tracé interurbain (défaut 1500m)
DIST_MAX_TRACE_M     : distance max centroïde→tracé interurbain (défaut 400m)
ANGLE_MAX_DEG        : écart angulaire maximal toléré (défaut 45°)
BUFFER_EXCLUSION_M   : zone d'exclusion autour de chaque nœud (défaut 2000m)
SAMPLE_N_ARCS        : nombre d'arcs à traiter (None = tous les 74)
PILOT_ID_ARC         : tester un seul arc (prioritaire sur SAMPLE_N_ARCS)

SORTIES (4 couches dans le gpkg)
---------------------------------
arcs_enrichis_v3      Arcs avec segments DJMA et métadonnées v3
trajets_segments_v3   Un enregistrement par segment DJMA retenu
trace_osrm_v3         Tracé OSRM complet A→B (validation visuelle)
trace_interurbain_v3  Tracé clipé hors polygones RMR A et B (corridor DJMA effectif)
"""

import os
import math
import geopandas as gpd
import pandas as pd
import numpy as np
import time
from shapely.geometry import LineString, Point
from shapely.ops import unary_union
import warnings

warnings.filterwarnings("ignore")


# ============================================================
# CONFIGURATION
# ============================================================

PATH_RMR    = os.path.expanduser("~/projects/projet-resilience-cirano/data/raw/reseau_rmr.gpkg")
PATH_RTSS   = os.path.expanduser("~/projects/projet-resilience-cirano/data/raw/ReseauRoutier_RTSS.gpkg")
PATH_DJMA   = os.path.expanduser("~/projects/projet-resilience-cirano/data/raw/DebitCirculation.gpkg")
OUTPUT_FILE = os.path.expanduser("~/projects/projet-resilience-cirano/data/processed/graphe_routier_v3_sample.gpkg")

LAYER_ARCS    = "arcs"
LAYER_NOEUDS  = "noeuds"
LAYER_ZONES   = "rmr_zones"
LAYER_RTSS    = "bgr_v_sous_route_res_sup_act"
LAYER_DJMA    = "circulation_routier"

CRS_WORK = "EPSG:32198"
CRS_WGS  = "EPSG:4326"

BUFFER_RECHERCHE_M = 1500
DIST_MAX_TRACE_M   = 400
ANGLE_MAX_DEG      = 45
BUFFER_EXCLUSION_M = 2000
SAMPLE_N_ARCS      = 10    # None = tous les 74 arcs RMR
PILOT_ID_ARC       = None  # ex: 7 pour tester un seul arc


# ============================================================
# CHARGEMENT DJMA
# ============================================================

def charger_djma(path: str, layer: str, crs: str) -> gpd.GeoDataFrame:
    """Charge DebitCirculation et extrait le DJMA le plus récent par segment."""
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
# UTILITAIRES GÉOMÉTRIQUES (identiques à v2)
# ============================================================

def angle_linestring(geom) -> float:
    if geom.geom_type == "MultiLineString":
        geom = max(geom.geoms, key=lambda g: g.length)
    coords = list(geom.coords)
    dx = coords[-1][0] - coords[0][0]
    dy = coords[-1][1] - coords[0][1]
    return math.degrees(math.atan2(dy, dx)) % 180


def angle_local_trace(trace: LineString, pt: Point) -> float:
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
    """Longueur totale d'une géométrie (LineString ou MultiLineString)."""
    if geom is None or geom.is_empty:
        return 0.0
    if geom.geom_type == "MultiLineString":
        return sum(g.length for g in geom.geoms)
    return geom.length


# ============================================================
# CLIPPING RMR — NOUVEAUTÉ V3
# ============================================================

def clipper_trace_interurbain(
    trace: LineString,
    zone_a,
    zone_b,
) -> LineString | None:
    """
    Coupe le tracé OSRM pour ne garder que la portion hors des zones RMR A et B.
    C'est le corridor interurbain pur : entre la sortie de RMR_A et l'entrée de RMR_B.

    Retourne None si le tracé résultant est vide ou trop court (< 500m).
    """
    zones_union = unary_union([zone_a, zone_b])
    interurbain = trace.difference(zones_union)

    if interurbain.is_empty:
        return None

    # Si MultiLineString, garder uniquement la plus longue partie
    if interurbain.geom_type == "MultiLineString":
        interurbain = max(interurbain.geoms, key=lambda g: g.length)

    if interurbain.length < 500:
        return None

    return interurbain


# ============================================================
# FILTRES V2 (inchangés, appliqués sur le tracé interurbain)
# ============================================================

def filtre_distance_trace(segs: gpd.GeoDataFrame,
                           trace, dist_max_m: float) -> gpd.GeoDataFrame:
    if segs.empty:
        return segs
    distances = segs.geometry.centroid.distance(trace)
    return segs[distances <= dist_max_m].copy()


def filtre_direction(segs: gpd.GeoDataFrame,
                     trace, angle_max_deg: float) -> gpd.GeoDataFrame:
    if segs.empty:
        return segs
    masque = []
    for _, row in segs.iterrows():
        centroid   = row.geometry.centroid
        angle_seg  = angle_linestring(row.geometry)
        angle_tr   = angle_local_trace(trace, centroid)
        masque.append(diff_angle(angle_seg, angle_tr) <= angle_max_deg)
    return segs[masque].copy()


def filtre_noeud_proximite(segs: gpd.GeoDataFrame,
                            pt_a: Point, pt_b: Point,
                            tous_noeuds: gpd.GeoDataFrame,
                            exclusion_m: float) -> gpd.GeoDataFrame:
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


def extraire_djma_v3(trace_interurbain,
                      djma_gdf: gpd.GeoDataFrame,
                      djma_sindex,
                      pt_a: Point, pt_b: Point,
                      tous_noeuds: gpd.GeoDataFrame) -> list:
    """
    Extraction des segments DJMA — v3.
    Les trois filtres de v2 sont appliqués sur le tracé INTERURBAIN (hors polygones RMR).
    Cela exclut automatiquement les segments de trafic local intra-RMR.
    """
    zone_recherche = trace_interurbain.buffer(BUFFER_RECHERCHE_M)
    cands = list(djma_sindex.intersection(zone_recherche.bounds))
    if not cands:
        return []

    candidats = djma_gdf.iloc[cands][
        djma_gdf.iloc[cands].geometry.intersects(zone_recherche)
    ].copy()

    candidats = filtre_distance_trace(candidats, trace_interurbain, DIST_MAX_TRACE_M)
    candidats = filtre_direction(candidats, trace_interurbain, ANGLE_MAX_DEG)
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

def main() -> None:
    print("=" * 65)
    print("GRAPHE ROUTIER MTQ — Projet CIRANO II — VERSION 3")
    print("Nœuds RMR + clipping corridor interurbain")
    print("=" * 65)

    # ── 1. Chargement ───────────────────────────────────────────
    print("\n[1/4] Chargement des données...")

    arcs   = gpd.read_file(PATH_RMR, layer=LAYER_ARCS)
    noeuds = gpd.read_file(PATH_RMR, layer=LAYER_NOEUDS)
    zones  = gpd.read_file(PATH_RMR, layer=LAYER_ZONES)

    # reseau_rmr.gpkg est en 32198 avec CRS explicite
    arcs   = arcs.to_crs(CRS_WORK)
    noeuds = noeuds.to_crs(CRS_WORK)
    zones  = zones.to_crs(CRS_WORK)

    # Index zones par RMRIDU (string) pour lookup rapide
    zones["RMRIDU"] = zones["RMRIDU"].astype(str)
    zones_idx = zones.set_index("RMRIDU")

    rtss = charger_rtss(PATH_RTSS, LAYER_RTSS, CRS_WORK)
    djma = charger_djma(PATH_DJMA, LAYER_DJMA, CRS_WORK)

    rtss_sindex = rtss.sindex
    djma_sindex = djma.sindex

    # Index noeuds par ID (ex: "RMR462")
    noeuds_lambert = noeuds.set_index("ID")

    print(f"  {len(arcs)} arcs RMR chargés | {len(noeuds)} nœuds | {len(zones)} zones")

    # ── 2. Sélection des arcs à traiter ─────────────────────────
    if PILOT_ID_ARC is not None:
        arcs_traiter = arcs[arcs["ID_ARC"] == PILOT_ID_ARC]
    elif SAMPLE_N_ARCS is not None:
        arcs_traiter = arcs.head(SAMPLE_N_ARCS)
    else:
        arcs_traiter = arcs

    print(f"\n[2/4] Traitement v3 — {len(arcs_traiter)} arcs RMR...")

    rows_arcs        = []
    rows_segs        = []
    rows_trace_osrm  = []
    rows_trace_inter = []

    CHAMPS_EXCLUS = {"geometry", "fid"}

    for _, arc in arcs_traiter.iterrows():
        arc_id  = arc["ID_ARC"]
        id_a    = arc["ID_A"]     # ex: "RMR462"
        id_b    = arc["ID_B"]     # ex: "RMR421"
        ville_a = arc["VILLE_A"]
        ville_b = arc["VILLE_B"]

        print(f"  Arc {arc_id:3d} | {str(ville_a)[:20]:<20} → {str(ville_b)[:20]:<20}", end=" ")

        base = {k: arc[k] for k in arc.index if k not in CHAMPS_EXCLUS}
        base["geometry"] = arc.geometry

        def echec(statut: str) -> None:
            base.update({
                "ids_segs_rtss"          : None,
                "ids_segs_rtss_dist"     : None,
                "ids_segs_rtss_type"     : None,
                "ids_segs_djma"          : None,
                "ids_segs_djma_val"      : None,
                "ids_segs_djma_val_cam"  : None,
                "statut_djma_pct"        : None,
                "longueur_trace_km"      : None,
                "longueur_interurbain_km": None,
                "n_segs_djma"            : 0,
                "statut"                 : statut,
                "methode"                : "v3",
            })
            rows_arcs.append(base.copy())

        # Nœuds Lambert
        if id_a not in noeuds_lambert.index or id_b not in noeuds_lambert.index:
            print("[noeud_manquant]")
            echec("noeud_manquant")
            continue

        pt_a_lambert = noeuds_lambert.loc[id_a, "geometry"]
        pt_b_lambert = noeuds_lambert.loc[id_b, "geometry"]

        # Trace OSRM (déjà stockée dans l'arc, pas d'appel API)
        trace_lambert     = arc.geometry
        longueur_trace_km = round(longueur_geom(trace_lambert) / 1000, 2)

        rows_trace_osrm.append({
            "ID_ARC"           : arc_id,
            "VILLE_A"          : ville_a,
            "VILLE_B"          : ville_b,
            "longueur_trace_km": longueur_trace_km,
            "geometry"         : trace_lambert,
        })

        # ── CLIPPING RMR — NOUVEAUTÉ V3 ─────────────────────────
        rmridu_a = id_a.replace("RMR", "")
        rmridu_b = id_b.replace("RMR", "")

        if rmridu_a not in zones_idx.index or rmridu_b not in zones_idx.index:
            print("[zone_rmr_manquante]")
            echec("zone_rmr_manquante")
            continue

        zone_a = zones_idx.loc[rmridu_a, "geometry"]
        zone_b = zones_idx.loc[rmridu_b, "geometry"]

        trace_interurbain = clipper_trace_interurbain(trace_lambert, zone_a, zone_b)

        if trace_interurbain is None:
            print("[corridor_interurbain_vide]")
            echec("corridor_vide")
            continue

        longueur_inter_km = round(longueur_geom(trace_interurbain) / 1000, 2)

        rows_trace_inter.append({
            "ID_ARC"                 : arc_id,
            "VILLE_A"                : ville_a,
            "VILLE_B"                : ville_b,
            "longueur_trace_km"      : longueur_trace_km,
            "longueur_interurbain_km": longueur_inter_km,
            "geometry"               : trace_interurbain,
        })

        # ── EXTRACTION RTSS & DJMA sur corridor interurbain ─────
        zone_recherche = trace_interurbain.buffer(BUFFER_RECHERCHE_M)
        segs_rtss = extraire_rtss(zone_recherche, rtss, rtss_sindex)

        segs_djma = extraire_djma_v3(
            trace_interurbain, djma, djma_sindex,
            pt_a_lambert, pt_b_lambert, noeuds_lambert
        )

        if not segs_djma:
            print("[aucun_djma]")
            base.update({
                "ids_segs_rtss"          : formater_liste([s["num_rts"] for s in segs_rtss]),
                "ids_segs_rtss_dist"     : formater_liste([s["long_m"]  for s in segs_rtss]),
                "ids_segs_rtss_type"     : formater_liste([s["type"]    for s in segs_rtss]),
                "ids_segs_djma"          : None,
                "ids_segs_djma_val"      : None,
                "ids_segs_djma_val_cam"  : None,
                "statut_djma_pct"        : 0.0,
                "longueur_trace_km"      : longueur_trace_km,
                "longueur_interurbain_km": longueur_inter_km,
                "n_segs_djma"            : 0,
                "statut"                 : "aucun_djma",
                "methode"                : "v3",
            })
            rows_arcs.append(base.copy())
            continue

        # Encodage DJMA
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
            "ids_segs_rtss"          : formater_liste([s["num_rts"] for s in segs_rtss]),
            "ids_segs_rtss_dist"     : formater_liste([s["long_m"]  for s in segs_rtss]),
            "ids_segs_rtss_type"     : formater_liste([s["type"]    for s in segs_rtss]),
            "ids_segs_djma"          : formater_liste([s["ide_sectn_trafc"] for s in segs_djma]),
            "ids_segs_djma_val"      : formater_liste(djma_vals),
            "ids_segs_djma_val_cam"  : formater_liste(cam_vals),
            "statut_djma_pct"        : statut_pct,
            "longueur_trace_km"      : longueur_trace_km,
            "longueur_interurbain_km": longueur_inter_km,
            "n_segs_djma"            : len(segs_djma),
            "statut"                 : "ok",
            "methode"                : "v3",
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
              f"qualité {pct} | {longueur_trace_km:.1f}km → inter {longueur_inter_km:.1f}km")

    # ── 3. Export ────────────────────────────────────────────────
    print(f"\n[3/4] Export → {OUTPUT_FILE}")

    gdf_arcs  = gpd.GeoDataFrame(rows_arcs,        crs=CRS_WORK)
    gdf_segs  = gpd.GeoDataFrame(rows_segs,         crs=CRS_WORK)
    gdf_trace = gpd.GeoDataFrame(rows_trace_osrm,   crs=CRS_WORK)
    gdf_inter = gpd.GeoDataFrame(rows_trace_inter,  crs=CRS_WORK)

    for gdf in [gdf_arcs, gdf_segs, gdf_trace, gdf_inter]:
        if "fid" in gdf.columns:
            gdf.drop(columns=["fid"], inplace=True)

    gdf_arcs.to_file( OUTPUT_FILE, layer="arcs_enrichis_v3",     driver="GPKG")
    gdf_segs.to_file( OUTPUT_FILE, layer="trajets_segments_v3",   driver="GPKG", mode="a")
    gdf_trace.to_file(OUTPUT_FILE, layer="trace_osrm_v3",         driver="GPKG", mode="a")
    gdf_inter.to_file(OUTPUT_FILE, layer="trace_interurbain_v3",  driver="GPKG", mode="a")

    # ── 4. Résumé ────────────────────────────────────────────────
    print()
    print("=" * 65)
    print("RÉSUMÉ — VERSION 3")
    print("=" * 65)
    ok    = gdf_arcs[gdf_arcs["statut"] == "ok"]
    n_err = (gdf_arcs["statut"] != "ok").sum()
    print(f"Arcs traités             : {len(gdf_arcs)}")
    print(f"Arcs ok                  : {len(ok)}")
    print(f"Arcs en échec            : {n_err}")
    if len(ok) > 0:
        print(f"\nLongueurs :")
        print(f"  Tracé OSRM médiane    : {ok['longueur_trace_km'].median():.1f} km")
        print(f"  Interurbain médiane   : {ok['longueur_interurbain_km'].median():.1f} km")
        reduction = (1 - ok['longueur_interurbain_km'].mean() / ok['longueur_trace_km'].mean()) * 100
        print(f"  Réduction moy (RMR)   : {reduction:.1f}% du tracé exclu (trafic local)")
        print(f"\nQualité DJMA (arcs ok) :")
        print(f"  Médiane statut_djma_pct : {ok['statut_djma_pct'].median():.0f}%")
        print(f"  Arcs à 100%             : {(ok['statut_djma_pct'] == 100).sum()}")
        print(f"  Arcs < 50%              : {(ok['statut_djma_pct'] < 50).sum()}")
        print(f"  Médiane segs DJMA/arc   : {ok['n_segs_djma'].median():.0f}")
    print(f"\nSegments DJMA exportés : {len(gdf_segs)}")
    print(f"\nParamètres v3 utilisés :")
    print(f"  BUFFER_RECHERCHE_M = {BUFFER_RECHERCHE_M}m")
    print(f"  DIST_MAX_TRACE_M   = {DIST_MAX_TRACE_M}m")
    print(f"  ANGLE_MAX_DEG      = {ANGLE_MAX_DEG}°")
    print(f"  BUFFER_EXCLUSION_M = {BUFFER_EXCLUSION_M}m")
    print(f"  SAMPLE_N_ARCS      = {SAMPLE_N_ARCS}")
    print(f"\nTerminé — résultat dans {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
