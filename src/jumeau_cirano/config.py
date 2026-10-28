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


# ---------------------------------------------------------------------------
# Réseau (configs/reseau.yaml)
# ---------------------------------------------------------------------------

class EntreesReseau(Schema):
    rtss: str
    liaisons: str
    sections: str
    traces_osrm: str
    djma: str
    sections_completes: str


class ParamGraphe(Schema):
    statuts_gardes: list[str]
    classes_exclues: list[str]
    tolerance_noeud_m: float
    tolerance_croisement_m: float
    tolerance_jonction_m: float
    rattachement_max_km: float
    raccord_municipal_max_km: float
    facteur_detour_raccord: float
    vitesses_kmh: dict[str, float]


class ParamAcces(Schema):
    routes_max: int
    rayon_km: float
    facteur_detour: float
    vitesse_kmh: float


class Emprise(Schema):
    nom: str
    lon_min: float
    lat_min: float
    lon_max: float
    lat_max: float


class ParamControle(Schema):
    seuil_interurbain_km: float
    ratio_longueur_max: float
    part_liaisons_min: float
    tampon_recouvrement_m: float
    recouvrement_min: float


class ParamAlternatives(Schema):
    k: int
    facteur_penalite: float
    detour_max: float
    recouvrement_max: float
    exclusion_noeud_tiers_km: float


class ParamSections(Schema):
    tampon_m: float
    recouvrement_min: float


class ConfigReseau(Schema):
    entrees: EntreesReseau
    sortie: str
    rapport: str
    crs: str
    graphe: ParamGraphe
    acces: ParamAcces
    echantillon: Emprise
    controle: ParamControle
    alternatives: ParamAlternatives
    sections: ParamSections


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


# ---------------------------------------------------------------------------
# Modèle (configs/modele.yaml, configs/objectifs/*.yaml)
# ---------------------------------------------------------------------------

class ParamChemins(Schema):
    k_par_paire: int
    detour_max: float
    max_par_itineraire: int
    vitesse_hors_qc_kmh: float
    detour_hors_qc: float


class ParametresModele(Schema):
    mu: float
    beta_hors_autoroute: float
    beta_estime: float
    beta_frontiere: float
    croissance: float
    fond_par_classe: dict[str, float]
    erreur_postes: float


class ParamCalibration(Schema):
    objectif: str
    pas_adam: float
    iterations_adam: int
    iterations_lbfgs: int
    plis: int
    bloc_km: float
    bootstrap: int
    graine: int
    regularisation: float


class ConfigModele(Schema):
    reseau: str
    dossier_sortie: str
    chemins: ParamChemins
    parametres_initiaux: ParametresModele
    calibration: ParamCalibration


class Objectif(Schema):
    """Ce qu'on vise : le poids de chaque source d'observation dans la perte."""

    nom: str
    poids_comptages: float
    poids_enquete: float


class ConfigScenarios(Schema):
    modele: str
    objectif: str
    valeur_par_tonne: float
    cout_horaire_camion: float
    robustesse_tirages: int
