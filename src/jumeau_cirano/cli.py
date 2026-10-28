"""
cli.py
======
Point d'entrée en ligne de commande : `python -m jumeau_cirano <commande>` ou `jumeau <commande>`.

Chaque commande correspond à une étape de la chaîne et lit sa configuration YAML.
Les modules sont importés à la demande : `jumeau reseau` ne charge pas PyTorch.
"""

from __future__ import annotations

import argparse
import importlib

from jumeau_cirano import __version__

# commande → (aide, "module:fonction(args)", options propres [(drapeau, paramètres argparse)])
COMMANDES: dict[str, tuple[str, str, list[tuple[str, dict]]]] = {
    "reseau": ("graphe RTSS, routes alternatives, sections, point de contrôle",
               "jumeau_cirano.reseau.commande:executer",
               [("--echantillon", {"action": "store_true", "help": "échantillon géographique de la config"})]),
    "preparer": ("artefact du modèle : demande ENR, itinéraires, postes, sections",
                 "jumeau_cirano.calibration.commande:executer_preparation", []),
    "calibrer": ("validation croisée, calibration, bootstrap, suivi MLflow",
                 "jumeau_cirano.calibration.commande:executer",
                 [("--objectif", {"help": "configs/objectifs/<nom>.yaml (défaut : celui de la config)"}),
                  ("--rapide", {"action": "store_true", "help": "calibration finale seulement"})]),
}


def main(argv: list[str] | None = None) -> None:
    """Analyse la ligne de commande et lance la commande demandée."""
    analyseur = argparse.ArgumentParser(prog="jumeau", description="Jumeau numérique du camionnage québécois")
    analyseur.add_argument("--version", action="version", version=f"jumeau-cirano {__version__}")
    sous = analyseur.add_subparsers(dest="commande", required=True)
    for nom, (aide, _, options) in COMMANDES.items():
        p = sous.add_parser(nom, help=aide)
        p.add_argument("--config", help="fichier YAML (défaut : celui de configs/ pour cette commande)")
        for drapeau, parametres in options:
            p.add_argument(drapeau, **parametres)
    args = analyseur.parse_args(argv)
    module, fonction = COMMANDES[args.commande][1].split(":")
    getattr(importlib.import_module(module), fonction)(args)
