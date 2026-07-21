"""
Nettoyage du projet QGIS pour le livrable final.

Renomme les groupes du layer-tree selon leur contenu réel, purge les
groupes legacy (couches + maplayers XML), et fixe le titre du projet.
Réécrit le .qgz en place (zip: .qgs modifié + styles.db inchangée).
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

PROJET_DIR = Path(__file__).resolve().parent.parent
QGZ_PATH = PROJET_DIR / "qgis" / "reseau-routier-graphe.qgz"

TITRE_PROJET = "Réseau routier — résilience CIRANO"

RENOMMAGE_GROUPES = {
    "DJMA": "DJMA — méthodes",
    "Attribution Djma méthodes & complétion": "Routage — liens & tracés",
    "Routage v4 — liens & tracés": "Routage — liens & tracés",
}

GROUPE_LEGACY = "Liens Route version algo"

# Migration des sources de données vers les fichiers/couches renommés (sans suffixe de version)
MIGRATION_SOURCES = {
    "../data/processed/debits_completes_v2.gpkg|layername=debits_completes_v2":
        "../data/processed/debits_completes.gpkg|layername=debits_completes",
    "../data/processed/graphe_routier_v4.gpkg|layername=arcs_enrichis_v4":
        "../data/processed/graphe_routier.gpkg|layername=arcs_enrichis",
    "../data/processed/graphe_routier_v4.gpkg|layername=trace_osrm_v4":
        "../data/processed/graphe_routier.gpkg|layername=trace_osrm",
    "../data/processed/graphe_routier_v4.gpkg|layername=trajets_segments_v4":
        "../data/processed/graphe_routier.gpkg|layername=trajets_segments",
    "../data/processed/graphe_routier_v4_djma.gpkg|layername=arcs_enrichis_v4_djma":
        "../data/processed/graphe_routier_djma.gpkg|layername=arcs_enrichis_djma",
}

# Étiquettes affichées dans le panneau des couches (cosmétique, sans impact sur les données)
LABELS_COUCHES = {
    "debits_completes_v2": "debits_completes",
    "graphe_routier_v4 — arcs_enrichis_v4": "graphe_routier — arcs_enrichis",
    "graphe_routier_v4 — trace_osrm_v4": "graphe_routier — trace_osrm",
    "graphe_routier_v4 — trajets_segments_v4": "graphe_routier — trajets_segments",
    "graphe_routier_v4_djma — arcs_enrichis_v4_djma_m1": "graphe_routier_djma — arcs_enrichis_djma_m1",
    "graphe_routier_v4_djma — arcs_enrichis_v4_djma_m2": "graphe_routier_djma — arcs_enrichis_djma_m2",
    "graphe_routier_v4_djma — arcs_enrichis_v4_djma_m3": "graphe_routier_djma — arcs_enrichis_djma_m3",
    "graphe_routier_v4_djma — arcs_enrichis_v4_djma_m4": "graphe_routier_djma — arcs_enrichis_djma_m4",
}


def extraire_qgz(qgz_path: Path, dest_dir: Path) -> tuple[Path, Path]:
    """Extrait le .qgs et la styles.db d'un .qgz vers dest_dir."""
    with zipfile.ZipFile(qgz_path) as z:
        noms = z.namelist()
        qgs_name = next(n for n in noms if n.endswith(".qgs"))
        db_name = next(n for n in noms if n.endswith(".db"))
        z.extractall(dest_dir)
    return dest_dir / qgs_name, dest_dir / db_name


def fixer_titre(root: ET.Element) -> None:
    root.set("projectname", TITRE_PROJET)
    for title_el in root.findall("./title"):
        title_el.text = TITRE_PROJET
        break


def collecter_layer_ids(group: ET.Element) -> set[str]:
    ids = set()
    for layer_el in group.iter("layer-tree-layer"):
        ids.add(layer_el.get("id"))
    return ids


def renommer_et_purger_groupes(root: ET.Element) -> set[str]:
    """Renomme les groupes ciblés et retire le groupe legacy.

    Retourne les layer-id des couches supprimées (pour purge des maplayers).
    """
    layer_tree = root.find(".//layer-tree-group")
    ids_a_supprimer: set[str] = set()

    for group in list(layer_tree.findall("layer-tree-group")):
        nom = group.get("name")
        if nom in RENOMMAGE_GROUPES:
            group.set("name", RENOMMAGE_GROUPES[nom])
        elif nom == GROUPE_LEGACY:
            ids_a_supprimer |= collecter_layer_ids(group)
            layer_tree.remove(group)

    return ids_a_supprimer


def migrer_sources_donnees(root: ET.Element) -> int:
    """Réécrit les <datasource> pointant vers les anciens noms de fichiers/couches.

    Le préfixe est comparé plutôt que l'égalité stricte : certaines couches
    portent un suffixe supplémentaire (ex: |subset="ID_ARC"=153) conservé tel quel.
    """
    n_migres = 0
    for datasource_el in root.iter("datasource"):
        texte = datasource_el.text or ""
        for ancien, nouveau in MIGRATION_SOURCES.items():
            if texte.startswith(ancien):
                datasource_el.text = nouveau + texte[len(ancien):]
                n_migres += 1
                break
    return n_migres


def renommer_labels_couches(root: ET.Element) -> int:
    """Renomme les étiquettes affichées (name= des layer-tree-layer, <layername> des maplayer).

    Ne touche jamais l'attribut id= (référencé ailleurs dans le projet).
    """
    n_renomme = 0
    for layer_tree_el in root.iter("layer-tree-layer"):
        nom = layer_tree_el.get("name")
        if nom in LABELS_COUCHES:
            layer_tree_el.set("name", LABELS_COUCHES[nom])
            n_renomme += 1
    for layername_el in root.iter("layername"):
        if layername_el.text in LABELS_COUCHES:
            layername_el.text = LABELS_COUCHES[layername_el.text]
            n_renomme += 1
    return n_renomme


def purger_maplayers(root: ET.Element, ids_a_supprimer: set[str]) -> None:
    """Retire les <maplayer> correspondant aux couches legacy retirées du layer-tree."""
    for parent in root.iter():
        for maplayer in list(parent.findall("maplayer")):
            id_el = maplayer.find("id")
            if id_el is not None and id_el.text in ids_a_supprimer:
                parent.remove(maplayer)


def afficher_arbre(qgs_path: Path) -> None:
    tree = ET.parse(qgs_path)
    root = tree.getroot()
    layer_tree = root.find(".//layer-tree-group")

    def walk(node, depth=0):
        for child in node:
            if child.tag == "layer-tree-group":
                print("  " * depth + f"[GROUP] {child.get('name')}")
                walk(child, depth + 1)
            elif child.tag == "layer-tree-layer":
                print("  " * depth + f"- {child.get('name')}")

    walk(layer_tree)


def reconstruire_qgz(qgs_path: Path, db_path: Path, qgz_out: Path) -> None:
    with zipfile.ZipFile(qgz_out, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(qgs_path, arcname=qgs_path.name)
        z.write(db_path, arcname=db_path.name)


def main() -> None:
    if not QGZ_PATH.exists():
        raise FileNotFoundError(f"Introuvable : {QGZ_PATH}")

    print(f"Nettoyage de {QGZ_PATH.name}")
    print(f"Titre projet : {TITRE_PROJET}")
    print(f"Renommage groupes : {RENOMMAGE_GROUPES}")
    print(f"Purge groupe legacy : {GROUPE_LEGACY}")

    tmp_dir = QGZ_PATH.parent / "_tmp_nettoyage_qgis"
    tmp_dir.mkdir(exist_ok=True)
    try:
        qgs_path, db_path = extraire_qgz(QGZ_PATH, tmp_dir)

        tree = ET.parse(qgs_path)
        root = tree.getroot()

        fixer_titre(root)
        ids_a_supprimer = renommer_et_purger_groupes(root)
        purger_maplayers(root, ids_a_supprimer)
        n_migres = migrer_sources_donnees(root)
        n_renomme = renommer_labels_couches(root)

        tree.write(qgs_path, encoding="UTF-8", xml_declaration=True)

        sauvegarde = QGZ_PATH.with_suffix(".qgz.bak")
        shutil.copy2(QGZ_PATH, sauvegarde)
        print(f"\nSauvegarde de l'original : {sauvegarde.name}")

        reconstruire_qgz(qgs_path, db_path, QGZ_PATH)
        print(f"Projet réécrit : {QGZ_PATH}")

        print(f"\n{len(ids_a_supprimer)} couche(s) legacy purgée(s) du XML.")
        print(f"{n_migres} source(s) de données migrée(s) vers les noms sans suffixe de version.")
        print(f"{n_renomme} étiquette(s) de couche renommée(s).")
        print("\nArbre de groupes/couches final :")
        afficher_arbre(qgs_path)

    finally:
        shutil.rmtree(tmp_dir)


if __name__ == "__main__":
    main()
