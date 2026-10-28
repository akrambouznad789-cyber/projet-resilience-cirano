"""
moteur.py
=========
Contexte d'un scénario → données compactes du moteur JavaScript (scripts/gabarits/js/modele.js
et scenario.js), qui refait dans la page le calcul de `affectation.py` et de `resilience.py`.

Pseudo-code
-----------
routes    : minutes, km hors autoroute, km estimés, km, extrémités (indices de nœuds)
séquences : suites de routes uniques (45 000 pour 258 000 itinéraires : beaucoup d'itinéraires
            ne diffèrent que par l'accès hors Québec) ; chaque itinéraire pointe vers la sienne
itinéraires : paire, séquence, nœuds d'entrée et de sortie, minutes et km d'accès, ln PS
paires    : trajets et tonnes ENR par an (n3, t3)
sections  : incidence route → section, classe RTSS, sections mesurées et camions observés
tableaux numériques en base64 (petit-boutiste) : la page complète pèse ~7 Mo au lieu de ~40 Mo en JSON

Ces données dérivent de l'ENR : elles ne vont que dans la page LOCALE (outputs/), jamais dans docs/.
Le test tests/test_parite_js.py vérifie que node retrouve les sorties de PyTorch à 1e-6 près.
"""

from __future__ import annotations

import base64

import numpy as np
import pandas as pd

from jumeau_cirano.modele.affectation import CLASSES
from jumeau_cirano.scenarios.resilience import Contexte


def tableau(x: np.ndarray, type_: str) -> dict:
    """Tableau numérique → {"type", "b64"} ; types : f4, f8, i4, u2, u1."""
    dt = {"f4": "<f4", "f8": "<f8", "i4": "<i4", "u2": "<u2", "u1": "u1"}[type_]
    a = np.ascontiguousarray(np.asarray(x).astype(dt))
    return {"type": type_, "n": int(a.size), "b64": base64.b64encode(a.tobytes()).decode("ascii")}


def entier(x: np.ndarray) -> dict:
    """Entiers positifs dans le plus petit type sûr (u2 si possible)."""
    x = np.asarray(x)
    return tableau(x, "u2" if x.size == 0 or x.max() < 65535 else "i4")


def donnees_moteur(ctx: Contexte, liaisons: pd.DataFrame, reels: str = "f4") -> dict:
    """Données du moteur JS pour le contexte `ctx` ; `reels="f8"` pour le test de parité."""
    art, d = ctx.art, ctx.art.donnees
    routes = art.routes.reset_index(drop=True)
    bouts = liaisons.set_index("ID_ARC")[["ID_A", "ID_B"]]
    a_route = bouts.loc[routes["ID_ARC"], "ID_A"].to_numpy()
    b_route = bouts.loc[routes["ID_ARC"], "ID_B"].to_numpy()
    chemins = art.chemins.reset_index(drop=True)
    noeuds = sorted(set(a_route) | set(b_route) | set(chemins["entree"]) | set(chemins["sortie"]))
    i_noeud = {n: i for i, n in enumerate(noeuds)}

    # Suites de routes de chaque itinéraire (incidence creuse, triée par ligne), puis dédoublonnées
    ind = d.chemin_route.coalesce().indices().numpy()
    bornes = np.searchsorted(ind[0], np.arange(d.chemin_route.shape[0] + 1))
    suites = [tuple(ind[1, bornes[k]:bornes[k + 1]]) for k in range(len(bornes) - 1)]
    uniques: dict[tuple, int] = {}
    seq = np.array([uniques.setdefault(s, len(uniques)) for s in suites])
    longueurs = np.array([len(s) for s in uniques])
    debut = np.concatenate([[0], np.cumsum(longueurs)])
    contenu = np.concatenate([np.array(s, dtype=np.int64) for s in uniques]) if uniques else np.zeros(0)

    rs = d.route_section.coalesce().indices().numpy()
    observees = np.asarray(getattr(art, "observees", np.zeros(0, dtype=int)))
    y_obs = getattr(art, "y_obs", None)
    return {
        "classes": CLASSES,
        "frontieres": list(ctx.modele.frontieres),
        "noeuds": {"id": noeuds, "frontiere": [n in ctx.frontieres for n in noeuds]},
        "routes": {
            "id": routes["ID_ROUTE"].tolist(), "arc": routes["ID_ARC"].astype(int).tolist(),
            "a": [i_noeud[n] for n in a_route], "b": [i_noeud[n] for n in b_route],
            "minutes": tableau(d.minutes.numpy(), "f8"), "km_hors_auto": tableau(d.km_hors_auto.numpy(), "f8"),
            "km_estime": tableau(d.km_estime.numpy(), "f8"), "km": tableau(routes["km"].to_numpy(dtype=float), "f8"),
        },
        "sequences": {"debut": tableau(debut, "i4"), "routes": entier(contenu)},
        "chemins": {
            "paire": tableau(d.paire.numpy(), "i4"), "seq": entier(seq),
            "entree": entier(chemins["entree"].map(i_noeud).to_numpy()),
            "sortie": entier(chemins["sortie"].map(i_noeud).to_numpy()),
            "minutes_acces": tableau(d.minutes_acces.numpy(), reels),
            "km_acces": tableau(chemins["km_acces"].to_numpy(dtype=float), reels),
            "ln_ps": tableau(d.ln_ps.numpy(), reels),
        },
        "paires": {"n3": tableau(ctx.paires["n3"].to_numpy(dtype=float), reels),
                   "t3": tableau(ctx.paires["t3"].to_numpy(dtype=float), reels)},
        "sections": {
            "n": int(d.route_section.shape[1]),
            "route": entier(rs[0]), "section": tableau(rs[1], "i4"),
            "classe": tableau(d.classe_section.numpy(), "u1"),
            "observees": tableau(observees, "i4"),
            "y_obs": tableau(np.zeros(0) if y_obs is None else y_obs.numpy(), reels),
        },
    }


def theta_js(valeurs: dict[str, float], frontieres: list[str]) -> dict:
    """Paramètres calés (calibration_<objectif>.json) → objet θ de modele.js."""
    return {"mu": valeurs["mu"], "beta_hors_autoroute": valeurs["beta_hors_autoroute"],
            "beta_estime": valeurs["beta_estime"], "croissance": valeurs["croissance"],
            "fond": [valeurs[f"fond_{c}"] for c in CLASSES],
            "beta_frontiere": [valeurs.get(f"beta_frontiere_{f}", 0.0) for f in frontieres]}
