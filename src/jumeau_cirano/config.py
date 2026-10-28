"""
config.py
=========
Configuration du jumeau : fichiers YAML de `configs/`, validés par pydantic.

Chaque brique déclare son schéma ici. Les chemins de données sont dans les
YAML, pas dans le code : on peut substituer un autre réseau (autre
reseau_arcs.gpkg) et relancer la chaîne sans modifier une ligne de Python.
"""

from __future__ import annotations

from pathlib import Path
from typing import TypeVar

import yaml
from pydantic import BaseModel, ConfigDict

RACINE = Path(__file__).resolve().parents[2]
DIR_CONFIGS = RACINE / "configs"


class Schema(BaseModel):
    """Base commune : clés inconnues refusées (une faute de frappe ne passe pas en silence)."""

    model_config = ConfigDict(extra="forbid", frozen=True)


S = TypeVar("S", bound=Schema)


def chemin_absolu(chemin: str | Path) -> Path:
    """Chemin relatif à la racine du dépôt, ou absolu tel quel."""
    p = Path(chemin)
    return p if p.is_absolute() else RACINE / p


def charger(chemin: str | Path, schema: type[S]) -> S:
    """Lit un YAML et le valide contre `schema` (erreur explicite si une clé manque ou déborde)."""
    p = chemin_absolu(chemin)
    with p.open(encoding="utf-8") as f:
        brut = yaml.safe_load(f) or {}
    return schema.model_validate(brut)
