"""
atlas_donnees_ml.py
===================
Projet CIRANO — atlas des données pour l'apprentissage automatique.

Recalcule, à partir des seuls fichiers publics du dépôt, chaque agrégat cité dans
docs/donnees_ml.md, et trace les figures de couverture. Le script ne lit aucun
fichier de l'ENR et n'entraîne aucun modèle. Il ne mesure ni la dynamique des
séries, ni le lien entre une cible et ses variables, ni la ressemblance entre
sections voisines : ces questions sont réservées aux labos.

Pseudo-code
-----------
1. inventaire()         fichiers de données : taille, empreinte SHA-256, suivi par git, couches, lignes, champs
2. series()             comptages bruts et complétés → format long (section × année × mesure)
                        mesuré = valeur présente dans le fichier du MTQ ; sinon la méthode de complétion :
                          aucune valeur de DJMA dans la section        → KNN + IDW (DJMA et % camions)
                          du DJMA mais aucun % camions dans la section → forêt aléatoire (% camions)
                          année entre deux années mesurées             → interpolation
                          année avant la première ou après la dernière → extrapolation
3. blocs()              bloc de 50 km = (⌊x / 50 km⌋, ⌊y / 50 km⌋) du centroïde en EPSG:32198,
                        comme scripts/validation_spatiale_randomforest.py (contrôle : 149 blocs)
4. cibles()             par cible possible : valeurs, sections, blocs occupés (taille effective)
5. reseau()             RTSS brut, graphe RTSS, liaisons, routes, sections de calage
6. figures()            couverture temporelle (mesuré / complété par année) et spatiale (par bloc)

Sorties : docs/donnees_ml/agregats.json, figures/ml_couverture_temporelle.png,
          figures/ml_couverture_spatiale.png (agrégats seulement)
Usage   : uv run python scripts/atlas_donnees_ml.py   (environnement uv avec ses extras, ou venv portfolio)
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import warnings
from datetime import date
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pyogrio  # noqa: E402
import shapely  # noqa: E402
import style_figures as sf  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

RACINE = Path(__file__).resolve().parent.parent
BRUT = RACINE / "data" / "raw" / "DebitCirculation.gpkg"
COMPLETE = RACINE / "data" / "processed" / "debits_completes.gpkg"
RTSS = RACINE / "data" / "raw" / "ReseauRoutier_RTSS.gpkg"
LIAISONS = RACINE / "data" / "raw" / "reseau_arcs.gpkg"
MODULE1 = RACINE / "data" / "processed" / "graphe_routier.gpkg"
MODULE1_DJMA = RACINE / "data" / "processed" / "graphe_routier_djma.gpkg"
MULTI_ROUTES = RACINE / "data" / "processed" / "reseau_multi_routes.gpkg"
MULTI_ROUTES_ECH = RACINE / "data" / "processed" / "reseau_multi_routes_echantillon.gpkg"
SORTIE_JSON = RACINE / "docs" / "donnees_ml" / "agregats.json"
DIR_FIGURES = RACINE / "figures"

CRS_METRES = "EPSG:32198"
BLOC_KM = 50
ANNEES = list(range(2025, 2015, -1))            # créneau i (1 à 10) ↔ année 2026 − i
MESURES = {"djma": "DJMA", "djme": "DJME", "djmh": "DJMH", "cam": "% camions"}
STATUTS = ["mesure", "interpolation", "extrapolation", "foret_aleatoire", "knn_idw"]
TEMPORELLES = ("complet", "interpolation", "extrapolation")

# Figures. Statuts : palette catégorielle validée (validate_palette.js du skill dataviz, mode clair, paires
# adjacentes : ΔE daltonisme 9,1 ; vision normale 19,6). Trois teintes passent sous 3:1 contre le fond : la
# légende et le tableau de docs/donnees_ml.md portent les valeurs.
ETIQUETTES_STATUT = {"mesure": "Publié par le MTQ", "interpolation": "Interpolation", "extrapolation": "Extrapolation",
                     "foret_aleatoire": "Forêt aléatoire", "knn_idw": "KNN + IDW"}
COULEURS_STATUT = {"mesure": "#2a78d6", "interpolation": "#eb6834", "extrapolation": "#1baf7a",
                   "foret_aleatoire": "#eda100", "knn_idw": "#e87ba4"}
# Sections par bloc : rampe ordinale à une teinte, validée contre la terre du fond de carte (--ordinal)
BORNES_BLOC = [1, 3, 10, 30, 100]
ETIQUETTES_BLOC = ["1 à 2", "3 à 9", "10 à 29", "30 à 99", "100 et plus"]
COULEURS_BLOC = ["#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]


# ---------------------------------------------------------------------------
# 1. Inventaire
# ---------------------------------------------------------------------------

def empreinte(chemin: Path) -> str:
    """Empreinte SHA-256 d'un fichier (lecture par blocs)."""
    h = hashlib.sha256()
    with chemin.open("rb") as f:
        for bloc in iter(lambda: f.read(1 << 20), b""):
            h.update(bloc)
    return h.hexdigest()


def inventaire(fichiers: list[Path]) -> dict[str, dict]:
    """Taille, empreinte, suivi par git et couches de chaque GeoPackage."""
    suivis = set(subprocess.run(["git", "ls-files", "data"], cwd=RACINE, capture_output=True, text=True,
                                check=True).stdout.split("\n"))
    resultat = {}
    for f in fichiers:
        relatif = f.relative_to(RACINE).as_posix()
        couches = {}
        for nom, geometrie in pyogrio.list_layers(f):
            info = pyogrio.read_info(f, layer=nom)
            couches[str(nom)] = {"lignes": int(info["features"]), "champs": len(info["fields"]),
                                 "geometrie": None if geometrie is None else str(geometrie),
                                 "crs_declare": info["crs"] is not None}
        resultat[relatif] = {"octets": f.stat().st_size, "sha256": empreinte(f), "suivi_par_git": relatif in suivis,
                             "couches": couches}
    return resultat


# ---------------------------------------------------------------------------
# 2. Séries : mesuré ou complété, valeur par valeur
# ---------------------------------------------------------------------------

def lire_brut() -> gpd.GeoDataFrame:
    """Comptages bruts du MTQ ; les valeurs (texte, vide = non mesuré) deviennent des nombres ou NaN."""
    brut = gpd.read_file(BRUT)
    for m in MESURES:
        for i in range(1, 11):
            brut[f"val_{m}_annee_{i}"] = pd.to_numeric(brut[f"val_{m}_annee_{i}"], errors="coerce")
    return brut


def lire_complete() -> pd.DataFrame:
    """Comptages complétés par le Module 1 (sans géométrie)."""
    colonnes = (["ide_sectn_trafc", "methode_djma", "methode_cam", "n_djma_original", "n_cam_original"]
                + [f"val_{m}_annee_{i}" for m in ("djma", "cam") for i in range(1, 11)])
    return gpd.read_file(COMPLETE, columns=colonnes, ignore_geometry=True)


def statut_des_valeurs(brut: pd.DataFrame, comp: pd.DataFrame, mesure: str) -> pd.DataFrame:
    """Format long pour `mesure` (djma ou cam) : une ligne par section et par année.

    Colonnes : ide_sectn_trafc, annee, brut, complete, statut (voir STATUTS).
    """
    cols = [f"val_{mesure}_annee_{i}" for i in range(1, 11)]
    b = brut.set_index("ide_sectn_trafc")[cols]
    c = comp.set_index("ide_sectn_trafc").loc[b.index]
    present = b.notna().to_numpy()                                   # sections × 10, colonnes de 2025 à 2016
    annees = np.array(ANNEES)
    premiere = np.where(present, annees, 9999).min(axis=1)           # première année mesurée de la section
    derniere = np.where(present, annees, -9999).max(axis=1)
    entre = (annees[None, :] > premiere[:, None]) & (annees[None, :] < derniere[:, None])
    statut = np.where(entre, "interpolation", "extrapolation").astype(object)
    if mesure == "cam":
        statut[(c["methode_cam"] == "mice").to_numpy(), :] = "foret_aleatoire"
    statut[(c[f"methode_{mesure}"] == "geo_knn").to_numpy(), :] = "knn_idw"
    statut[present] = "mesure"
    n = len(b)
    return pd.DataFrame({
        "ide_sectn_trafc": np.repeat(b.index.to_numpy(), 10),
        "annee": np.tile(annees, n),
        "brut": b.to_numpy().ravel(),
        "complete": c[cols].to_numpy().ravel(),
        "statut": statut.ravel(),
    })


def integrite(long: pd.DataFrame, comp: pd.DataFrame, mesure: str) -> dict:
    """Contrôles de la complétion : valeurs mesurées conservées, plus aucun manque, méthodes cohérentes."""
    mesurees = long[long["statut"] == "mesure"]
    ecart = (mesurees["brut"] - mesurees["complete"]).abs()
    par_section = long.assign(m=long["statut"] == "mesure").groupby("ide_sectn_trafc")["m"].sum()
    methode = comp.set_index("ide_sectn_trafc")[f"methode_{mesure}"]
    n_original = comp.set_index("ide_sectn_trafc")[f"n_{mesure}_original"]
    completees = long[long["statut"] != "mesure"]
    return {
        "valeurs_mesurees": int(len(mesurees)),
        "mesurees_modifiees_par_la_completion": int((ecart > 1e-9).sum()),
        "ecart_max_sur_valeur_mesuree": float(ecart.max()),
        "valeurs_encore_manquantes": int(long["complete"].isna().sum()),
        "n_original_coherent": bool((n_original.loc[par_section.index] == par_section).all()),
        "sections_completes_coherentes": bool(
            ((methode.loc[par_section.index] == "complet") == (par_section == 10)).all()),
        "completees_negatives": int((completees["complete"] < 0).sum()),
        "completees_nulles": int((completees["complete"] == 0).sum()),
        "completees_superieures_a_100": int((completees["complete"] > 100).sum()) if mesure == "cam" else None,
        "mesurees_nulles": int((mesurees["brut"] == 0).sum()),
    }


# ---------------------------------------------------------------------------
# 3. Blocs spatiaux
# ---------------------------------------------------------------------------

def blocs_50km(geometries: gpd.GeoSeries) -> np.ndarray:
    """Identifiant du bloc de 50 km de chaque géométrie : « ⌊x / 50 km⌋_⌊y / 50 km⌋ » du centroïde en EPSG:32198.

    Même définition que scripts/validation_spatiale_randomforest.py : les comparaisons entre modèles
    doivent mettre de côté les mêmes blocs.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        centre = geometries.to_crs(CRS_METRES).centroid
    cote = BLOC_KM * 1000
    return (np.floor(centre.x / cote).astype(int).astype(str) + "_"
            + np.floor(centre.y / cote).astype(int).astype(str)).to_numpy()


def taille_effective(masque: np.ndarray, blocs: np.ndarray, valeurs: int | None = None) -> dict:
    """Sections et blocs occupés par une cible ; `valeurs` = nombre de valeurs (section × année) s'il diffère."""
    par_bloc = pd.Series(blocs[masque]).value_counts()
    return {
        "valeurs": int(masque.sum()) if valeurs is None else int(valeurs),
        "sections": int(masque.sum()),
        "blocs": int(len(par_bloc)),
        "sections_par_bloc_min": int(par_bloc.min()) if len(par_bloc) else 0,
        "sections_par_bloc_mediane": float(par_bloc.median()) if len(par_bloc) else 0.0,
        "sections_par_bloc_max": int(par_bloc.max()) if len(par_bloc) else 0,
        "part_dans_les_10_blocs_les_plus_denses": round(float(par_bloc.head(10).sum() / max(par_bloc.sum(), 1)), 4),
    }


# ---------------------------------------------------------------------------
# 4. Cibles possibles et taille effective
# ---------------------------------------------------------------------------

def cibles(brut: gpd.GeoDataFrame, comp: pd.DataFrame) -> tuple[dict, dict[str, np.ndarray], np.ndarray]:
    """Taille effective de chaque cible : valeurs, sections, blocs de 50 km occupés.

    Renvoie aussi les masques par section (pour les cartes) et le bloc de chaque section.
    """
    blocs = blocs_50km(brut.geometry)
    djma = brut[[f"val_djma_annee_{i}" for i in range(1, 11)]].notna().to_numpy()
    cam = brut[[f"val_cam_annee_{i}" for i in range(1, 11)]].notna().to_numpy()
    c = comp.set_index("ide_sectn_trafc").loc[brut["ide_sectn_trafc"]]
    garde = (c["methode_djma"].isin(TEMPORELLES) & c["methode_cam"].isin(TEMPORELLES)).to_numpy()
    sans_djma = (c["methode_djma"] == "geo_knn").to_numpy()

    ids = brut["ide_sectn_trafc"].astype(str).to_numpy()
    sur_route = set(gpd.read_file(MULTI_ROUTES, layer="routes_sections")["ide_sectn_trafc"].astype(str))
    observee = djma[:, 0] & cam[:, 0]                                   # DJMA et % camions publiés pour 2025
    calage = observee & np.isin(ids, list(sur_route))

    masques = {
        "toutes_les_sections": np.ones(len(brut), bool),
        "djma_publie": djma.any(axis=1),
        "pct_camions_publie": cam.any(axis=1),
        "djma_et_pct_camions_meme_annee": (djma & cam).any(axis=1),
        "djma_2025": djma[:, 0],
        "pct_camions_2025": cam[:, 0],
        "djma_et_pct_camions_2025": observee,
        "sections_de_calage_module_3": calage,
        "validation_foret_aleatoire": garde,
    }
    valeurs = {"djma_publie": djma.sum(), "pct_camions_publie": cam.sum(),
               "djma_et_pct_camions_meme_annee": (djma & cam).sum(), "validation_foret_aleatoire": garde.sum() * 10}
    resultat = {nom: taille_effective(m, blocs, valeurs.get(nom)) for nom, m in masques.items()}
    resultat["validation_foret_aleatoire"]["cibles_publiees"] = int(cam[garde].sum())
    resultat["validation_foret_aleatoire"]["cibles_completees"] = int((~cam[garde]).sum())
    resultat["validation_foret_aleatoire"]["djma_publies"] = int(djma[garde].sum())
    resultat["validation_foret_aleatoire"]["djma_completes"] = int((~djma[garde]).sum())
    # % camions publié dans une section sans aucun DJMA : la complétion l'a remplacé par la valeur des voisins
    cols = [f"val_cam_annee_{i}" for i in range(1, 11)]
    ecart = (brut[cols].to_numpy() - c[cols].to_numpy())[cam & sans_djma[:, None]]
    resultat["pct_camions_publie_sans_djma"] = {
        "sections": int((cam.any(axis=1) & sans_djma).sum()), "valeurs": int(cam[sans_djma].sum()),
        "valeurs_remplacees_par_la_completion": int((np.abs(ecart) > 1e-9).sum()),
        "ecart_absolu_median_points": round(float(np.median(np.abs(ecart))), 2),
        "ecart_absolu_max_points": round(float(np.abs(ecart).max()), 1),
        "blocs": int(len(set(blocs[cam.any(axis=1) & sans_djma]))),
    }
    return resultat, masques, blocs


# ---------------------------------------------------------------------------
# 5. Réseaux : RTSS, graphe RTSS, liaisons, routes
# ---------------------------------------------------------------------------

def comptes(serie: pd.Series) -> dict[str, int]:
    """Effectifs d'une colonne, valeurs manquantes comprises, en dictionnaire exportable."""
    return {("(vide)" if pd.isna(k) else str(k)): int(v) for k, v in serie.value_counts(dropna=False).items()}


def mal_encode(texte: object) -> bool:
    """Vrai si le texte est de l'UTF-8 lu en Latin-1 (« MontrÃ©al ») : il se répare en le ré-encodant."""
    if not isinstance(texte, str):
        return False
    try:
        return texte.encode("latin-1").decode("utf-8") != texte
    except (UnicodeEncodeError, UnicodeDecodeError):
        return False


def agregats_rtss() -> dict:
    """RTSS brut : lignes et kilomètres par classe et par statut."""
    rtss = gpd.read_file(RTSS, columns=["num_route", "cod_clasf_", "des_clasf_", "sta_sous_r", "cod_cote_c",
                                        "val_longr_"]).to_crs(CRS_METRES)
    km = rtss.geometry.length / 1000
    par = lambda col: {str(k): {"lignes": int(g.size), "km": round(float(g.sum()), 1)}  # noqa: E731
                       for k, g in km.groupby(rtss[col].fillna("(vide)"))}
    return {"lignes": int(len(rtss)), "km": round(float(km.sum()), 1),
            "routes_numerotees": int(rtss["num_route"].nunique()),
            "par_classe": par("des_clasf_"), "par_statut": par("sta_sous_r"), "par_cote_de_chaussee": par("cod_cote_c"),
            "ecart_median_longueur_declaree_m": round(float((rtss["val_longr_"] - km * 1000).abs().median()), 2)}


def agregats_graphe(fichier: Path) -> dict:
    """Graphe RTSS (sortie de `jumeau reseau`) : liens, nœuds, tronçons estimés, connecteurs d'accès."""
    liens = gpd.read_file(fichier, layer="liens_rtss")
    noeuds = gpd.read_file(fichier, layer="noeuds_rtss", ignore_geometry=True)
    acces = gpd.read_file(fichier, layer="acces", ignore_geometry=True)
    km = liens["longueur_m"] / 1000
    par_bloc = km.groupby(blocs_50km(liens.geometry)).sum()
    return {
        "liens": int(len(liens)), "noeuds": int(len(noeuds)), "km": round(float(km.sum()), 1),
        "liens_estimes_raccords": int(liens["raccord"].sum()),
        "km_estimes_raccords": round(float(km[liens["raccord"]].sum()), 1),
        "connecteurs_d_acces": int(len(acces)), "noeuds_de_liaison_raccordes": int(acces["ID"].nunique()),
        "connecteurs_de_plus_de_8_km": int((acces["dist_km"] > 8).sum()),
        "connecteur_le_plus_long_km": round(float(acces["dist_km"].max()), 1),
        "degre_des_noeuds": comptes(noeuds["degre"]),
        "par_classe": {str(k): {"liens": int(g.size), "km": round(float(g.sum()), 1)}
                       for k, g in km.groupby(liens["cod_clasf_"].fillna("(vide)"))},
        "blocs_occupes": int(len(par_bloc)), "km_par_bloc_mediane": round(float(par_bloc.median()), 1),
        "km_par_bloc_max": round(float(par_bloc.max()), 1),
    }


def agregats_rattachement(brut: gpd.GeoDataFrame, blocs: np.ndarray) -> dict:
    """Sections de comptage rattachées aux liens du graphe RTSS : même numéro de route, à 30 m ou moins.

    Reprend la règle de `jumeau reseau` (jumeau_cirano.reseau.sections), appliquée ici à tout le graphe
    et non aux seules routes des liaisons.
    """
    from jumeau_cirano.config import DIR_CONFIGS, ConfigReseau, charger
    from jumeau_cirano.donnees.observations import charger_sections
    from jumeau_cirano.reseau.sections import paires_section_lien

    cfg = charger(DIR_CONFIGS / "reseau.yaml", ConfigReseau)
    sections = charger_sections(cfg)
    liens = gpd.read_file(MULTI_ROUTES, layer="liens_rtss")
    reels = liens[~liens["raccord"]]
    paires = paires_section_lien(sections, reels, cfg.sections.tampon_m)
    rattachees = set(paires["ide_sectn_trafc"].astype(str))
    ids = brut["ide_sectn_trafc"].astype(str).to_numpy()
    djma_2025 = brut["val_djma_annee_1"].notna().to_numpy()
    cam_2025 = brut["val_cam_annee_1"].notna().to_numpy()
    sur_graphe = np.isin(ids, list(rattachees))
    liens_par_section = paires.groupby("ide_sectn_trafc")["id_lien"].nunique()
    sections_par_lien = paires.groupby("id_lien")["ide_sectn_trafc"].nunique()
    couverts = reels["id_lien"].isin(sections_par_lien.index)
    avec_cible = set(ids[sur_graphe & djma_2025 & cam_2025])
    liens_avec_cible = paires.loc[paires["ide_sectn_trafc"].astype(str).isin(avec_cible), "id_lien"].nunique()
    return {
        "tampon_m": cfg.sections.tampon_m, "liens_hors_troncons_estimes": int(len(reels)),
        "liens_avec_section": int(couverts.sum()),
        "part_des_km_avec_section": round(
            float(reels.loc[couverts, "longueur_m"].sum() / reels["longueur_m"].sum()), 4),
        "sections_rattachees": int(sur_graphe.sum()), "sections_non_rattachees": int((~sur_graphe).sum()),
        "liens_par_section_mediane": float(liens_par_section.median()),
        "liens_par_section_max": int(liens_par_section.max()),
        "sections_par_lien_mediane": float(sections_par_lien.median()),
        "sections_par_lien_max": int(sections_par_lien.max()),
        "sections_rattachees_djma_2025": int((sur_graphe & djma_2025).sum()),
        "sections_rattachees_djma_et_pct_camions_2025": int((sur_graphe & djma_2025 & cam_2025).sum()),
        "blocs_des_sections_rattachees_djma_et_pct_camions_2025": int(
            len(set(blocs[sur_graphe & djma_2025 & cam_2025]))),
        "liens_portant_une_section_djma_et_pct_camions_2025": int(liens_avec_cible),
    }


def agregats_liaisons() -> dict:
    """Graphe des liaisons, sorties du Module 1 et réseau à plusieurs routes."""
    arcs = gpd.read_file(LIAISONS, layer="arcs", ignore_geometry=True)
    noeuds = gpd.read_file(LIAISONS, layer="noeuds", ignore_geometry=True)
    djma = gpd.read_file(MODULE1_DJMA, ignore_geometry=True)
    segments = gpd.read_file(MODULE1, layer="trajets_segments", ignore_geometry=True)
    traces = gpd.read_file(MODULE1, layer="trace_osrm")
    blocs_trace = pd.Series(blocs_50km(traces.geometry), index=traces["ID_ARC"])
    ok = djma.loc[djma["statut"] == "ok", "ID_ARC"]
    par_section = segments.groupby("ide_sectn_trafc")["ID_ARC"].nunique()
    brut = gpd.read_file(BRUT, columns=["ide_sectn_trafc", "val_djma_annee_1", "val_cam_annee_1"], ignore_geometry=True)
    publie = brut.set_index("ide_sectn_trafc")[["val_djma_annee_1", "val_cam_annee_1"]].ne("")
    provenance = {}
    for mesure, col in (("djma", "val_djma_annee_1"), ("pct_camions", "val_cam_annee_1")):
        part = segments["ide_sectn_trafc"].map(publie[col]).groupby(segments["ID_ARC"]).mean()
        provenance[mesure] = {
            "sections_publiees_en_2025": int(publie.loc[segments["ide_sectn_trafc"].unique(), col].sum()),
            "liaisons_toutes_sections_publiees": int((part == 1).sum()),
            "liaisons_aucune_section_publiee": int((part == 0).sum()),
            "liaisons_une_partie": int(((part > 0) & (part < 1)).sum())}

    routes = gpd.read_file(MULTI_ROUTES, layer="routes", ignore_geometry=True)
    rs = gpd.read_file(MULTI_ROUTES, layer="routes_sections")
    sections = gpd.read_file(MULTI_ROUTES, layer="sections", ignore_geometry=True)
    controle = gpd.read_file(MULTI_ROUTES, layer="controle_liaisons", ignore_geometry=True)
    arc_de_route = routes.set_index("ID_ROUTE")["ID_ARC"]
    routes_par_section = rs.groupby("ide_sectn_trafc")["ID_ROUTE"].nunique()
    liaisons_par_section = rs.assign(arc=rs["ID_ROUTE"].map(arc_de_route)).groupby("ide_sectn_trafc")["arc"].nunique()
    observees = set(sections.loc[sections["observee"], "ide_sectn_trafc"].astype(str))
    return {
        "liaisons": int(len(arcs)), "noeuds_dans_la_couche": int(len(noeuds)),
        "noeuds_relies_par_une_liaison": int(len(set(arcs["ID_A"]) | set(arcs["ID_B"]))),
        "noeuds_par_type": comptes(noeuds["TYPE"]),
        "noms_de_noeuds_mal_encodes": int(noeuds["NOM"].map(mal_encode).sum()),
        "statut_module_1": comptes(djma["statut"]),
        "liaisons_completees_par_emprunt": int(djma["djma_complete"].sum()),
        "rattachements_liaison_section": int(len(segments)),
        "sections_distinctes_module_1": int(segments["ide_sectn_trafc"].nunique()),
        "sections_sur_plusieurs_liaisons_module_1": int((par_section > 1).sum()),
        "provenance_2025_module_1": provenance,
        "blocs_des_liaisons": int(blocs_trace.nunique()),
        "blocs_des_liaisons_ok": int(blocs_trace.loc[blocs_trace.index.isin(ok)].nunique()),
        "routes": int(len(routes)), "routes_par_source": comptes(routes["source"]),
        "routes_par_rang": comptes(routes["rang"]),
        "liaisons_a_plusieurs_routes": int((routes.groupby("ID_ARC").size() > 1).sum()),
        "rattachements_route_section": int(len(rs)), "sections_sur_une_route": int(rs["ide_sectn_trafc"].nunique()),
        "sections_sur_plusieurs_routes": int((routes_par_section > 1).sum()),
        "sections_sur_plusieurs_liaisons": int((liaisons_par_section > 1).sum()),
        "routes_sans_section": int((routes["n_sections"] == 0).sum()),
        "routes_avec_section_de_calage": int((routes["n_observees"] > 0).sum()),
        "sections_observees": int(len(observees)),
        "sections_de_calage": int(len(observees & set(rs["ide_sectn_trafc"].astype(str)))),
        "routage_par_liaison": comptes(controle["routage"]),
    }


# ---------------------------------------------------------------------------
# 6. Figures de couverture
# ---------------------------------------------------------------------------

def figure_temporelle(statuts: dict[str, dict[str, dict[str, int]]], sections: int) -> Path:
    """Colonnes empilées : statut des valeurs de chaque année, DJMA puis % camions."""
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.9), sharey=True)
    annees = [str(a) for a in sorted(ANNEES)]
    for ax, (mesure, titre) in zip(axes, (("djma", "DJMA"), ("cam", "% de camions")), strict=True):
        bas = np.zeros(len(annees))
        for s in STATUTS:
            h = np.array([statuts[mesure][a][s] for a in annees], dtype=float)
            if h.sum() == 0:
                continue
            ax.bar(annees, h, bottom=bas, width=0.5, color=COULEURS_STATUT[s], edgecolor=sf.SURFACE, linewidth=1.6)
            bas += h
        publie = sum(statuts[mesure][a]["mesure"] for a in annees)
        ax.set_title(f"{titre} : {100 * publie / (sections * len(annees)):.0f} % des valeurs publiées par le MTQ",
                     fontsize=10.5, color=sf.ENCRE_2, loc="left", pad=8)
        ax.set_ylim(0, sections)
        ax.set_yticks([0, 2000, 4000, 6000, sections])
        ax.set_yticklabels(["0", "2 000", "4 000", "6 000", f"{sections:,}".replace(",", " ")])
        ax.grid(axis="y", color=sf.GRILLE, linewidth=0.8)
        ax.set_axisbelow(True)
        ax.tick_params(length=0, labelsize=9.5)
        for cote in ("top", "right", "left"):
            ax.spines[cote].set_visible(False)
    axes[0].set_ylabel("Sections de comptage", fontsize=10)
    fig.suptitle("Valeurs publiées par le MTQ et valeurs complétées par le Module 1, par année", fontsize=13,
                 fontweight="bold", color=sf.ENCRE, x=0.065, ha="left", y=0.975)
    poignees = [Patch(facecolor=COULEURS_STATUT[s], label=ETIQUETTES_STATUT[s]) for s in STATUTS]
    legende = fig.legend(handles=poignees, loc="lower center", ncol=5, fontsize=9.5, frameon=True, facecolor=sf.SURFACE,
                         edgecolor=sf.BASELINE, framealpha=1.0, bbox_to_anchor=(0.5, 0.0))
    legende.get_frame().set_linewidth(0.8)
    fig.tight_layout(rect=(0, 0.075, 1, 0.965))
    sortie = DIR_FIGURES / "ml_couverture_temporelle.png"
    fig.savefig(sortie, dpi=170)
    plt.close(fig)
    return sortie


def fond_de_carte(emprise: tuple[float, float, float, float]) -> dict[str, gpd.GeoDataFrame]:
    """Fond Natural Earth (data/reference/), découpé à l'emprise, en EPSG:32198."""
    fenetre = shapely.box(*emprise)
    lire = lambda nom: gpd.clip(gpd.read_file(sf.DIR_REFERENCE / nom).to_crs(CRS_METRES), fenetre)  # noqa: E731
    admin = lire("ne_50m_admin_1_states_provinces.zip")
    return {"quebec": admin[admin["name"] == "Québec"], "autres": admin[admin["name"] != "Québec"],
            "ocean": lire("ne_50m_ocean.zip"), "lacs": lire("ne_50m_lakes.zip")}


def figure_spatiale(masques: dict[str, np.ndarray], blocs: np.ndarray, tailles: dict) -> Path:
    """Trois cartes : sections par bloc de 50 km, pour trois cibles."""
    panneaux = [("djma_publie", "DJMA publié, au moins une année"),
                ("pct_camions_publie", "% de camions publié, au moins une année"),
                ("sections_de_calage_module_3", "Sections de calage du Module 3 (2025)")]
    cote = BLOC_KM * 1000
    ix, iy = np.array([b.split("_") for b in blocs], dtype=int).T
    emprise = (ix.min() * cote - 30_000, iy.min() * cote - 30_000, (ix.max() + 1) * cote + 30_000,
               (iy.max() + 1) * cote + 30_000)
    fond = fond_de_carte(emprise)
    tous = set(blocs)
    fig, axes = plt.subplots(1, 3, figsize=(15.5, 5.0))
    for ax, (cle, titre) in zip(axes, panneaux, strict=True):
        fond["ocean"].plot(ax=ax, color=sf.EAU, linewidth=0, zorder=0)
        fond["autres"].plot(ax=ax, color=sf.FOND_TERRE, edgecolor=sf.FOND_BORDURE, linewidth=0.6, zorder=0)
        fond["quebec"].plot(ax=ax, color=sf.FOND_TERRE, edgecolor=sf.QUEBEC_BORDURE, linewidth=1.0, zorder=0)
        fond["lacs"].plot(ax=ax, color=sf.EAU, linewidth=0, zorder=0)
        par_bloc = pd.Series(blocs[masques[cle]]).value_counts()
        carres, couleurs = [], []
        for b, n in par_bloc.items():
            x, y = (int(v) * cote for v in b.split("_"))
            carres.append(shapely.box(x + 1500, y + 1500, x + cote - 1500, y + cote - 1500))
            couleurs.append(COULEURS_BLOC[int(np.searchsorted(BORNES_BLOC, n, side="right")) - 1])
        sans_valeur = [[int(v) * cote for v in b.split("_")] for b in tous - set(par_bloc.index)]
        vides = [shapely.box(x + 1500, y + 1500, x + cote - 1500, y + cote - 1500) for x, y in sans_valeur]
        gpd.GeoSeries(vides).plot(ax=ax, facecolor="none", edgecolor=sf.MUET, linewidth=0.5, zorder=2)
        gpd.GeoSeries(carres).plot(ax=ax, color=couleurs, linewidth=0, zorder=3)
        ax.set_xlim(emprise[0], emprise[2])
        ax.set_ylim(emprise[1], emprise[3])
        ax.set_axis_off()
        t = tailles[cle]
        ax.text(0.0, 1.065, titre, fontsize=11.5, fontweight="bold", color=sf.ENCRE, transform=ax.transAxes)
        ax.text(0.0, 1.015, f"{t['sections']:,} sections dans {t['blocs']} blocs".replace(",", " "), fontsize=10,
                color=sf.ENCRE_2, transform=ax.transAxes)
    poignees = [Patch(facecolor=c, label=e) for c, e in zip(COULEURS_BLOC, ETIQUETTES_BLOC, strict=True)]
    poignees.append(Patch(facecolor="none", edgecolor=sf.MUET, label="Des sections, aucune valeur"))
    legende = fig.legend(handles=poignees, loc="lower center", ncol=6, fontsize=9.5, frameon=True, facecolor=sf.SURFACE,
                         edgecolor=sf.BASELINE, framealpha=1.0, title="Sections par bloc de 50 km",
                         bbox_to_anchor=(0.5, 0.005))
    legende.get_frame().set_linewidth(0.8)
    legende.get_title().set_fontweight("bold")
    fig.suptitle("Couverture spatiale des valeurs publiées par le MTQ, par bloc de 50 km", fontsize=13.5,
                 fontweight="bold", color=sf.ENCRE, x=0.012, ha="left", y=0.985)
    fig.subplots_adjust(left=0.01, right=0.99, top=0.84, bottom=0.14, wspace=0.03)
    sortie = DIR_FIGURES / "ml_couverture_spatiale.png"
    fig.savefig(sortie, dpi=160)
    plt.close(fig)
    return sortie


# ---------------------------------------------------------------------------
# Assemblage
# ---------------------------------------------------------------------------

def agregats_comptages() -> tuple[dict, dict]:
    """Agrégats des comptages et tables intermédiaires utiles aux figures."""
    brut, comp = lire_brut(), lire_complete()
    assert brut["ide_sectn_trafc"].is_unique and len(brut) == len(comp)
    for m in MESURES:                                                # le créneau i porte bien l'année 2026 − i
        for i, annee in enumerate(ANNEES, start=1):
            assert (brut[f"{m}_annee_{i}"] == annee).all(), (m, i)

    a: dict = {"sections": int(len(brut)), "annees": [min(ANNEES), max(ANNEES)]}
    a["valeurs_presentes"] = {
        m: {str(annee): int(brut[f"val_{m}_annee_{i}"].notna().sum()) for i, annee in enumerate(ANNEES, start=1)}
        for m in MESURES}
    a["valeurs_presentes_total"] = {m: int(sum(v.values())) for m, v in a["valeurs_presentes"].items()}

    longs = {m: statut_des_valeurs(brut, comp, m) for m in ("djma", "cam")}
    a["statut_par_valeur"] = {
        m: {str(annee): {s: int(n) for s, n in g["statut"].value_counts().reindex(STATUTS, fill_value=0).items()}
            for annee, g in sorted(longs[m].groupby("annee"), key=lambda x: x[0])}
        for m in longs}
    a["statut_total"] = {
        m: {s: int(n) for s, n in longs[m]["statut"].value_counts().reindex(STATUTS, fill_value=0).items()}
        for m in longs}
    a["sections_par_nombre_d_annees_mesurees"] = {
        m: {str(k): int(n) for k, n in longs[m].assign(x=longs[m]["statut"] == "mesure").groupby("ide_sectn_trafc")["x"]
            .sum().value_counts().reindex(range(11), fill_value=0).items()}
        for m in longs}
    a["methode_par_section"] = {f"{d} / {c}": int(n) for (d, c), n in
                                comp.groupby(["methode_djma", "methode_cam"]).size().items()}
    a["integrite"] = {m: integrite(longs[m], comp, m) for m in longs}
    # Extrapolation : pente locale, ou simple recopie quand la section n'a qu'une année publiée
    a["extrapolation_par_recopie"] = {}
    for m, long in longs.items():
        publiees = long.assign(p=long["statut"] == "mesure").groupby("ide_sectn_trafc")["p"].transform("sum")
        a["extrapolation_par_recopie"][m] = int(((long["statut"] == "extrapolation") & (publiees == 1)).sum())
    a["distribution_des_valeurs_publiees"] = {}
    for m in MESURES:
        v = brut[[f"val_{m}_annee_{i}" for i in range(1, 11)]].to_numpy().ravel()
        v = v[~np.isnan(v)]
        a["distribution_des_valeurs_publiees"][m] = {
            "min": float(v.min()), "centile_1": float(np.quantile(v, 0.01)), "mediane": float(np.median(v)),
            "centile_99": float(np.quantile(v, 0.99)), "max": float(v.max())}
    djma = brut[[f"val_djma_annee_{i}" for i in range(1, 11)]].to_numpy().ravel()
    djma = djma[~np.isnan(djma)]
    a["djma_multiples_de_10"] = round(float((djma % 10 == 0).mean()), 4)
    a["djma_multiples_de_100"] = round(float((djma % 100 == 0).mean()), 4)
    a["trentieme_heure_presente"] = int(pd.to_numeric(brut["val_30e_heure"], errors="coerce").notna().sum())
    a["geometries"] = comptes(brut.geometry.geom_type)
    longueur = brut.geometry.to_crs(CRS_METRES).length
    a["longueur_des_sections_m"] = {"min": round(float(longueur.min()), 1),
                                    "mediane": round(float(longueur.median()), 1),
                                    "max": round(float(longueur.max()), 1)}
    debut = brut["dat_debut_sectn_trafc"]
    a["debut_des_sections"] = [str(debut.min())[:10], str(debut.max())[:10]]
    a["index"] = {c: comptes(brut[c]) for c in ("index_agreg", "index_sectn", "index_donnees")}
    a["doublons"] = {c: int(brut[c].duplicated().sum()) for c in ("ide_sectn_trafc", "num_sectn_trafc", "objectid")}
    a["doublons"]["geometrie"] = int(brut.geometry.to_wkb().duplicated().sum())
    return a, {"brut": brut, "comp": comp, "longs": longs}


def main() -> None:
    sf.appliquer_style()
    print("[1/6] inventaire")
    fichiers = [BRUT, RTSS, LIAISONS, COMPLETE, MODULE1, MODULE1_DJMA, MULTI_ROUTES, MULTI_ROUTES_ECH]
    agregats: dict = {"genere_le": date.today().isoformat(), "fichiers": inventaire(fichiers)}
    print("[2/6] séries : publié par le MTQ ou complété")
    agregats["comptages"], tables = agregats_comptages()
    print("[3/6] blocs de 50 km et cibles")
    agregats["blocs"] = {"cote_km": BLOC_KM, "crs": CRS_METRES,
                         "definition": "floor(x / 50 km)_floor(y / 50 km) du centroïde de la géométrie"}
    agregats["cibles"], masques, blocs = cibles(tables["brut"], tables["comp"])
    assert agregats["cibles"]["validation_foret_aleatoire"]["blocs"] == 149, "la définition des blocs a changé"
    print("[4/6] RTSS, graphe, liaisons, routes")
    agregats["rtss"] = agregats_rtss()
    agregats["graphe_rtss"] = {"quebec": agregats_graphe(MULTI_ROUTES),
                               "echantillon": agregats_graphe(MULTI_ROUTES_ECH)}
    agregats["liaisons"] = agregats_liaisons()
    agregats["rattachement_sections_liens"] = agregats_rattachement(tables["brut"], blocs)
    print("[5/6] figures")
    for figure in (figure_temporelle(agregats["comptages"]["statut_par_valeur"], agregats["comptages"]["sections"]),
                   figure_spatiale(masques, blocs, agregats["cibles"])):
        print(f"      → {figure.relative_to(RACINE)}")
    print("[6/6] agrégats")
    SORTIE_JSON.parent.mkdir(parents=True, exist_ok=True)
    SORTIE_JSON.write_text(json.dumps(agregats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"      → {SORTIE_JSON.relative_to(RACINE)}")


if __name__ == "__main__":
    main()
