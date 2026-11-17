"""
actualiser_valeurs_liaisons.py
==============================
Projet CIRANO — Module 1 : reporter les valeurs complétées sur les liaisons, sans refaire le routage.

Le routage (algo_jointure_routes_liens.py) interroge le serveur public OSRM : le relancer pourrait
changer les tracés, donc les sections rattachées à chaque liaison. Quand seule la complétion change
(debits_completes.gpkg), ce script garde les rattachements et les tracés, et réécrit seulement les
valeurs 2025 complétées, au format exact du routage (« valeur@année », DJMA tronqué à l'entier).

Pseudo-code
-----------
1. valeurs_par_section(fichier)   ide_sectn_trafc → valeurs 2025 et chaînes « valeur@année » du routage
2. chaines_liaisons(arcs, v)      ids_segs_djma_val et ids_segs_djma_val_cam recalculés liaison par liaison
3. --reference ancien.gpkg        contrôle : avec le fichier complété qui a servi au routage, les chaînes et
                                  les valeurs recalculées sont identiques à celles du graphe, sinon arrêt
4. ecrire()                       arcs_enrichis (2 colonnes), trajets_segments (djma_val, cam_val),
                                  trace_osrm recopiée → fichier temporaire relu et comparé, puis remplacé

Entrées : data/processed/graphe_routier.gpkg, data/processed/debits_completes.gpkg
Sortie  : data/processed/graphe_routier.gpkg (mêmes couches, mêmes rattachements, mêmes tracés)

Usage : ~/.venvs/portfolio/bin/python scripts/actualiser_valeurs_liaisons.py [--reference ancien_debits.gpkg]
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

RACINE         = Path(__file__).resolve().parent.parent
FICHIER_GRAPHE = RACINE / "data" / "processed" / "graphe_routier.gpkg"
FICHIER_DEBITS = RACINE / "data" / "processed" / "debits_completes.gpkg"
COUCHES        = ("arcs_enrichis", "trajets_segments", "trace_osrm")
COLS_CHAINES   = ["ids_segs_djma_val", "ids_segs_djma_val_cam"]
COLS_VALEURS   = ["djma_val", "cam_val"]


def chaine(valeur: float, annee: float, entier: bool) -> str:
    """Une valeur au format du routage : « 1780@2025 » (DJMA tronqué), « 11.7@2025 » (% camions), « NA »."""
    if pd.isna(valeur):
        return "NA"
    a = int(annee) if pd.notna(annee) else "?"
    return f"{int(valeur)}@{a}" if entier else f"{valeur}@{a}"


def valeurs_par_section(fichier: Path) -> pd.DataFrame:
    """Valeurs 2025 complétées de chaque section et leurs chaînes, indexées par ide_sectn_trafc."""
    d = gpd.read_file(fichier, ignore_geometry=True, columns=[
        "ide_sectn_trafc", "val_djma_annee_1", "djma_annee_1", "val_cam_annee_1", "cam_annee_1"])
    if not d["ide_sectn_trafc"].is_unique:
        raise ValueError(f"{fichier.name} : identifiants de section en double")
    d = d.set_index(d["ide_sectn_trafc"].astype(int))
    djma = pd.to_numeric(d["val_djma_annee_1"], errors="coerce")
    cam = pd.to_numeric(d["val_cam_annee_1"], errors="coerce")
    a_djma = pd.to_numeric(d["djma_annee_1"], errors="coerce")
    a_cam = pd.to_numeric(d["cam_annee_1"], errors="coerce")
    return pd.DataFrame({
        "djma_val": djma, "cam_val": cam,
        "djma_txt": [chaine(v, a, True) for v, a in zip(djma.to_numpy(), a_djma.to_numpy(), strict=True)],
        "cam_txt": [chaine(v, a, False) for v, a in zip(cam.to_numpy(), a_cam.to_numpy(), strict=True)],
    }, index=d.index)


def chaines_liaisons(arcs: pd.DataFrame, v: pd.DataFrame) -> pd.DataFrame:
    """ids_segs_djma_val et ids_segs_djma_val_cam recalculés à partir des sections rattachées (même ordre)."""
    def recomposer(ids: object, colonne: str) -> str | None:
        if not isinstance(ids, str) or not ids:
            return None
        cles = [int(s) for s in ids.split("|")]
        manquantes = [s for s in cles if s not in v.index]
        if manquantes:
            raise ValueError(f"sections rattachées absentes du fichier complété : {manquantes[:5]}")
        return "|".join(v.loc[cles, colonne])

    return pd.DataFrame({
        "ids_segs_djma_val": [recomposer(i, "djma_txt") for i in arcs["ids_segs_djma_id"]],
        "ids_segs_djma_val_cam": [recomposer(i, "cam_txt") for i in arcs["ids_segs_djma_id"]],
    }, index=arcs.index)


def controler_reference(couches: dict[str, gpd.GeoDataFrame], reference: Path) -> None:
    """Avec le fichier complété qui a servi au routage, le recalcul doit redonner exactement le graphe actuel."""
    v = valeurs_par_section(reference)
    arcs, segs = couches["arcs_enrichis"], couches["trajets_segments"]
    recalcul = chaines_liaisons(arcs, v)
    ecarts = 0
    for c in COLS_CHAINES:
        ecarts += int((recalcul[c].fillna("∅") != arcs[c].fillna("∅")).sum())
    for c in COLS_VALEURS:
        attendu = v.loc[segs["ide_sectn_trafc"].astype(int), c].to_numpy()
        ecarts += int((~np.isclose(attendu, segs[c].to_numpy(dtype=float), rtol=0, atol=1e-9, equal_nan=True)).sum())
    n = int(arcs["ids_segs_djma_id"].fillna("").str.len().gt(0).sum())
    print(f"  Contrôle avec {reference.name} : {ecarts} écart sur {n} liaisons et {len(segs)} rattachements")
    if ecarts:
        raise ValueError("le recalcul ne reproduit pas le graphe actuel : arrêt, rien n'est écrit")


def ecrire(couches: dict[str, gpd.GeoDataFrame], cible: Path) -> None:
    """Écrit les couches dans un fichier temporaire, le relit et le compare, puis remplace la cible."""
    tmp = cible.with_name(cible.stem + ".tmp.gpkg")
    if tmp.exists():
        tmp.unlink()
    for nom in COUCHES:
        couches[nom].to_file(tmp, layer=nom, driver="GPKG")
    for nom in COUCHES:
        relu, ecrit = gpd.read_file(tmp, layer=nom), couches[nom]
        if list(relu.columns) != list(ecrit.columns) or len(relu) != len(ecrit):
            raise ValueError(f"{nom} : relecture différente (colonnes ou lignes) ; {tmp.name} gardé pour examen")
        if not relu.geometry.geom_equals_exact(ecrit.geometry, tolerance=0).all():
            raise ValueError(f"{nom} : géométries modifiées à l'écriture ; {tmp.name} gardé pour examen")
    os.replace(tmp, cible)


def main() -> None:
    """Réécrit les valeurs 2025 complétées des liaisons à partir de debits_completes.gpkg."""
    parser = argparse.ArgumentParser(description="Reporter les valeurs complétées sur les liaisons (sans OSRM).")
    parser.add_argument("--reference", type=Path,
                        help="fichier complété qui a servi au routage : contrôle de non-régression avant d'écrire")
    args = parser.parse_args()

    print("[1/4] lecture du graphe routier")
    couches = {nom: gpd.read_file(FICHIER_GRAPHE, layer=nom) for nom in COUCHES}
    if args.reference:
        print("[2/4] contrôle de non-régression")
        controler_reference(couches, args.reference)
    else:
        print("[2/4] contrôle de non-régression : sauté (pas de --reference)")

    print("[3/4] nouvelles valeurs")
    v = valeurs_par_section(FICHIER_DEBITS)
    arcs, segs = couches["arcs_enrichis"].copy(), couches["trajets_segments"].copy()
    nouvelles = chaines_liaisons(arcs, v)
    changees = (nouvelles[COLS_CHAINES].fillna("∅") != arcs[COLS_CHAINES].fillna("∅")).any(axis=1)
    arcs[COLS_CHAINES] = nouvelles[COLS_CHAINES]
    cles = segs["ide_sectn_trafc"].astype(int)
    avant = segs[COLS_VALEURS].to_numpy(dtype=float)
    for c in COLS_VALEURS:
        segs[c] = v.loc[cles, c].to_numpy()
    n_segs = int((~np.isclose(avant, segs[COLS_VALEURS].to_numpy(dtype=float), rtol=0, atol=1e-9)).any(axis=1).sum())
    print(f"  {int(changees.sum())} liaisons sur {len(arcs)} et {n_segs} rattachements sur {len(segs)} changent de valeur")

    print("[4/4] écriture")
    ecrire({**couches, "arcs_enrichis": arcs, "trajets_segments": segs}, FICHIER_GRAPHE)
    print(f"→ {FICHIER_GRAPHE.relative_to(RACINE)} (rattachements et tracés inchangés)")


if __name__ == "__main__":
    main()
