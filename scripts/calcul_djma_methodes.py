"""
calcul_djma_methodes.py
========================
Projet CIRANO II — Calcul du DJMA agrégé par arc, 4 méthodes combinées

MÉTHODES
--------
  m1 : Moyenne simple des DJMA des segments sous-jacents
  m2 : Moyenne pondérée par longueur de segment (depuis la géométrie de trajets_segments)
  m3 : Moyenne pondérée par type d'axe routier (hiérarchie MTQ) ; la classe de
       chaque section vient du RTSS, par son code de sous-route (jointure exacte)
  m4 : 80e percentile par rang (nearest-rank) — valeur réelle du segment
       classé à la position round(PERCENTILE_M4 × n), pas d'interpolation

ENTRÉE
------
  data/processed/graphe_routier.gpkg
    layers : arcs_enrichis, trajets_segments

SORTIE
------
  data/processed/graphe_routier_djma.gpkg
    layer  : arcs_enrichis_djma

CHAMPS PRODUITS (par méthode)
------------------------------
  djma_m{N}     : DJMA agrégé (entier)
  pct_cam_m{N}  : % camion agrégé (1 décimale) — N/A pour m4
  djma_cam_m{N} : débit camion estimé = djma_m{N} × pct_cam_m{N} / 100 (entier)
  n_segs_m{N}   : nombre de segments contributeurs (reste à 0 pour un arc complété)

COMPLÉTION GÉOGRAPHIQUE (arcs statut == "aucun_djma", cf. completer_echecs_geographique)
------------------------------------------------------------------------------------------
  djma_complete           : True si le DJMA a été emprunté au plus proche voisin
  djma_arc_source          : ID_ARC de l'arc donneur
  djma_distance_source_km : distance moyenne du tracé de l'arc au tracé du donneur (km)

CORRECTIFS DU 2026-10-28
------------------------
  - m3 appariait les valeurs des sections avec la liste des sous-routes RTSS de
    l'arc (deux listes de longueurs différentes) : la classe de chaque section
    est désormais lue dans le RTSS par son code de sous-route.
  - La complétion comparait les lignes droites A–B, qui se touchent aux nœuds :
    distance toujours nulle. Elle compare désormais les tracés OSRM, par la
    distance moyenne (pas le minimum, nul lui aussi entre arcs voisins).
"""

import warnings
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ============================================================
# CONFIGURATION
# ============================================================

RACINE       = Path(__file__).resolve().parent.parent
INPUT_FILE   = RACINE / "data" / "processed" / "graphe_routier.gpkg"
LAYER_ARCS   = "arcs_enrichis"
LAYER_SEGS   = "trajets_segments"
LAYER_TRACES = "trace_osrm"

FICHIER_SECTIONS = RACINE / "data" / "raw" / "DebitCirculation.gpkg"
FICHIER_RTSS     = RACINE / "data" / "raw" / "ReseauRoutier_RTSS.gpkg"

OUTPUT_FILE  = RACINE / "data" / "processed" / "graphe_routier_djma.gpkg"
OUTPUT_LAYER = "arcs_enrichis_djma"

# Poids m3 — hiérarchie fonctionnelle MTQ
POIDS_TYPE: dict[str, int] = {
    "Autoroute": 4,
    "Nationale": 3,
    "Régionale": 2,
    "Collectrice": 2,
    "Accès aux ressources": 1,
    "Accès aux ressources et aux localités isolées": 1,
    "Sans classe": 1,
    "Local 1": 1,
    "Local 2": 1,
    "Local 3": 1,
}
POIDS_DEFAUT = 1

# Paramètre m4 — percentile visé (nearest-rank, valeur réelle sans interpolation)
PERCENTILE_M4 = 0.80


# ============================================================
# PARSING COMMUN
# ============================================================

def parser_valeurs(chaine: str | None, separateur: str = "|") -> list[float]:
    """Extrait les valeurs numériques d'une chaîne 'valeur@année|...' ; ignore NA."""
    if not chaine or pd.isna(chaine):
        return []
    valeurs = []
    for token in str(chaine).split(separateur):
        token = token.strip()
        if not token or token.upper() == "NA":
            continue
        try:
            valeurs.append(float(token.split("@")[0]))
        except ValueError:
            continue
    return valeurs


def parser_paires_djma_cam(
    chaine_djma: str | None,
    chaine_cam: str | None,
    separateur: str = "|",
) -> list[tuple[float, float | None]]:
    """Parse ids_segs_djma_val et ids_segs_djma_val_cam en parallèle (même segment,
    même position) → liste de (djma_val, cam_val ou None si NA)."""
    if not chaine_djma or pd.isna(chaine_djma):
        return []
    tokens_djma = str(chaine_djma).split(separateur)
    tokens_cam  = str(chaine_cam).split(separateur) if (chaine_cam and not pd.isna(chaine_cam)) \
                  else [""] * len(tokens_djma)
    paires = []
    for td, tc in zip(tokens_djma, tokens_cam):
        td = td.strip()
        if not td or td.upper() == "NA":
            continue
        try:
            val_djma = float(td.split("@")[0])
        except ValueError:
            continue
        tc = tc.strip()
        val_cam = None
        if tc and tc.upper() != "NA":
            try:
                val_cam = float(tc.split("@")[0])
            except ValueError:
                val_cam = None
        paires.append((val_djma, val_cam))
    return paires


def classes_sections() -> dict[str, str]:
    """Classe routière RTSS de chaque section de comptage, par jointure exacte.

    La section porte le code RTSS de sa sous-route de début (`00073-03-205-32A0`)
    et de fin ; le RTSS porte la classe de chaque sous-route (`num_rts`, mêmes
    14 caractères sans tirets). Repli sur le code de fin ; sans correspondance,
    la section garde le poids par défaut.
    """
    sections = gpd.read_file(FICHIER_SECTIONS, ignore_geometry=True,
                             columns=["ide_sectn_trafc", "rtss_debut", "rtss_fin"])
    rtss = gpd.read_file(FICHIER_RTSS, ignore_geometry=True, columns=["num_rts", "des_clasf_"])
    classe = rtss.drop_duplicates("num_rts").set_index("num_rts")["des_clasf_"]
    debut = sections["rtss_debut"].str.replace("-", "", regex=False).map(classe)
    fin = sections["rtss_fin"].str.replace("-", "", regex=False).map(classe)
    return dict(zip(sections["ide_sectn_trafc"].astype(str), debut.fillna(fin)))


def parser_paires_type(
    chaine_val: str | None,
    types: list[str | None],
    separateur: str = "|",
) -> tuple[list[float], list[int]]:
    """Parse ids_segs_djma_val avec la classe de chaque section (même ordre) → (valeurs, poids)."""
    if not chaine_val or pd.isna(chaine_val):
        return [], []
    tokens_val = str(chaine_val).split(separateur)
    vals, poids = [], []
    for tv, tt in zip(tokens_val, types):
        tv = tv.strip()
        if not tv or tv.upper() == "NA":
            continue
        try:
            vals.append(float(tv.split("@")[0]))
            poids.append(POIDS_TYPE.get(tt, POIDS_DEFAUT) if isinstance(tt, str) else POIDS_DEFAUT)
        except ValueError:
            continue
    return vals, poids


# ============================================================
# M1 — Moyenne simple
# ============================================================

def calculer_m1(row: pd.Series) -> tuple:
    vals_djma = parser_valeurs(row.get("ids_segs_djma_val"))
    vals_cam  = parser_valeurs(row.get("ids_segs_djma_val_cam"))
    if not vals_djma:
        return None, None, None, 0
    djma_m1    = round(float(np.mean(vals_djma)))
    pct_cam_m1 = round(float(np.mean(vals_cam)), 1) if vals_cam else None
    djma_cam_m1 = round(djma_m1 * pct_cam_m1 / 100) if pct_cam_m1 is not None else None
    return djma_m1, pct_cam_m1, djma_cam_m1, len(vals_djma)


def appliquer_m1(arcs: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    res = arcs.apply(calculer_m1, axis=1, result_type="expand")
    res.columns = ["djma_m1", "pct_cam_m1", "djma_cam_m1", "n_segs_m1"]
    arcs = arcs.copy()
    arcs["djma_m1"]     = res["djma_m1"].astype("Int64")
    arcs["pct_cam_m1"]  = res["pct_cam_m1"]
    arcs["djma_cam_m1"] = res["djma_cam_m1"].astype("Int64")
    arcs["n_segs_m1"]   = res["n_segs_m1"].astype(int)
    return arcs


# ============================================================
# M2 — Pondération par longueur (nécessite trajets_segments)
# ============================================================

def aggreger_m2(segs: gpd.GeoDataFrame) -> pd.DataFrame:
    """Calcule djma_m2 (moyenne pondérée par longueur) pour chaque arc."""
    resultats = []
    for id_arc, groupe in segs.groupby("ID_ARC"):
        vals_djma = groupe["djma_val"].dropna()
        if vals_djma.empty:
            resultats.append({"ID_ARC": id_arc, "djma_m2": None, "pct_cam_m2": None,
                               "djma_cam_m2": None, "n_segs_m2": 0})
            continue
        idx       = vals_djma.index
        poids     = groupe.loc[idx, "longueur_m"]
        total_p   = poids.sum()
        if total_p == 0:
            resultats.append({"ID_ARC": id_arc, "djma_m2": None, "pct_cam_m2": None,
                               "djma_cam_m2": None, "n_segs_m2": 0})
            continue
        djma_m2  = round(float(np.average(vals_djma, weights=poids)))
        vals_cam = groupe.loc[idx, "cam_val"].dropna()
        if not vals_cam.empty:
            pct_cam_m2 = round(float(np.average(vals_cam, weights=groupe.loc[vals_cam.index, "longueur_m"])), 1)
        else:
            pct_cam_m2 = None
        djma_cam_m2 = round(djma_m2 * pct_cam_m2 / 100) if pct_cam_m2 is not None else None
        resultats.append({"ID_ARC": id_arc, "djma_m2": djma_m2, "pct_cam_m2": pct_cam_m2,
                           "djma_cam_m2": djma_cam_m2, "n_segs_m2": len(vals_djma)})
    return pd.DataFrame(resultats)


def appliquer_m2(arcs: gpd.GeoDataFrame, segs: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    df = aggreger_m2(segs)
    arcs = arcs.merge(df, on="ID_ARC", how="left")
    arcs["djma_m2"]     = arcs["djma_m2"].astype("Int64")
    arcs["djma_cam_m2"] = arcs["djma_cam_m2"].astype("Int64")
    arcs["n_segs_m2"]   = arcs["n_segs_m2"].fillna(0).astype(int)
    return arcs


# ============================================================
# M3 — Pondération par type d'axe
# ============================================================

def calculer_m3(row: pd.Series, classes: dict[str, str]) -> tuple:
    ids = str(row.get("ids_segs_djma_id") or "").split("|")
    types = [classes.get(i.strip()) for i in ids]
    vals, poids = parser_paires_type(row.get("ids_segs_djma_val"), types)
    if not vals:
        return None, None, None, 0
    total_p = sum(poids)
    djma_m3 = round(float(np.average(vals, weights=poids))) if total_p > 0 else round(float(np.mean(vals)))
    vals_cam, poids_cam = parser_paires_type(row.get("ids_segs_djma_val_cam"), types)
    if vals_cam:
        total_pc   = sum(poids_cam)
        pct_cam_m3 = round(float(np.average(vals_cam, weights=poids_cam)), 1) if total_pc > 0 \
                     else round(float(np.mean(vals_cam)), 1)
    else:
        pct_cam_m3 = None
    djma_cam_m3 = round(djma_m3 * pct_cam_m3 / 100) if pct_cam_m3 is not None else None
    return djma_m3, pct_cam_m3, djma_cam_m3, len(vals)


def appliquer_m3(arcs: gpd.GeoDataFrame, classes: dict[str, str]) -> gpd.GeoDataFrame:
    res = arcs.apply(calculer_m3, axis=1, result_type="expand", classes=classes)
    res.columns = ["djma_m3", "pct_cam_m3", "djma_cam_m3", "n_segs_m3"]
    arcs = arcs.copy()
    arcs["djma_m3"]     = res["djma_m3"].astype("Int64")
    arcs["pct_cam_m3"]  = res["pct_cam_m3"]
    arcs["djma_cam_m3"] = res["djma_cam_m3"].astype("Int64")
    arcs["n_segs_m3"]   = res["n_segs_m3"].astype(int)
    return arcs


# ============================================================
# M4 — 80e percentile par rang (nearest-rank, valeur réelle)
# ============================================================

def calculer_m4(row: pd.Series) -> tuple:
    paires = parser_paires_djma_cam(row.get("ids_segs_djma_val"), row.get("ids_segs_djma_val_cam"))
    if not paires:
        return None, None, None, 0
    n    = len(paires)
    rang = min(max(round(PERCENTILE_M4 * n), 1), n)
    djma_m4, cam_m4 = sorted(paires, key=lambda p: p[0])[rang - 1]
    djma_m4     = round(djma_m4)
    pct_cam_m4  = round(cam_m4, 1) if cam_m4 is not None else None
    djma_cam_m4 = round(djma_m4 * pct_cam_m4 / 100) if pct_cam_m4 is not None else None
    return djma_m4, pct_cam_m4, djma_cam_m4, n


def appliquer_m4(arcs: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    res = arcs.apply(calculer_m4, axis=1, result_type="expand")
    res.columns = ["djma_m4", "pct_cam_m4", "djma_cam_m4", "n_segs_m4"]
    arcs = arcs.copy()
    arcs["djma_m4"]     = res["djma_m4"].astype("Int64")
    arcs["pct_cam_m4"]  = res["pct_cam_m4"]
    arcs["djma_cam_m4"] = res["djma_cam_m4"].astype("Int64")
    arcs["n_segs_m4"]   = res["n_segs_m4"].astype(int)
    return arcs


# ============================================================
# COMPLÉTION GÉOGRAPHIQUE DES ÉCHECS (statut == "aucun_djma")
# ============================================================

COLS_A_EMPRUNTER = [
    "djma_m1", "pct_cam_m1", "djma_cam_m1",
    "djma_m2", "pct_cam_m2", "djma_cam_m2",
    "djma_m3", "pct_cam_m3", "djma_cam_m3",
    "djma_m4", "pct_cam_m4", "djma_cam_m4",
]


def distance_moyenne_traces(trace, autre, n_points: int = 21) -> float:
    """Distance moyenne (m) de points régulièrement espacés sur `trace` jusqu'à `autre`."""
    points = [trace.interpolate(t, normalized=True) for t in np.linspace(0, 1, n_points)]
    return float(np.mean([autre.distance(p) for p in points]))


def completer_echecs_geographique(arcs: gpd.GeoDataFrame, traces: gpd.GeoSeries) -> gpd.GeoDataFrame:
    """Emprunte le DJMA (m1-m4) de l'arc valide le plus proche pour les arcs en
    échec 'aucun_djma' (aucune station MTQ à proximité du tracé, donc aucun
    segment propre pour calculer m1-m4).

    Un seul plus proche voisin parmi les arcs statut == 'ok', au sens de la
    distance moyenne entre tracés OSRM (`traces`, indexé par ID_ARC). La
    distance minimale ne convient pas : deux arcs voisins se touchent au nœud
    commun, elle vaut 0 pour tous les voisins. Ce n'est pas
    une moyenne pondérée comme le KNN+IDW temporel de
    completion_donnees_randomforest.py (qui comble les trous d'une série d'une
    station qui *existe déjà*) : ici aucune mesure n'existe pour cet arc, donc
    on ne fait qu'emprunter telle quelle la meilleure estimation disponible à
    proximité — n_segs_m{N} reste à 0, ces arcs n'ont toujours aucun segment
    MTQ propre. djma_complete / djma_arc_source / djma_distance_source_km
    tracent l'origine de chaque valeur empruntée. Les arcs 'hors_quebec' (le
    tracé sort du territoire) ne sont pas concernés.
    """
    arcs = arcs.copy()
    arcs["djma_complete"]           = False
    arcs["djma_arc_source"]         = pd.array([pd.NA] * len(arcs), dtype="Int64")
    arcs["djma_distance_source_km"] = np.nan

    valides     = arcs[arcs["statut"] == "ok"]
    a_completer = arcs[arcs["statut"] == "aucun_djma"]

    for idx, arc in a_completer.iterrows():
        trace        = traces[arc["ID_ARC"]]
        distances    = valides["ID_ARC"].map(lambda i: distance_moyenne_traces(trace, traces[i]))
        idx_proche   = distances.idxmin()
        plus_proche  = valides.loc[idx_proche]

        arcs.loc[idx, COLS_A_EMPRUNTER]          = plus_proche[COLS_A_EMPRUNTER]
        arcs.loc[idx, "djma_complete"]           = True
        arcs.loc[idx, "djma_arc_source"]         = plus_proche["ID_ARC"]
        arcs.loc[idx, "djma_distance_source_km"] = round(distances[idx_proche] / 1000, 1)

    for col in ["djma_m1", "djma_m2", "djma_m3", "djma_m4",
                "djma_cam_m1", "djma_cam_m2", "djma_cam_m3", "djma_cam_m4"]:
        arcs[col] = arcs[col].astype("Int64")

    return arcs


# ============================================================
# RÉSUMÉ PAR MÉTHODE
# ============================================================

def afficher_resume(gdf: gpd.GeoDataFrame, col: str, label: str) -> None:
    n_avec = gdf[col].notna().sum()
    n_sans = gdf[col].isna().sum()
    ok     = gdf[gdf[col].notna()]
    print(f"  {label:<8} : {n_avec:>3} arcs avec valeur  |  {n_sans:>2} sans")
    if n_avec > 0:
        print(f"           médiane={ok[col].median():.0f}  min={ok[col].min()}  max={ok[col].max()}")


# ============================================================
# PIPELINE PRINCIPAL
# ============================================================

def main() -> None:
    print("=" * 65)
    print("CALCUL DJMA — méthodes m1, m2, m3, m4 (v4)")
    print(f"  percentile m4 = {PERCENTILE_M4}")
    print("=" * 65)

    print(f"\n[1/5] Chargement {LAYER_ARCS}...")
    arcs = gpd.read_file(INPUT_FILE, layer=LAYER_ARCS)
    print(f"  {len(arcs)} arcs chargés")

    print(f"\n[2/5] Chargement {LAYER_SEGS}...")
    segs = gpd.read_file(INPUT_FILE, layer=LAYER_SEGS)
    segs["longueur_m"] = segs.geometry.length
    print(f"  {len(segs)} segments chargés")

    print("\n[3/5] Calcul m1 (moyenne simple)...")
    arcs = appliquer_m1(arcs)

    print("[3/5] Calcul m2 (pondération longueur)...")
    arcs = appliquer_m2(arcs, segs)

    print("[3/5] Calcul m3 (pondération type d'axe)...")
    classes = classes_sections()
    print(f"  classe RTSS retrouvée pour {sum(isinstance(c, str) for c in classes.values())}/{len(classes)} sections")
    arcs = appliquer_m3(arcs, classes)

    print("[3/6] Calcul m4 (80e percentile, nearest-rank)...")
    arcs = appliquer_m4(arcs)

    print("\n[4/6] Complétion géographique des échecs (statut == aucun_djma)...")
    n_avant = arcs["statut"].eq("aucun_djma").sum()
    traces = gpd.read_file(INPUT_FILE, layer=LAYER_TRACES).drop_duplicates("ID_ARC").set_index("ID_ARC").geometry
    arcs = completer_echecs_geographique(arcs, traces)
    n_completes = arcs["djma_complete"].sum()
    print(f"  {n_completes}/{n_avant} arcs complétés par emprunt au plus proche voisin")
    if n_completes:
        dist = arcs.loc[arcs["djma_complete"], "djma_distance_source_km"]
        print(f"  distance au donneur : médiane={dist.median():.1f} km, max={dist.max():.1f} km")

    print("\n[5/6] Résultats :")
    for col, label in [("djma_m1", "m1"), ("djma_m2", "m2"), ("djma_m3", "m3"), ("djma_m4", "m4")]:
        afficher_resume(arcs, col, label)

    print(f"\n[6/6] Export → {OUTPUT_FILE}  (layer : {OUTPUT_LAYER})")
    arcs.to_file(OUTPUT_FILE, layer=OUTPUT_LAYER, driver="GPKG")
    print("  Terminé.")


if __name__ == "__main__":
    main()
