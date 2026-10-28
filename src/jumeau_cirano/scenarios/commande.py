"""
commande.py
===========
`jumeau scenarios [--config configs/scenarios.yaml] [--objectif configs/objectifs/<nom>.yaml]`

1. charge le modèle calé (calibration_<objectif>.json) et l'artefact
2. ferme chaque liaison, puis chaque route d'une liaison qui en a plusieurs
3. classe les liaisons par Δ heures équivalentes (coût composite du logit) ; les coupures (trajets non
   desservis) sont signalées par la colonne `coupure`, car leur coût n'est pas dans ce Δ
4. robustesse : rejoue les fermetures de liaisons avec des paramètres tirés du bootstrap ;
   τ de Kendall et recouvrement du top 20 avec le classement central
Sorties : outputs/jumeau/criticite_<objectif>.csv ; docs/jumeau/criticite_<objectif>.csv (résultats par liaison,
publiables selon la règle du Volet 2 : aucun flux entre lieux).
"""

from __future__ import annotations

import argparse
import json

import pandas as pd
from scipy.stats import kendalltau

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
from jumeau_cirano.donnees.liaisons import charger_liaisons, charger_noeuds
from jumeau_cirano.modele.affectation import ModeleAffectation
from jumeau_cirano.modele.tenseurs import charger_artefact
from jumeau_cirano.reseau.chemins import graphe_des_routes
from jumeau_cirano.scenarios.resilience import Contexte, fermer, toutes_les_fermetures


def executer(args: argparse.Namespace) -> None:
    cfg = charger(args.config or DIR_CONFIGS / "scenarios.yaml", ConfigScenarios)
    cm = charger(cfg.modele, ConfigModele)
    cr = charger(cm.reseau, ConfigReseau)
    objectif = charger(args.objectif or cfg.objectif, Objectif)
    dossier = chemin_absolu(cm.dossier_sortie)
    calibration = json.loads((dossier / f"calibration_{objectif.nom}.json").read_text(encoding="utf-8"))

    print(f"[1/3] modèle calé « {objectif.nom} »")
    art = charger_artefact(dossier / "artefact")
    paires = pd.read_parquet(dossier / "artefact" / "paires.parquet")
    liaisons = charger_liaisons(cr)
    frontieres = set(charger_noeuds(cr).query("TYPE.str.startswith('frontiere')", engine="python")["ID"])
    h = graphe_des_routes(art.routes, liaisons)

    def contexte(valeurs: dict) -> Contexte:
        return Contexte.creer(art, ModeleAffectation.depuis_valeurs(valeurs, art.frontieres), h, frontieres, paires)

    print("[2/3] fermetures (chaque liaison, puis chaque route)")
    res = toutes_les_fermetures(contexte(calibration["parametres"]), cfg.cout_horaire_camion, cfg.valeur_par_tonne)
    res = res.merge(liaisons[["ID_ARC", "VILLE_A", "VILLE_B"]], on="ID_ARC")
    res["coupure"] = res["trajets_non_desservis"] > 0
    arcs = res[res["scenario"] == "liaison"].sort_values("delta_heures_equivalentes", ascending=False).copy()
    arcs["rang"] = range(1, len(arcs) + 1)
    res = res.merge(arcs[["ID_ARC", "rang"]], on="ID_ARC", how="left")
    print(arcs.head(15)[["rang", "VILLE_A", "VILLE_B", "camions_sur_liaison", "delta_heures_equivalentes",
                         "trajets_non_desservis"]].round(0).to_string(index=False))
    print(f"      coupures : {int(arcs['coupure'].sum())} liaisons sur {len(arcs)}")

    robustesse = {}
    tirages_f = dossier / f"bootstrap_{objectif.nom}.parquet"
    if tirages_f.exists() and cfg.robustesse_tirages:
        print(f"[3/3] robustesse ({cfg.robustesse_tirages} tirages du bootstrap)")
        tirages = pd.read_parquet(tirages_f).head(cfg.robustesse_tirages)
        top = set(arcs["ID_ARC"].head(20))
        taus, recouvrements = [], []
        for _, v in tirages.iterrows():
            ctx = contexte(v.to_dict())
            dh = pd.Series({a: fermer(ctx, set(g["ID_ROUTE"]), cfg.cout_horaire_camion, cfg.valeur_par_tonne)
                            ["delta_heures_equivalentes"] for a, g in art.routes.groupby("ID_ARC")})
            taus.append(kendalltau(arcs.set_index("ID_ARC")["delta_heures_equivalentes"],
                                   dh.reindex(arcs["ID_ARC"]))[0])
            recouvrements.append(len(top & set(dh.sort_values(ascending=False).index[:20])) / 20)
        robustesse = {"kendall_tau_moyen": float(pd.Series(taus).mean()), "kendall_tau_min": float(min(taus)),
                      "recouvrement_top20_moyen": float(pd.Series(recouvrements).mean())}
        print("      " + " · ".join(f"{k} = {v:.3f}" for k, v in robustesse.items()))

    sortie = dossier / f"criticite_{objectif.nom}.csv"
    res.round(2).to_csv(sortie, index=False)
    public = RACINE / "docs" / "jumeau" / f"criticite_{objectif.nom}.csv"
    public.parent.mkdir(parents=True, exist_ok=True)
    res.round(0).to_csv(public, index=False)
    (dossier / f"robustesse_{objectif.nom}.json").write_text(json.dumps(robustesse, indent=2), encoding="utf-8")
    print(f"      → {sortie}\n      → {public}")
