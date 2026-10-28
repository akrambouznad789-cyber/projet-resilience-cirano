"""
schemas.py
==========
Schémas pydantic des requêtes et des réponses de l'API. Exportés dans docs/api/openapi.json
(`jumeau openapi`) : c'est le contrat d'intégration avec l'application web officielle.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MESURES = Literal["delta_heures_equivalentes", "delta_heures_meilleur", "delta_camions_heures", "delta_camions_km",
                  "trajets_non_desservis", "cout_perturbation", "camions_sur_liaison"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Parametres(Strict):
    """Paramètres à changer par rapport au modèle calé ; ceux qui manquent gardent leur valeur calée."""

    mu: float | None = Field(None, gt=0, description="dispersion du logit (1/min)")
    beta_hors_autoroute: float | None = Field(None, description="minutes équivalentes par km hors autoroute")
    beta_estime: float | None = Field(None, description="minutes par km de raccord ou d'accès estimé")
    croissance: float | None = Field(None, gt=0, description="g : camions 2025 par trajet ENR 2006-2007")
    fond_10: float | None = Field(None, ge=0, description="camions par jour absents de l'ENR, autoroutes")
    fond_20: float | None = Field(None, ge=0, description="idem, nationales")
    fond_30: float | None = Field(None, ge=0, description="idem, régionales")
    fond_40: float | None = Field(None, ge=0, description="idem, collectrices")
    fond_autre: float | None = Field(None, ge=0, description="idem, autres classes")

    def en_dict(self) -> dict[str, float]:
        return {k: v for k, v in self.model_dump().items() if v is not None}


class Sante(BaseModel):
    statut: Literal["ok"]
    version: str
    objectif: str
    routes: int
    liaisons: int
    itineraires: int
    genere_le: str


class Modele(BaseModel):
    objectif: dict
    parametres: dict[str, float]
    ic95: dict[str, list[float]]
    metriques: dict[str, float]
    validation_croisee: dict
    genere_le: str


class DemandeAffectation(Strict):
    theta: Parametres | None = None


class FluxRoute(BaseModel):
    ID_ROUTE: str
    ID_ARC: int
    camions_jour: float = Field(description="camions par jour en 2025")


class Affectation(BaseModel):
    theta: dict[str, float]
    metriques: dict[str, float] = Field(description="sur les sections mesurées en 2025 et l'enquête")
    routes: list[FluxRoute]


class DemandeScenario(Strict):
    liaisons: list[int] = Field(default_factory=list, description="ID_ARC des liaisons fermées (toutes leurs routes)")
    routes: list[str] = Field(default_factory=list, description="ID_ROUTE des routes fermées, en plus")
    theta: Parametres | None = None
    seuil_report: float = Field(0.5, ge=0, description="reports plus petits (camions par jour) omis")


class Impacts(BaseModel):
    camions_sur_liaison: float = Field(description="camions par an qui empruntaient ce qui ferme")
    delta_heures_equivalentes: float = Field(description="heures équivalentes perdues par an (logsum) : classement")
    delta_heures_meilleur: float = Field(description="par an, sur le meilleur itinéraire seulement")
    delta_camions_heures: float = Field(description="variation des camions-heures par an (temps moyen, descriptif)")
    delta_camions_km: float
    trajets_non_desservis: float = Field(description="trajets par an sans aucun chemin (coupure)")
    tonnes_sur_liaison: float
    valeur_sur_liaison: float = Field(description="$ par an, valeur par tonne PROVISOIRE")
    cout_perturbation: float = Field(description="$ par an, coût horaire PROVISOIRE")


class Report(BaseModel):
    ID_ROUTE: str
    delta_camions_jour: float


class Scenario(BaseModel):
    fermees: list[str]
    theta_modifie: bool
    impacts: Impacts
    reports: list[Report] = Field(description="du plus grand au plus petit en valeur absolue")


class LigneCriticite(BaseModel):
    scenario: str
    ID_ARC: int
    ID_ROUTE: str
    VILLE_A: str
    VILLE_B: str
    rang: float | None = None
    coupure: bool
    camions_sur_liaison: float
    delta_heures_equivalentes: float
    delta_heures_meilleur: float
    delta_camions_km: float
    trajets_non_desservis: float
    cout_perturbation: float
