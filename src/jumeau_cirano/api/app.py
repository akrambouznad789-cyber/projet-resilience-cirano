"""
app.py
======
API HTTP du jumeau (FastAPI), façade de api/service.py.

    GET  /sante       le service tourne et quel modèle il sert
    GET  /modele      paramètres calés, IC à 95 %, métriques, validation croisée
    POST /affectation camions par jour sur chaque route (θ calé, ou modifié)
    POST /scenario    fermer des liaisons ou des routes : impacts et report du trafic
    GET  /criticite   classement précalculé des liaisons (ou des routes) les plus critiques

Le paquet est lu au démarrage depuis JUMEAU_ARTEFACT (défaut : outputs/jumeau/service).
`app.openapi()` ne lit rien : `jumeau openapi` exporte le contrat sans données.
Lancement : `jumeau api` ou `uvicorn jumeau_cirano.api.app:app`.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request

from jumeau_cirano import __version__
from jumeau_cirano.api.schemas import (
    MESURES,
    Affectation,
    DemandeAffectation,
    DemandeScenario,
    LigneCriticite,
    Modele,
    Sante,
    Scenario,
)
from jumeau_cirano.api.service import ErreurDemande, Service
from jumeau_cirano.config import RACINE

PAQUET_DEFAUT = RACINE / "outputs" / "jumeau" / "service"


@asynccontextmanager
async def demarrage(app: FastAPI) -> AsyncIterator[None]:
    app.state.service = Service.charger(Path(os.environ.get("JUMEAU_ARTEFACT", PAQUET_DEFAUT)))
    yield


app = FastAPI(
    title="Jumeau numérique du camionnage québécois (CIRANO)",
    version=__version__,
    description="Affectation calée des camions sur le réseau routier et scénarios de fermeture. Les réponses ne "
                "contiennent que des résultats par route, par liaison ou globaux. Montants en $ provisoires.",
    lifespan=demarrage,
)


def service(request: Request) -> Service:
    return request.app.state.service


ServiceDep = Annotated[Service, Depends(service)]


def protege(fonction, *args):
    try:
        return fonction(*args)
    except ErreurDemande as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@app.get("/sante", response_model=Sante)
def sante(s: ServiceDep) -> dict:
    return {"statut": "ok", "version": __version__, "objectif": s.meta["objectif"], "routes": len(s.art.routes),
            "liaisons": int(s.art.routes["ID_ARC"].nunique()), "itineraires": len(s.art.chemins),
            "genere_le": s.meta["genere_le"]}


@app.get("/modele", response_model=Modele)
def modele(s: ServiceDep) -> dict:
    c = s.calibration
    return {"objectif": c["objectif"], "parametres": c["parametres"], "ic95": c.get("ic95", {}),
            "metriques": c.get("metriques_finales", {}), "validation_croisee": c.get("validation_croisee", {}),
            "genere_le": c["genere_le"]}


@app.post("/affectation", response_model=Affectation)
def affectation(demande: DemandeAffectation, s: ServiceDep) -> dict:
    return protege(s.affecter, demande.theta.en_dict() if demande.theta else None)


@app.post("/scenario", response_model=Scenario)
def scenario(demande: DemandeScenario, s: ServiceDep) -> dict:
    return protege(s.fermer, demande.liaisons, demande.routes, demande.theta.en_dict() if demande.theta else None,
                   demande.seuil_report)


@app.get("/criticite", response_model=list[LigneCriticite])
def criticite(s: ServiceDep, scenario: Annotated[str, Query(pattern="^(liaison|route)$")] = "liaison",
              mesure: MESURES = "delta_heures_equivalentes",
              limite: Annotated[int, Query(ge=1, le=1000)] = 20) -> list[dict]:
    return s.classement(scenario, mesure, limite)
