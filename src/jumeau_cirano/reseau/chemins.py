"""
chemins.py
==========
Ensembles de chemins du modèle d'affectation, calculés une fois pour toutes
(ils ne dépendent pas des paramètres, ce qui rend la calibration différentiable
et rapide).

Le graphe est celui des liaisons, avec plusieurs routes par liaison. Pour
utiliser l'algorithme de Yen (k plus courts chemins simples), chaque route
devient un nœud intermédiaire « R:<ID_ROUTE> » : A → R:12-1 → B. Un point
frontière ne peut être qu'une extrémité, jamais une étape (sortir du Québec
pour y rentrer au même point n'a pas de sens).

Pseudo-code
-----------
paires de nœuds nécessaires = (nœud ou frontière d'origine) × (nœud ou frontière de destination)
pour chaque paire (en parallèle) : k plus rapides, détour ≤ detour_max × le meilleur
itinéraires d'une paire de lieux = accès hors Québec + chemin, ≤ max_par_itineraire, détour ≤ detour_max
"""

from __future__ import annotations

from itertools import islice

import networkx as nx
import numpy as np
import pandas as pd
from joblib import Parallel, delayed

from jumeau_cirano.config import ParamChemins


def graphe_des_routes(routes: pd.DataFrame, liaisons: pd.DataFrame) -> nx.DiGraph:
    """Graphe orienté nœud → R:route → nœud (dans les deux sens), pondéré en minutes."""
    bouts = liaisons.set_index("ID_ARC")[["ID_A", "ID_B"]]
    h = nx.DiGraph()
    for r in routes.itertuples():
        a, b = bouts.loc[r.ID_ARC]
        n, w = f"R:{r.ID_ROUTE}", r.minutes / 2
        for u, v in ((a, n), (n, b), (b, n), (n, a)):
            h.add_edge(u, v, minutes=w)
    return h


def chemins_entre(h: nx.DiGraph, a: str, b: str, frontieres: set[str], k: int, detour_max: float) -> list[tuple]:
    """Jusqu'à k chemins (tuples d'ID_ROUTE), du plus rapide au moins rapide, sans transit par une frontière."""
    vue = h.subgraph([n for n in h.nodes if n not in frontieres or n in (a, b)])
    trouves, meilleur = [], None
    try:
        for chemin in islice(nx.shortest_simple_paths(vue, a, b, weight="minutes"), 4 * k):
            cout = nx.path_weight(vue, chemin, "minutes")
            meilleur = cout if meilleur is None else meilleur
            if cout > detour_max * meilleur:
                break
            trouves.append(tuple(n[2:] for n in chemin if n.startswith("R:")))
            if len(trouves) >= k:
                break
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        pass
    return trouves


def substitutions(chemins: list[tuple], route_arc: dict[str, int], routes_arc: dict[int, list[str]],
                  minutes: dict[str, float], detour_max: float) -> list[tuple]:
    """Ajoute, pour le meilleur chemin, chaque variante où UNE liaison emprunte une autre de ses routes.

    Yen garde les k chemins les plus rapides du réseau entier : les alternatives d'une liaison peuvent en
    être évincées par des variantes ailleurs (Lacolle : l'A-15 n'est que la 2e route de sa liaison).
    La substitution garantit que chaque alternative d'une liaison traversée figure dans l'ensemble.
    """
    if not chemins:
        return chemins
    tous, meilleur = list(chemins), chemins[0]
    cout = lambda ch: sum(minutes[r] for r in ch)  # noqa: E731
    limite = detour_max * cout(meilleur)
    for i, r in enumerate(meilleur):
        for autre in routes_arc[route_arc[r]]:
            variante = meilleur[:i] + (autre,) + meilleur[i + 1:]
            if autre != r and variante not in tous and cout(variante) <= limite:
                tous.append(variante)
    return tous


def ensembles_par_paire(h: nx.DiGraph, paires: list[tuple[str, str]], frontieres: set[str],
                        p: ParamChemins, routes: pd.DataFrame, n_jobs: int = -1) -> dict[tuple[str, str], list[tuple]]:
    """Chemins de chaque paire de nœuds (Yen puis substitutions de routes), calculés en parallèle par lots."""
    lots = [paires[i::32] for i in range(32)]
    route_arc = dict(zip(routes["ID_ROUTE"], routes["ID_ARC"], strict=True))
    routes_arc = routes.groupby("ID_ARC")["ID_ROUTE"].apply(list).to_dict()
    minutes = dict(zip(routes["ID_ROUTE"], routes["minutes"], strict=True))

    def lot(ps: list) -> list:
        return [(pa, substitutions(chemins_entre(h, pa[0], pa[1], frontieres, p.k_par_paire, p.detour_max),
                                   route_arc, routes_arc, minutes, p.detour_max)) for pa in ps]

    resultats = Parallel(n_jobs=n_jobs)(delayed(lot)(ps) for ps in lots if ps)
    return {pa: ch for r in resultats for pa, ch in r}


def candidats_du_lieu(lieu: pd.Series, p: ParamChemins) -> list[tuple[str, float, float]]:
    """[(nœud, km d'accès hors Québec, minutes d'accès)] : le nœud lui-même au Québec, sinon les frontières."""
    return [(n, km, km / p.vitesse_hors_qc_kmh * 60) for n, km in lieu["candidats"]]


def itineraires(paires_lieux: pd.DataFrame, lieux: pd.DataFrame, ensembles: dict, minutes_route: dict[str, float],
                p: ParamChemins) -> pd.DataFrame:
    """Une ligne par itinéraire : paire (indice), routes, frontières d'entrée et de sortie, km et minutes d'accès."""
    lieu = lieux.set_index("pt")
    lignes = []
    for i, (o, d) in enumerate(zip(paires_lieux["o_pt"], paires_lieux["d_pt"], strict=True)):
        options = []
        for no, km_o, mn_o in candidats_du_lieu(lieu.loc[o], p):
            for nd, km_d, mn_d in candidats_du_lieu(lieu.loc[d], p):
                if no == nd:
                    continue
                for ch in ensembles.get((no, nd), []):
                    options.append((mn_o + mn_d + sum(minutes_route[r] for r in ch), ch, no, nd, km_o + km_d))
        if not options:
            continue
        options.sort(key=lambda x: x[0])
        meilleur = options[0][0]
        for cout, ch, no, nd, km in options[:p.max_par_itineraire]:
            if cout > p.detour_max * meilleur:
                break
            lignes.append({"paire": i, "routes": ch, "entree": no, "sortie": nd, "km_acces": km,
                           "minutes_acces": km / p.vitesse_hors_qc_kmh * 60})
    return pd.DataFrame(lignes)


def taille_de_chemin(chemins: pd.DataFrame, km_route: dict[str, float]) -> np.ndarray:
    """Facteur de taille de chemin (path-size) : PS_p = Σ_r∈p (km_r / km_p) / N_r, N_r = chemins de la paire qui
    empruntent r. Il corrige le logit, qui sinon surestime deux chemins presque identiques."""
    ps = np.ones(len(chemins))
    for _, grp in chemins.groupby("paire"):
        usage: dict[str, int] = {}
        for ch in grp["routes"]:
            for r in ch:
                usage[r] = usage.get(r, 0) + 1
        for j, ch in zip(grp.index, grp["routes"], strict=True):
            km = np.array([km_route[r] for r in ch])
            if km.sum() > 0:
                ps[j] = float((km / km.sum() / np.array([usage[r] for r in ch])).sum())
    return ps
