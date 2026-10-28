"""
paquet.py
=========
Commandes de service du jumeau.

`jumeau exporter [--objectif configs/objectifs/<nom>.yaml]`
    paquet de service dans outputs/jumeau/service/ : artefact, calibration, liaisons, frontières,
    criticité. C'est le dossier à monter (en lecture seule) dans le conteneur de l'API.
    Il dérive de l'ENR : il reste confidentiel, comme tout outputs/.
`jumeau openapi`
    docs/api/openapi.json : le contrat d'intégration de l'API (aucune donnée lue).
`jumeau api [--hote 127.0.0.1] [--port 8000]`
    lance l'API sur le paquet désigné par JUMEAU_ARTEFACT (défaut : outputs/jumeau/service).
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import date
from pathlib import Path

import pandas as pd

from jumeau_cirano.config import (
    DIR_CONFIGS,
    RACINE,
    ConfigModele,
    ConfigReseau,
    ConfigScenarios,
    Objectif,
    charger,
    chemin_absolu,
)

COLONNES_LIAISONS = ["ID_ARC", "ID_A", "ID_B", "VILLE_A", "VILLE_B"]


def ecrire_paquet(sortie: Path, artefact: Path, calibration: dict, liaisons: pd.DataFrame, frontieres: list[str],
                  objectif: str, cout_horaire: float, valeur_tonne: float, criticite: Path | None = None) -> Path:
    """Assemble un paquet de service (voir api/service.py pour son contenu)."""
    sortie.mkdir(parents=True, exist_ok=True)
    if artefact.resolve() != (sortie / "artefact").resolve():
        shutil.copytree(artefact, sortie / "artefact", dirs_exist_ok=True)
    (sortie / "calibration.json").write_text(json.dumps(calibration, ensure_ascii=False, indent=2), encoding="utf-8")
    liaisons[COLONNES_LIAISONS].to_parquet(sortie / "liaisons.parquet", index=False)
    meta = {"objectif": objectif, "frontieres": sorted(frontieres), "cout_horaire": cout_horaire,
            "valeur_tonne": valeur_tonne, "genere_le": date.today().isoformat()}
    (sortie / "service.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    if criticite is not None and criticite.exists():
        shutil.copyfile(criticite, sortie / "criticite.csv")
    return sortie


def executer_export(args: argparse.Namespace) -> None:
    from jumeau_cirano.donnees.liaisons import charger_liaisons, charger_noeuds

    cfg = charger(args.config or DIR_CONFIGS / "scenarios.yaml", ConfigScenarios)
    cm = charger(cfg.modele, ConfigModele)
    cr = charger(cm.reseau, ConfigReseau)
    objectif = charger(args.objectif or cfg.objectif, Objectif)
    dossier = chemin_absolu(cm.dossier_sortie)
    calibration = json.loads((dossier / f"calibration_{objectif.nom}.json").read_text(encoding="utf-8"))
    frontieres = charger_noeuds(cr).query("TYPE.str.startswith('frontiere')", engine="python")["ID"].tolist()
    sortie = ecrire_paquet(dossier / "service", dossier / "artefact", calibration, charger_liaisons(cr), frontieres,
                           objectif.nom, cfg.cout_horaire_camion, cfg.valeur_par_tonne,
                           dossier / f"criticite_{objectif.nom}.csv")
    print(f"→ {sortie} (à monter en lecture seule : -v {sortie}:/artefact:ro)")


def executer_openapi(args: argparse.Namespace) -> None:
    from jumeau_cirano.api.app import app

    cible = RACINE / "docs" / "api" / "openapi.json"
    cible.parent.mkdir(parents=True, exist_ok=True)
    cible.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"→ {cible}")


def executer_api(args: argparse.Namespace) -> None:
    import uvicorn

    uvicorn.run("jumeau_cirano.api.app:app", host=args.hote, port=int(args.port))
