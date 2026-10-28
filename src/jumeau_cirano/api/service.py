"""
service.py
==========
Le jumeau en service : un paquet (dossier monté en lecture seule, variable JUMEAU_ARTEFACT)
→ modèle calé, affectation et fermetures à la demande. Indépendant de HTTP : app.py n'en
est qu'une façade, et l'application officielle peut l'importer directement.

Contenu du paquet (écrit par `jumeau exporter`, ou par api/synthetique.py pour la démonstration) :
    artefact/          tableaux du modèle (lus par modele.tenseurs.charger_artefact)
    liaisons.parquet   ID_ARC, ID_A, ID_B, VILLE_A, VILLE_B
    calibration.json   paramètres calés, IC à 95 %, métriques, validation croisée
    service.json       objectif, points frontières, coût horaire, valeur par tonne, date
    criticite.csv      classement précalculé par `jumeau scenarios` (facultatif)

Pseudo-code
-----------
charger(dossier)         lit le paquet ; contexte calé (même calcul que `jumeau scenarios`)
valeurs(θ partiel)       θ calé, mis à jour par les seules clés fournies (clé inconnue : erreur)
contexte(θ)              contexte du scénario pour θ ; les 8 derniers sont gardés en cache
affecter(θ)              camions par jour sur chaque route ; métriques sur les sections mesurées
fermer(liaisons, routes, θ)   impacts annuels et report de trafic par route (resilience.fermer_avec_flux)

Comme les pages publiques, le service ne renvoie que des résultats par route, par liaison ou
globaux : jamais de flux entre lieux. Le paquet, lui, dérive de l'ENR et reste confidentiel.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd
import torch

from jumeau_cirano.calibration.validation import metriques
from jumeau_cirano.modele.affectation import ModeleAffectation
from jumeau_cirano.modele.tenseurs import Artefact, charger_artefact
from jumeau_cirano.reseau.chemins import graphe_des_routes
from jumeau_cirano.scenarios.resilience import Contexte, fermer_avec_flux


class ErreurDemande(ValueError):
    """Demande invalide (paramètre, liaison ou route inconnus) : 422 côté HTTP."""


@dataclass
class Service:
    dossier: Path
    calibration: dict
    meta: dict
    liaisons: pd.DataFrame
    criticite: pd.DataFrame | None
    art: Artefact
    paires: pd.DataFrame
    h: nx.DiGraph
    frontieres: set[str]
    cale: Contexte
    _cache: OrderedDict = field(default_factory=OrderedDict, repr=False)

    @classmethod
    def charger(cls, dossier: Path) -> Service:
        dossier = Path(dossier)
        if not (dossier / "artefact").is_dir():
            raise FileNotFoundError(f"paquet du jumeau introuvable : {dossier} (variable JUMEAU_ARTEFACT)")
        calibration = json.loads((dossier / "calibration.json").read_text(encoding="utf-8"))
        meta = json.loads((dossier / "service.json").read_text(encoding="utf-8"))
        art = charger_artefact(dossier / "artefact")
        paires = pd.read_parquet(dossier / "artefact" / "paires.parquet")
        liaisons = pd.read_parquet(dossier / "liaisons.parquet")
        f = dossier / "criticite.csv"
        criticite = pd.read_csv(f, dtype={"ID_ROUTE": str}, keep_default_na=False) if f.exists() else None
        h = graphe_des_routes(art.routes, liaisons)
        frontieres = set(meta["frontieres"])
        modele = ModeleAffectation.depuis_valeurs(calibration["parametres"], art.frontieres)
        cale = Contexte.creer(art, modele, h, frontieres, paires)
        return cls(dossier, calibration, meta, liaisons, criticite, art, paires, h, frontieres, cale)

    # --- Paramètres ---
    def valeurs(self, theta: dict[str, float] | None) -> dict[str, float]:
        v = dict(self.calibration["parametres"])
        for cle, x in (theta or {}).items():
            if cle not in v:
                raise ErreurDemande(f"paramètre inconnu : {cle}")
            v[cle] = float(x)
        return v

    def contexte(self, theta: dict[str, float] | None) -> Contexte:
        v = self.valeurs(theta)
        if v == self.calibration["parametres"]:
            return self.cale
        cle = json.dumps(v, sort_keys=True)
        if cle not in self._cache:
            modele = ModeleAffectation.depuis_valeurs(v, self.art.frontieres)
            self._cache[cle] = Contexte.creer(self.art, modele, self.h, self.frontieres, self.paires)
            while len(self._cache) > 8:
                self._cache.popitem(last=False)
        self._cache.move_to_end(cle)
        return self._cache[cle]

    # --- Calculs ---
    def affecter(self, theta: dict[str, float] | None = None) -> dict:
        ctx = self.contexte(theta)
        n_obs, n_grp = len(self.art.observees), int(self.art.enquete.n.shape[0])
        with torch.no_grad():
            m = metriques(ctx.modele, self.art, np.ones(n_obs, dtype=bool), np.ones(n_grp, dtype=bool))
        routes = self.art.routes
        return {"theta": self.valeurs(theta), "metriques": m,
                "routes": [{"ID_ROUTE": r, "ID_ARC": int(a), "camions_jour": float(f)}
                           for r, a, f in zip(routes["ID_ROUTE"], routes["ID_ARC"], ctx.flux_ref, strict=True)]}

    def routes_fermees(self, liaisons: list[int], routes: list[str]) -> set[str]:
        r = self.art.routes
        inconnues = sorted(set(liaisons) - set(r["ID_ARC"].astype(int)))
        if inconnues:
            raise ErreurDemande(f"liaison(s) inconnue(s) : {inconnues}")
        inconnues = sorted(set(routes) - set(r["ID_ROUTE"]))
        if inconnues:
            raise ErreurDemande(f"route(s) inconnue(s) : {inconnues}")
        fermees = set(routes) | set(r.loc[r["ID_ARC"].astype(int).isin(liaisons), "ID_ROUTE"])
        if not fermees:
            raise ErreurDemande("rien à fermer : donner au moins une liaison ou une route")
        return fermees

    def fermer(self, liaisons: list[int], routes: list[str], theta: dict[str, float] | None = None,
               seuil_report: float = 0.5) -> dict:
        fermees = self.routes_fermees(liaisons, routes)
        ctx = self.contexte(theta)
        impacts, flux = fermer_avec_flux(ctx, fermees, self.meta["cout_horaire"], self.meta["valeur_tonne"])
        delta = flux - ctx.flux_ref
        ids = self.art.routes["ID_ROUTE"].to_numpy()
        k = np.flatnonzero(np.abs(delta) >= seuil_report)
        k = k[np.argsort(-np.abs(delta[k]))]
        return {"fermees": sorted(fermees), "theta_modifie": ctx is not self.cale, "impacts": impacts,
                "reports": [{"ID_ROUTE": str(ids[i]), "delta_camions_jour": float(delta[i])} for i in k]}

    def classement(self, scenario: str, mesure: str, limite: int) -> list[dict]:
        if self.criticite is None:
            return []
        c = self.criticite[self.criticite["scenario"] == scenario]
        c = c.sort_values(mesure, ascending=False).head(limite)
        return c.replace({np.nan: None}).to_dict(orient="records")
