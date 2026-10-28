"""
visualisation_jumeau.py
=======================
Page « Volet 3 · Le jumeau » : le réseau à plusieurs routes, le modèle calé et ses réglages,
la calibration, la fermeture d'une liaison et le classement des liaisons critiques.

Pseudo-code
-----------
1. contexte()        modèle calé (calibration_<objectif>.json) + artefact, comme `jumeau scenarios`
2. couches           routes (tracés simplifiés, rang, km, minutes, numéros), nœuds, fond de carte du Volet 2
3. resultats_cales() flux par route, camions prédits et mesurés par section, métriques ; paramètres,
                     IC à 95 % et validation croisée B0 / B1 / modèle (calibration_<objectif>.json)
4. scenarios()       criticité par liaison et par route, reports de trafic, robustesse (`jumeau scenarios`)
5. version LOCALE seulement : données du moteur JS (itinéraires, dérivés de l'ENR) et préréglages
   d'objectif → curseurs et recalcul dans la page. Version PUBLIQUE : résultats précalculés seulement.
6. exporter()        gabarit scripts/gabarits/jumeau.html + thème + js/ (moteur Canvas, modele.js, scenario.js)

Sorties : outputs/jumeau/index.html (complète, locale) ; docs/jumeau/index.html (--publique)
Usage : uv run python scripts/visualisation_jumeau.py [--publique] [--objectif configs/objectifs/<nom>.yaml]
"""

from __future__ import annotations

import argparse
import inspect
import json
import re
from datetime import date
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import torch
from shapely.geometry import LineString
from theme_pages import injecter
from visualisation_volet2 import fond_de_carte, geojson

from jumeau_cirano.config import (
    DIR_CONFIGS,
    ConfigModele,
    ConfigReseau,
    ConfigScenarios,
    Objectif,
    charger,
    chemin_absolu,
)
from jumeau_cirano.donnees.liaisons import charger_liaisons, charger_noeuds
from jumeau_cirano.interface.moteur import donnees_moteur, theta_js
from jumeau_cirano.modele.affectation import CLASSES, ModeleAffectation
from jumeau_cirano.modele.tenseurs import charger_artefact
from jumeau_cirano.reseau.chemins import graphe_des_routes
from jumeau_cirano.scenarios import resilience
from jumeau_cirano.scenarios.resilience import Contexte

RACINE = Path(__file__).resolve().parent.parent
DIR_GABARITS = RACINE / "scripts" / "gabarits"
SORTIE_LOCALE = RACINE / "outputs" / "jumeau" / "index.html"
SORTIE_PUBLIQUE = RACINE / "docs" / "jumeau" / "index.html"
TOL_ROUTE_M = 120.0          # simplification des tracés pour le web
NOMS_OBJECTIFS = {"équilibre": "Calé · équilibre", "comptages": "Comptages seuls", "enquête": "Enquête seule"}


def contexte(objectif_yaml: str | None) -> tuple[Contexte, dict, ConfigReseau, Path, Objectif, ConfigScenarios]:
    """Modèle calé et artefact (même construction que la commande `jumeau scenarios`)."""
    cfg = charger(DIR_CONFIGS / "scenarios.yaml", ConfigScenarios)
    cm = charger(cfg.modele, ConfigModele)
    cr = charger(cm.reseau, ConfigReseau)
    objectif = charger(objectif_yaml or cfg.objectif, Objectif)
    dossier = chemin_absolu(cm.dossier_sortie)
    calibration = json.loads((dossier / f"calibration_{objectif.nom}.json").read_text(encoding="utf-8"))
    art = charger_artefact(dossier / "artefact")
    paires = pd.read_parquet(dossier / "artefact" / "paires.parquet")
    liaisons = charger_liaisons(cr)
    frontieres = set(charger_noeuds(cr).query("TYPE.str.startswith('frontiere')", engine="python")["ID"])
    modele = ModeleAffectation.depuis_valeurs(calibration["parametres"], art.frontieres)
    ctx = Contexte.creer(art, modele, graphe_des_routes(art.routes, liaisons), frontieres, paires)
    return ctx, calibration, cr, dossier, objectif, cfg


def couche_routes(cr: ConfigReseau, routes: pd.DataFrame, liaisons: pd.DataFrame) -> dict:
    """Tracés des routes (ordre de l'artefact : `i` = indice dans les tableaux du modèle)."""
    geo = gpd.read_file(chemin_absolu(cr.sortie), layer="routes", columns=["ID_ROUTE", "km_autoroute", "routes"])
    geo = geo.set_index("ID_ROUTE").reindex(routes["ID_ROUTE"])
    geo["geometry"] = geo.geometry.simplify(TOL_ROUTE_M)
    # Quelques routes courtes ne sont faites que de raccords estimés, sans tracé : ligne droite entre les nœuds
    pos = charger_noeuds(cr).set_index("ID").geometry
    bouts = liaisons.set_index("ID_ARC").loc[routes["ID_ARC"], ["ID_A", "ID_B"]].to_numpy()
    sans = (geo.geometry.isna() | geo.geometry.is_empty).to_numpy()
    geo.loc[sans, "geometry"] = [LineString([pos[a], pos[b]]) for a, b in bouts[sans]]
    geo["schema"] = sans.astype(int)
    geo = gpd.GeoDataFrame(geo, geometry="geometry", crs=cr.crs).to_crs("EPSG:4326").reset_index()
    multiples = routes.groupby("ID_ARC")["ID_ROUTE"].transform("size") > 1
    villes = liaisons.set_index("ID_ARC")
    geo = geo.assign(
        i=np.arange(len(routes)), arc=routes["ID_ARC"].astype(int).to_numpy(),
        rang=routes["rang"].astype(int).to_numpy(),
        km=routes["km"].round(1).to_numpy(), min=routes["minutes"].round(1).to_numpy(),
        auto=routes["km_autoroute"].fillna(0).round(1).to_numpy(), nums=geo["routes"].fillna("").to_numpy(),
        src=routes["source"].to_numpy(), multi=multiples.astype(int).to_numpy(),
        a=villes.loc[routes["ID_ARC"], "VILLE_A"].to_numpy(), b=villes.loc[routes["ID_ARC"], "VILLE_B"].to_numpy())
    proprietes = ["i", "ID_ROUTE", "arc", "rang", "km", "min", "auto", "nums", "src", "multi", "schema", "a", "b"]
    return geojson(geo, proprietes, 4)


def couche_noeuds(cr: ConfigReseau) -> list[dict]:
    noeuds = charger_noeuds(cr).to_crs("EPSG:4326")
    return [{"id": r.ID, "nom": r.NOM, "type": r.TYPE, "lon": round(r.geometry.x, 4), "lat": round(r.geometry.y, 4)}
            for r in noeuds.itertuples()]


def resultats_cales(ctx: Contexte, cr: ConfigReseau, calibration: dict) -> dict:
    """Flux par route et camions par section mesurée, avec les paramètres calés."""
    art = ctx.art
    with torch.no_grad():
        _, flux, y = ctx.modele(art.donnees)
    sec = art.sections.iloc[art.observees]
    geo = gpd.read_file(chemin_absolu(cr.sortie), layer="sections", columns=["ide_sectn_trafc", "num_route"])
    geo = geo.set_index("ide_sectn_trafc").reindex(sec["ide_sectn_trafc"])
    pts = gpd.GeoSeries(geo.geometry.representative_point(), crs=cr.crs).to_crs("EPSG:4326")
    return {
        "flux": np.round(flux.numpy(), 2).tolist(),
        "sections": {"id": sec["ide_sectn_trafc"].astype(str).tolist(), "route": geo["num_route"].fillna("").tolist(),
                     "classe": sec["classe"].astype(str).tolist(),
                     "lon": pts.x.round(4).tolist(), "lat": pts.y.round(4).tolist(),
                     "obs": np.round(art.y_obs.numpy(), 1).tolist(),
                     "pred": np.round(y.numpy()[art.observees], 1).tolist()},
        "parametres": calibration["parametres"], "ic95": calibration["ic95"],
        "metriques": calibration["metriques_finales"], "validation": calibration["validation_croisee"],
        "observations": calibration["observations"], "objectif": calibration["objectif"],
    }


def scenarios(dossier: Path, objectif: Objectif) -> dict:
    """Criticité (une ligne par scénario), reports de trafic par scénario, robustesse."""
    crit = pd.read_csv(dossier / f"criticite_{objectif.nom}.csv")
    reports = pd.read_csv(dossier / f"reports_{objectif.nom}.csv")
    robustesse = json.loads((dossier / f"robustesse_{objectif.nom}.json").read_text(encoding="utf-8"))
    cles = np.where(reports["scenario"] == "liaison", "L" + reports["ID_ARC"].astype(str),
                    "R" + reports["ID_ROUTE"].fillna("").astype(str))
    par_scenario = {k: [g["route"].tolist(), g["delta_camions_jour"].round(1).tolist()]
                    for k, g in reports.assign(cle=cles).groupby("cle")}
    colonnes = ["scenario", "ID_ARC", "ID_ROUTE", "rang", "coupure", "camions_sur_liaison", "delta_heures_equivalentes",
                "delta_heures_meilleur", "delta_camions_heures", "delta_camions_km", "trajets_non_desservis",
                "tonnes_sur_liaison", "valeur_sur_liaison", "cout_perturbation"]
    crit = crit[colonnes].assign(ID_ROUTE=crit["ID_ROUTE"].fillna(""), rang=crit["rang"].fillna(0).astype(int))
    return {"colonnes": colonnes, "lignes": crit.round(0).to_numpy().tolist(), "reports": par_scenario,
            "robustesse": robustesse}


def prereglages(dossier: Path, frontieres: list[str]) -> dict:
    """θ de chaque objectif déjà calé (calibration_<nom>.json), pour les boutons de la page locale."""
    out = {}
    for nom, libelle in NOMS_OBJECTIFS.items():
        f = dossier / f"calibration_{nom}.json"
        if f.exists():
            cal = json.loads(f.read_text(encoding="utf-8"))
            out[nom] = {"libelle": libelle, "theta": theta_js(cal["parametres"], frontieres),
                        "metriques": cal.get("metriques_finales", {})}
    return out


def extrait(chemin: Path, debut: str, fin: str | None = None) -> str:
    """Texte de `chemin` entre la ligne qui contient `debut` et celle qui contient `fin` (exclue)."""
    lignes = chemin.read_text(encoding="utf-8").splitlines()
    i = next(k for k, ligne in enumerate(lignes) if debut in ligne)
    j = next((k for k in range(i + 1, len(lignes)) if fin and fin in lignes[k]), len(lignes))
    return "\n".join(lignes[i:j]).rstrip()


def extraits_code() -> dict:
    """Le code réellement exécuté, montré dans l'onglet « Méthode » de chaque étape."""
    src = RACINE / "src" / "jumeau_cirano"
    js = DIR_GABARITS / "js"
    morceaux = {
        "1": (src / "reseau" / "alternatives.py", "Pseudo-code", '"""'),
        "2": (js / "modele.js", "// Coût généralisé", "// Coût composite"),
        "3": (src / "modele" / "tenseurs.py", "Vraisemblance de l'enquête", "Seules comptent"),
        "4": (js / "scenario.js", "// Impacts annuels", "    // Paires privées"),
    }
    sortie = {k: [str(f.relative_to(RACINE)), extrait(f, a, b)] for k, (f, a, b) in morceaux.items()}
    sortie["5"] = ["src/jumeau_cirano/scenarios/resilience.py",
                   inspect.getdoc(resilience).split("Pseudo-code")[1].strip("-\n ")]
    return sortie


def exporter(donnees: dict, cible: Path) -> None:
    """Gabarit + thème + modules JS + données → page autonome."""
    html = injecter((DIR_GABARITS / "jumeau.html").read_text(encoding="utf-8"))
    for marqueur, fichier in (("/*__MOTEUR_CANVAS__*/", "moteur_canvas.js"), ("/*__MODELE_JS__*/", "modele.js"),
                              ("/*__SCENARIO_JS__*/", "scenario.js")):
        html = html.replace(marqueur, (DIR_GABARITS / "js" / fichier).read_text(encoding="utf-8"), 1)
    charge = json.dumps(donnees, ensure_ascii=False, separators=(",", ":"), allow_nan=False).replace("</", "<\\/")
    html = html.replace("/*__DONNEES__*/null", charge, 1)
    if re.search(r"/\*__[A-Z_]+__\*/", html):
        raise ValueError("marqueur non remplacé dans le gabarit")
    cible.parent.mkdir(parents=True, exist_ok=True)
    cible.write_text(html, encoding="utf-8")
    print(f"→ {cible.relative_to(RACINE)} ({cible.stat().st_size / 1e6:.1f} Mo)")


def main(publique: bool, objectif_yaml: str | None) -> None:
    print("[1/4] modèle calé et artefact")
    ctx, calibration, cr, dossier, objectif, cfg = contexte(objectif_yaml)
    liaisons = charger_liaisons(cr)
    art = ctx.art
    print("[2/4] couches")
    multi = art.routes.groupby("ID_ARC").size()
    meta = {
        "genere_le": date.today().isoformat(), "objectif": objectif.nom,
        "n_routes": int(len(art.routes)), "n_liaisons": int(multi.size), "n_liaisons_multi": int((multi > 1).sum()),
        "n_sections": int(len(art.sections)), "n_sections_mesurees": int(len(art.observees)),
        "n_itineraires": int(len(art.chemins)), "n_paires": int(art.donnees.n_paires),
        "cout_horaire": cfg.cout_horaire_camion, "valeur_tonne": cfg.valeur_par_tonne,
        "classes": CLASSES, "frontieres": art.frontieres,
    }
    donnees = {
        "meta": meta, "fond": fond_de_carte(), "routes": couche_routes(cr, art.routes, liaisons),
        "noeuds": couche_noeuds(cr),
        "liaisons": liaisons[["ID_ARC", "ID_A", "ID_B", "VILLE_A", "VILLE_B"]].to_dict(orient="list"),
        "theta_cale": theta_js(calibration["parametres"], art.frontieres),
        "code": extraits_code(),
    }
    print("[3/4] résultats calés et scénarios")
    donnees["cale"] = resultats_cales(ctx, cr, calibration)
    donnees["scenarios"] = scenarios(dossier, objectif)
    print("[4/4] export")
    locale = {**donnees, "moteur": donnees_moteur(ctx, liaisons), "prereglages": prereglages(dossier, art.frontieres)}
    exporter({**locale, "meta": {**meta, "publique": False}}, SORTIE_LOCALE)
    if publique:
        exporter({**donnees, "meta": {**meta, "publique": True}}, SORTIE_PUBLIQUE)


if __name__ == "__main__":
    analyseur = argparse.ArgumentParser(description=__doc__.splitlines()[3])
    analyseur.add_argument("--publique", action="store_true", help="écrire aussi docs/jumeau/index.html")
    analyseur.add_argument("--objectif", help="configs/objectifs/<nom>.yaml (défaut : celui de configs/scenarios.yaml)")
    args = analyseur.parse_args()
    main(args.publique, args.objectif)
