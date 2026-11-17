"""
commande.py
===========
`jumeau tables [--config configs/tables.yaml] [--echantillon]`

Écrit dans outputs/ml/ (hors Git), en Parquet :
    sections_annees   une ligne par section de comptage et par année
    noeuds, aretes    graphe RTSS produit par `jumeau reseau`
    aretes_sections   quelles sections longent quel lien (même numéro de route, à 30 m ou moins)
    aretes_annees     une ligne par lien et par année : valeurs des sections du lien
    meta.json         lignes, colonnes, version du code, empreintes des fichiers lus, contrôles
                      (pour tout le Québec, copié dans `meta_publique` : docs/donnees_ml/tables.json)

Pseudo-code
-----------
1. lire le brut (valeurs publiées), le complété (valeurs et méthodes), les sections projetées et leur classe
2. échantillon : garder les sections dont le centroïde tombe dans l'emprise
3. construire les tables (tables.py), vérifier les cibles
4. contrôles : autant de cibles que de valeurs publiées ; sur tout le Québec, les 149 blocs de la validation
5. écrire
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from datetime import date
from pathlib import Path

import geopandas as gpd
import pandas as pd

from jumeau_cirano import __version__
from jumeau_cirano.config import DIR_CONFIGS, RACINE, ConfigReseau, ConfigTables, charger, chemin_absolu
from jumeau_cirano.donnees import tables as t
from jumeau_cirano.donnees.blocs import CRS_BLOCS
from jumeau_cirano.donnees.observations import charger_sections
from jumeau_cirano.reseau.commande import suffixer
from jumeau_cirano.reseau.sections import paires_section_lien

MESURES_LUES = ("djma", "djme", "djmh", "cam")


def empreinte(chemin: Path) -> str:
    """Empreinte SHA-256 d'un fichier d'entrée (pour savoir de quelles données vient une table)."""
    h = hashlib.sha256()
    with chemin.open("rb") as f:
        for bloc in iter(lambda: f.read(1 << 20), b""):
            h.update(bloc)
    return h.hexdigest()


def version_du_code() -> dict[str, str | None]:
    """Version du paquet et commit du dépôt ; « -dirty » si un fichier suivi a changé depuis ce commit."""
    try:
        commit = subprocess.run(["git", "describe", "--always", "--dirty", "--abbrev=12"], cwd=RACINE,
                                capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):                 # hors d'un dépôt git (image Docker)
        commit = None
    return {"jumeau_cirano": __version__, "commit": commit}


def nom_relatif(chemin: Path) -> str:
    """Chemin relatif à la racine du dépôt quand le fichier s'y trouve, absolu sinon (autre réseau, tests)."""
    return chemin.relative_to(RACINE).as_posix() if chemin.is_relative_to(RACINE) else chemin.as_posix()


def lire_brut(chemin: Path) -> pd.DataFrame:
    """Fichier du MTQ, sans géométrie : identifiant, années et valeurs de chaque créneau."""
    brut = gpd.read_file(chemin, ignore_geometry=True)
    colonnes = ["ide_sectn_trafc"] + [c for c in brut.columns
                                      if c.startswith(tuple(f"{m}_annee_" for m in MESURES_LUES))
                                      or c.startswith(tuple(f"val_{m}_annee_" for m in MESURES_LUES))]
    return brut[colonnes]


def lire_complete(chemin: Path) -> pd.DataFrame:
    """Fichier complété par le Module 1, sans géométrie."""
    complete = gpd.read_file(chemin, ignore_geometry=True)
    colonnes = ["ide_sectn_trafc", "methode_djma", "methode_cam"] + [
        c for c in complete.columns if c.startswith(("val_djma_annee_", "val_cam_annee_"))]
    return complete[colonnes]


def construire(cr: ConfigReseau, bloc_km: float, echantillon: bool) -> dict[str, pd.DataFrame]:
    """Les cinq tables, à partir des fichiers désignés par la configuration du réseau."""
    brut = lire_brut(chemin_absolu(cr.entrees.sections))
    complete = lire_complete(chemin_absolu(cr.entrees.sections_completes))
    sections = charger_sections(cr)
    if echantillon:
        e = cr.echantillon
        centre = sections.geometry.centroid.to_crs("EPSG:4326")
        dedans = (centre.x >= e.lon_min) & (centre.x <= e.lon_max) & (centre.y >= e.lat_min) & (centre.y <= e.lat_max)
        sections = sections[dedans]
        brut = brut[brut["ide_sectn_trafc"].astype(str).isin(set(sections["ide_sectn_trafc"]))]
    rtss = gpd.read_file(chemin_absolu(cr.entrees.rtss), columns=["num_route", "cod_clasf_"])
    attributs = t.attributs_sections(sections, rtss, bloc_km, cr.sections.tampon_m)
    sections_annees = t.table_sections_annees(brut, complete, attributs)

    reseau = suffixer(chemin_absolu(cr.sortie), echantillon)
    if not reseau.exists():
        raise FileNotFoundError(f"{reseau} absent : lancer `jumeau reseau{' --echantillon' if echantillon else ''}`")
    liens = gpd.read_file(reseau, layer="liens_rtss").to_crs(CRS_BLOCS)
    noeuds = gpd.read_file(reseau, layer="noeuds_rtss")
    paires = paires_section_lien(sections, liens[~liens["raccord"]], cr.sections.tampon_m)
    aretes_sections = t.table_aretes_sections(paires, attributs)
    return {"sections_annees": sections_annees, "noeuds": t.table_noeuds(noeuds, liens, bloc_km),
            "aretes": t.table_aretes(liens, bloc_km), "aretes_sections": aretes_sections,
            "aretes_annees": t.etiquettes_aretes(aretes_sections, sections_annees)}


def controles(tables: dict[str, pd.DataFrame], brut_publiees: dict[str, int], seuil_jonction_m: float) -> dict:
    """Chiffres de contrôle écrits dans meta.json ; lève une erreur si une cible n'est pas une valeur publiée.

    `seuil_jonction_m` : tolérance de croisement du graphe ; au-delà, une jonction nouée est une estimation.
    """
    sa = tables["sections_annees"]
    t.verifier_cibles(sa)
    resultat: dict = {"sections": int(sa["ide_sectn_trafc"].nunique()),
                      "annees": sorted(int(a) for a in sa["annee"].unique()), "blocs": int(sa["bloc_50km"].nunique())}
    for nom, attendu in brut_publiees.items():
        cibles = int(sa[f"{nom}_cible"].notna().sum())
        if cibles != attendu:
            raise ValueError(f"{nom} : {cibles} cibles pour {attendu} valeurs publiées dans le fichier brut")
        resultat[nom] = {"cibles": cibles, "valeurs_ecrasees_par_la_completion": int(sa[f"{nom}_ecrase"].sum()),
                         "methodes": {m: int(n) for m, n in sa[f"{nom}_methode"].value_counts().items()},
                         "sections_avec_cible": int(sa.loc[sa[f"{nom}_mesure"], "ide_sectn_trafc"].nunique()),
                         "blocs_avec_cible": int(sa.loc[sa[f"{nom}_mesure"], "bloc_50km"].nunique())}
    les_deux = (sa["djma_n_mesures"] > 0) & (sa["pct_camions_n_mesures"] > 0)
    resultat["blocs_validation"] = int(sa.loc[les_deux, "bloc_50km"].nunique())

    # Exemples d'apprentissage (tables.exemples) : taille effective et colonnes laissées à l'utilisateur
    resultat["exemples"] = {}
    for cible in ("djma", "pct_camions", "camions"):
        ex = t.exemples(sa, cible)
        resultat["exemples"][cible] = {"lignes": int(len(ex)), "sections": int(ex["ide_sectn_trafc"].nunique()),
                                       "blocs": int(ex["bloc_50km"].nunique()), "colonnes": list(ex.columns)}
    resultat["exemples"]["djma"]["lignes_ou_djme_et_djmh_sont_publies"] = int(
        sa.loc[sa["djma_mesure"], list(t.SOEURS_DJMA)].notna().all(axis=1).sum())

    une = sa.drop_duplicates("ide_sectn_trafc")
    for colonne in ("type_route", "type_route_source"):
        resultat[colonne] = {str(k): int(v) for k, v in une[colonne].value_counts().items()}
    resultat["type_route_source_sections_avec_djma_publie"] = {
        str(k): int(v) for k, v in une.loc[une["djma_n_mesures"] > 0, "type_route_source"].value_counts().items()}

    # Graphe : nœuds noués par le projet, rattachement des sections, étiquettes des liens
    noeuds, ecart = tables["noeuds"], tables["noeuds"]["ecart_jonction_m"]
    estimees = ecart[ecart > seuil_jonction_m]
    resultat["noeuds_noues_par_le_projet"] = int(noeuds["jonction_nouee"].sum())
    resultat["seuil_jonction_estimee_m"] = seuil_jonction_m
    resultat["noeuds_jonction_estimee"] = int(len(estimees))
    resultat["ecart_jonction_m"] = {
        "max_aux_extremites_communes": arrondi(ecart[~noeuds["jonction_nouee"]].max(), 2),
        "jonctions_estimees": {"mediane": arrondi(estimees.median(), 1), "decile_9": arrondi(estimees.quantile(0.9), 1),
                               "max": arrondi(estimees.max(), 1)}}
    inc = tables["aretes_sections"].merge(tables["aretes"][["id_lien", "bloc_50km"]], on="id_lien",
                                          validate="many_to_one")
    resultat["sections_sur_le_graphe"] = int(inc["ide_sectn_trafc"].nunique())
    resultat["paires_lien_section"] = int(len(inc))
    resultat["paires_lien_section_de_blocs_differents"] = int((inc["bloc_section"] != inc["bloc_50km"]).sum())
    resultat["liens_par_section_mediane"] = float(inc.groupby("ide_sectn_trafc").size().median()) if len(inc) else 0.0
    aa = tables["aretes_annees"]
    resultat["aretes_avec_section"] = int(aa["id_lien"].nunique())
    resultat["aretes_avec_cible_djma"] = int(aa.loc[aa["djma_cible"].notna(), "id_lien"].nunique())
    resultat["aretes_etiquetees_par_annee"] = {
        nom: {str(a): int(v) for a, v in aa[aa[f"{nom}_cible"].notna()].groupby("annee")["id_lien"].nunique().items()}
        for nom in (*t.CIBLES.values(), "camions")}
    return resultat


def arrondi(valeur: float, chiffres: int) -> float | None:
    """Arrondi pour meta.json ; None pour une statistique vide (NaN n'est pas du JSON valide)."""
    return None if pd.isna(valeur) else round(float(valeur), chiffres)


def executer(args: argparse.Namespace) -> None:
    """Construit les tables d'apprentissage et les écrit dans le dossier de sortie."""
    debut = time.time()
    cfg = charger(args.config or DIR_CONFIGS / "tables.yaml", ConfigTables)
    cr = charger(cfg.reseau, ConfigReseau)
    echantillon = bool(args.echantillon)
    print(f"[1/3] tables{' — échantillon : ' + cr.echantillon.nom if echantillon else ' — tout le Québec'}")
    tables = construire(cr, cfg.bloc_km, echantillon)

    print("[2/3] contrôles")
    sa = tables["sections_annees"]
    brut = lire_brut(chemin_absolu(cr.entrees.sections))
    brut = brut[brut["ide_sectn_trafc"].isin(set(sa["ide_sectn_trafc"]))]
    publiees = {}
    for m, nom in t.CIBLES.items():
        colonnes = [c for c in brut.columns if c.startswith(f"val_{m}_annee_")]
        publiees[nom] = int((~pd.isna(t.valeurs(brut, colonnes))).sum())
    c = controles(tables, publiees, cr.graphe.tolerance_croisement_m)
    if not echantillon and c["blocs_validation"] != cfg.blocs_validation_attendus:
        raise ValueError(f"{c['blocs_validation']} blocs pour les sections de la validation spatiale, "
                         f"{cfg.blocs_validation_attendus} attendus : les blocs ou les données ont changé")
    for nom in t.CIBLES.values():
        print(f"      {nom:12s} {c[nom]['cibles']:>6,} cibles publiées, {c[nom]['sections_avec_cible']:>5,} sections, "
              f"{c[nom]['blocs_avec_cible']:>3} blocs ; {c[nom]['valeurs_ecrasees_par_la_completion']} écrasées")

    print("[3/3] écriture")
    dossier = chemin_absolu(cfg.dossier_sortie) / ("echantillon" if echantillon else "")
    dossier.mkdir(parents=True, exist_ok=True)
    for nom, table in tables.items():
        table.to_parquet(dossier / f"{nom}.parquet", index=False)
        print(f"      {nom:16s} {len(table):>8,} lignes, {table.shape[1]:>2} colonnes")
    entrees = [chemin_absolu(e) for e in (cr.entrees.sections, cr.entrees.sections_completes, cr.entrees.rtss)]
    entrees.append(suffixer(chemin_absolu(cr.sortie), echantillon))
    meta = {"genere_le": date.today().isoformat(), "code": version_du_code(),
            "echantillon": cr.echantillon.model_dump() if echantillon else None,
            "bloc_km": cfg.bloc_km, "crs": CRS_BLOCS, "tampon_rattachement_m": cr.sections.tampon_m,
            "tables": {nom: {"lignes": int(len(tb)), "colonnes": list(tb.columns)} for nom, tb in tables.items()},
            "entrees": {nom_relatif(e): empreinte(e) for e in entrees}, "controles": c}
    texte = json.dumps(meta, ensure_ascii=False, indent=2) + "\n"
    (dossier / "meta.json").write_text(texte, encoding="utf-8")
    if cfg.meta_publique and not echantillon:                        # mêmes agrégats, dans le dépôt
        publique = chemin_absolu(cfg.meta_publique)
        publique.parent.mkdir(parents=True, exist_ok=True)
        publique.write_text(texte, encoding="utf-8")
        print(f"      → {nom_relatif(publique)}")
    print(f"      → {dossier} ({time.time() - debut:.0f} s)")
