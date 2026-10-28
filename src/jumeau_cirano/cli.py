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

# commande → (aide, "module:fonction(args)")
COMMANDES: dict[str, tuple[str, str]] = {
    "reseau": ("graphe RTSS, rattachement des liaisons, point de contrôle",
               "jumeau_cirano.reseau.commande:executer"),
}


def main(argv: list[str] | None = None) -> None:
    """Analyse la ligne de commande et lance la commande demandée."""
    analyseur = argparse.ArgumentParser(prog="jumeau", description="Jumeau numérique du camionnage québécois")
    analyseur.add_argument("--version", action="version", version=f"jumeau-cirano {__version__}")
    sous = analyseur.add_subparsers(dest="commande", required=True)
    for nom, (aide, _) in COMMANDES.items():
        p = sous.add_parser(nom, help=aide)
        p.add_argument("--config", help="fichier YAML (défaut : celui de configs/ pour cette commande)")
        p.add_argument("--echantillon", action="store_true",
                       help="travailler sur l'échantillon géographique de la config")
    args = analyseur.parse_args(argv)
    module, fonction = COMMANDES[args.commande][1].split(":")
    getattr(importlib.import_module(module), fonction)(args)
