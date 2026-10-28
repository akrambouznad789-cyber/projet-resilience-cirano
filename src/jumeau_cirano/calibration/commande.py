"""
commande.py
===========
`jumeau preparer` : artefact du modèle (demande ENR, chemins, postes, sections) dans outputs/jumeau/artefact/.

`jumeau calibrer [--objectif configs/objectifs/<nom>.yaml] [--rapide]` :
1. validation croisée à 5 plis (blocs spatiaux de sections × postes d'interception entiers),
   pour le modèle calé et les bases de comparaison B0 et B1
2. calibration finale sur toutes les observations
3. bootstrap (poids de Poisson, départ à chaud) : intervalles à 95 % des paramètres
4. suivi MLflow + outputs/jumeau/calibration.json (local) + docs/jumeau/modele_public.json (paramètres et
   métriques seulement : aucune donnée de l'ENR)
`--rapide` : étape 2 seulement.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import date

import mlflow
import numpy as np
import pandas as pd
import torch

from jumeau_cirano.calibration.entrainement import Selection, entrainer
from jumeau_cirano.calibration.validation import (
    ModeleFixe,
    metriques,
    plis_groupes,
    plis_sections,
    selection_entrainement,
    tout_ou_rien,
)
from jumeau_cirano.config import DIR_CONFIGS, RACINE, ConfigModele, Objectif, charger, chemin_absolu
from jumeau_cirano.modele.affectation import ModeleAffectation
from jumeau_cirano.modele.preparation import preparer
from jumeau_cirano.modele.tenseurs import Artefact, charger_artefact


def executer_preparation(args: argparse.Namespace) -> None:
    cfg = charger(args.config or DIR_CONFIGS / "modele.yaml", ConfigModele)
    dossier = preparer(cfg)
    print(f"  → {dossier}")


def nouveau_modele(cfg: ConfigModele, art: Artefact, base: str | None = None) -> ModeleAffectation:
    p0 = cfg.parametres_initiaux
    if base is None:
        return ModeleAffectation(p0.model_dump(), p0.fond_par_classe, art.frontieres)
    return ModeleFixe(tout_ou_rien(art, base), p0.model_dump(), p0.fond_par_classe, art.frontieres)


def validation_croisee(cfg: ConfigModele, art: Artefact, objectif: Objectif) -> pd.DataFrame:
    """Une ligne par (modèle, pli) : métriques sur les sections et les camions mis de côté."""
    cal = cfg.calibration
    ps, pg = plis_sections(art, cal.bloc_km, cal.plis), plis_groupes(art, cal.plis)
    comptages = Objectif(nom="comptages", poids_comptages=1.0, poids_enquete=0.0)
    lignes = []
    for pli in range(cal.plis):
        sel = selection_entrainement(ps, pg, pli)
        for nom, base in (("modèle calé", None), ("B0 : Volet 2", "B0"), ("B1 : plus rapide", "B1")):
            m = nouveau_modele(cfg, art, base)
            entrainer(m, art, sel, objectif if base is None else comptages, cal)
            lignes.append({"modele": nom, "pli": pli, **metriques(m, art, ps == pli, pg == pli)})
            print(f"      pli {pli + 1}/{cal.plis} · {nom:16s} " + " ".join(
                f"{k}={v:.3f}" for k, v in lignes[-1].items() if isinstance(v, float)))
    return pd.DataFrame(lignes)


def bootstrap(cfg: ConfigModele, art: Artefact, objectif: Objectif, final: ModeleAffectation) -> pd.DataFrame:
    """Paramètres recalés sur des poids de Poisson(1) (départ à chaud depuis la calibration finale)."""
    gen = torch.Generator().manual_seed(cfg.calibration.graine)
    tirages = []
    for b in range(cfg.calibration.bootstrap):
        sel = Selection(torch.poisson(torch.ones(len(art.observees), dtype=torch.float64), generator=gen),
                        torch.poisson(torch.ones(art.enquete.n.shape[0], dtype=torch.float64), generator=gen))
        m = nouveau_modele(cfg, art)
        m.load_state_dict(final.state_dict())
        entrainer(m, art, sel, objectif, cfg.calibration, iterations_adam=40, iterations_lbfgs=20)
        tirages.append(m.valeurs())
        print(f"      tirage {b + 1}/{cfg.calibration.bootstrap}", end="\r")
    print()
    return pd.DataFrame(tirages)


def executer(args: argparse.Namespace) -> None:
    """Calibre le modèle selon l'objectif choisi et enregistre tout dans MLflow."""
    cfg = charger(args.config or DIR_CONFIGS / "modele.yaml", ConfigModele)
    objectif = charger(args.objectif or cfg.calibration.objectif, Objectif)
    dossier = chemin_absolu(cfg.dossier_sortie)
    if not (dossier / "artefact" / "meta.json").exists():
        print("[0] artefact absent : préparation")
        preparer(cfg)
    torch.manual_seed(cfg.calibration.graine)
    art = charger_artefact(dossier / "artefact")
    print(f"[1/4] objectif « {objectif.nom} » ; {len(art.observees)} sections mesurées, "
          f"{int(art.enquete.n.sum()):,} camions enquêtés, {art.donnees.chemin_route.shape[0]:,} itinéraires")

    mlflow.set_tracking_uri(f"sqlite:///{dossier / 'mlflow.db'}")
    if mlflow.get_experiment_by_name("jumeau-affectation") is None:
        mlflow.create_experiment("jumeau-affectation", artifact_location=(dossier / "mlartifacts").as_uri())
    mlflow.set_experiment("jumeau-affectation")
    with mlflow.start_run(run_name=f"{objectif.nom} {'rapide' if args.rapide else 'complet'}"):
        mlflow.log_params({"objectif": objectif.nom, "poids_comptages": objectif.poids_comptages,
                           "poids_enquete": objectif.poids_enquete, **cfg.calibration.model_dump(),
                           **{f"chemins_{k}": v for k, v in cfg.chemins.model_dump().items()}})
        cv = None
        if not args.rapide:
            print("[2/4] validation croisée")
            debut = time.time()
            cv = validation_croisee(cfg, art, objectif)
            resume = cv.drop(columns="pli").groupby("modele").agg(["mean", "std"])
            print(resume.round(3).to_string(), f"\n      ({time.time() - debut:.0f} s)")
            for modele, ligne in cv.drop(columns="pli").groupby("modele").mean().iterrows():
                cle = {"modèle calé": "M", "B0 : Volet 2": "B0", "B1 : plus rapide": "B1"}[modele]
                mlflow.log_metrics({f"cv_{cle}_{k}": float(v) for k, v in ligne.items()})

        print("[3/4] calibration finale")
        final = nouveau_modele(cfg, art)
        historique = entrainer(final, art, Selection.tout(art), objectif, cfg.calibration)
        valeurs = final.valeurs()
        tout = metriques(final, art, np.ones(len(art.observees), bool), np.ones(art.enquete.n.shape[0], bool))
        mlflow.log_metrics({f"final_{k}": v for k, v in {**valeurs, **tout}.items()})
        for i, h in enumerate(historique[::5]):
            mlflow.log_metrics({f"perte_{k}": v for k, v in h.items()}, step=i * 5)
        print("      " + " · ".join(f"{k} = {v:.4g}" for k, v in valeurs.items()))

        ic = {}
        if not args.rapide and cfg.calibration.bootstrap:
            print(f"[4/4] bootstrap ({cfg.calibration.bootstrap} tirages)")
            tirages = bootstrap(cfg, art, objectif, final)
            ic = {k: [float(tirages[k].quantile(0.025)), float(tirages[k].quantile(0.975))] for k in tirages}

        resultat = {"genere_le": date.today().isoformat(), "objectif": objectif.model_dump(),
                    "parametres": valeurs, "ic95": ic, "metriques_finales": tout,
                    "validation_croisee": None if cv is None else
                    cv.drop(columns="pli").groupby("modele").agg(["mean", "std"]).round(4).to_dict(),
                    "observations": {"sections_mesurees": len(art.observees),
                                     "camions_enquetes": int(art.enquete.n.sum()),
                                     "itineraires": int(art.donnees.chemin_route.shape[0])}}
        texte = json.dumps(resultat, ensure_ascii=False, indent=2, default=str)
        (dossier / f"calibration_{objectif.nom}.json").write_text(texte, encoding="utf-8")
        public = RACINE / "docs" / "jumeau" / f"modele_public_{objectif.nom}.json"
        public.parent.mkdir(parents=True, exist_ok=True)
        public.write_text(texte, encoding="utf-8")
        state = dossier / f"modele_{objectif.nom}.pt"
        torch.save(final.state_dict(), state)
        mlflow.log_artifact(str(dossier / f"calibration_{objectif.nom}.json"))
        mlflow.log_artifact(str(state))
        print(f"      → {dossier / f'calibration_{objectif.nom}.json'}\n      → {public}")
