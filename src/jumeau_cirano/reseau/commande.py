"""
commande.py
===========
`jumeau reseau [--config configs/reseau.yaml] [--echantillon]`

1. graphe RTSS (rtss.py) → composante principale
2. rattachement des nœuds du graphe des liaisons au graphe RTSS
3. point de contrôle contre les tracés OSRM (controle.py)
4. sorties : GeoPackage (liens_rtss, noeuds_rtss, controle_liaisons) + rapport Markdown
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

from jumeau_cirano.config import DIR_CONFIGS, ConfigReseau, charger, chemin_absolu
from jumeau_cirano.donnees.liaisons import charger_liaisons, charger_noeuds, charger_traces_osrm
from jumeau_cirano.reseau.controle import controler, synthese
from jumeau_cirano.reseau.rtss import (
    ajouter_acces,
    charger_rtss,
    composante_principale,
    construire_graphe,
    noder,
    noeud_acces,
    raccorder_traversees,
)


def suffixer(chemin: Path, echantillon: bool) -> Path:
    """`x.gpkg` → `x_echantillon.gpkg` quand on travaille sur l'échantillon."""
    return chemin.with_name(f"{chemin.stem}_echantillon{chemin.suffix}") if echantillon else chemin


def executer(args: argparse.Namespace) -> None:
    """Construit le graphe RTSS, rattache les nœuds des liaisons et lance le point de contrôle."""
    cfg = charger(args.config or DIR_CONFIGS / "reseau.yaml", ConfigReseau)
    emprise = cfg.echantillon if args.echantillon else None
    print(f"[1/4] RTSS{' — échantillon : ' + emprise.nom if emprise else ''}")
    lignes = charger_rtss(cfg, emprise)
    liens, noeuds = noder(lignes, cfg.graphe.tolerance_noeud_m, cfg.graphe.tolerance_jonction_m,
                          cfg.graphe.tolerance_croisement_m)
    liens, noeuds = raccorder_traversees(liens, noeuds, cfg.graphe.raccord_municipal_max_km,
                                         cfg.graphe.facteur_detour_raccord)
    g_complet = construire_graphe(liens, noeuds, cfg.graphe.vitesses_kmh)
    g = composante_principale(g_complet)
    garde = {k for _, _, k in g.edges(keys=True)}
    km_total = liens["longueur_m"].sum() / 1000
    km_garde = liens.loc[liens["id_lien"].isin(garde), "longueur_m"].sum() / 1000
    print(f"      {len(lignes):,} lignes → {len(liens):,} liens, {len(noeuds):,} nœuds ; "
          f"composante principale : {km_garde:,.0f} km sur {km_total:,.0f}")

    print("[2/4] connecteurs d'accès des nœuds des liaisons")
    nl = charger_noeuds(cfg)
    if emprise is not None:
        boite = shapely.box(emprise.lon_min, emprise.lat_min, emprise.lon_max, emprise.lat_max)
        cadre = gpd.GeoSeries([boite], crs="EPSG:4326").to_crs(cfg.crs).iloc[0]
        nl = nl[nl.within(cadre)]
    a = cfg.acces
    acces = ajouter_acces(g, nl["ID"].tolist(), np.column_stack([nl.geometry.x, nl.geometry.y]),
                          a.routes_max, a.rayon_km, a.facteur_detour, a.vitesse_kmh)
    nl = nl.merge(acces.groupby("ID").agg(dist_km=("dist_km", "min"), n_acces=("noeud_rtss", "size")),
                  left_on="ID", right_index=True)
    loin = nl[nl["dist_km"] > cfg.graphe.rattachement_max_km]

    print("[3/4] point de contrôle")
    arcs = charger_liaisons(cfg)
    arcs = arcs[arcs["ID_A"].isin(nl["ID"]) & arcs["ID_B"].isin(nl["ID"])].copy()
    arcs["noeud_a"], arcs["noeud_b"] = arcs["ID_A"].map(noeud_acces), arcs["ID_B"].map(noeud_acces)
    resultat = controler(g, liens, arcs, charger_traces_osrm(cfg), cfg.controle)
    resultat = resultat.merge(arcs[["ID_ARC", "VILLE_A", "VILLE_B"]], on="ID_ARC")
    # Décision par liaison : routes alternatives sur le RTSS si le réseau y est sans détour,
    # sinon la route OSRM actuelle reste l'unique route (Baie-James, traversier, hors Québec…)
    sur_rtss = (resultat["topologie"] == "ok") & (resultat["statut"] != "hors_quebec")
    resultat["routage"] = np.where(sur_rtss, "rtss", "osrm")
    s = synthese(resultat, cfg.controle)
    print(f"      topologie : {s['ok_interurbaines']}/{s['interurbaines']} interurbaines sans détour "
          f"({s['part_ok_interurbaines']:.0%}) — contrôle {'RÉUSSI' if s['passe'] else 'ÉCHOUÉ'} ; "
          f"urbaines {s['part_ok_urbaines']:.0%} ; accord de route avec OSRM {s['accord_route']:.0%}")

    print("[4/4] sorties")
    sortie = suffixer(chemin_absolu(cfg.sortie), bool(emprise))
    sortie.parent.mkdir(parents=True, exist_ok=True)
    liens[liens["id_lien"].isin(garde)].to_file(sortie, layer="liens_rtss", driver="GPKG")
    noeuds[noeuds["id_noeud"].isin(g.nodes)].to_file(sortie, layer="noeuds_rtss", driver="GPKG")
    xy = {n: (g.nodes[n]["x"], g.nodes[n]["y"]) for n in g.nodes}
    gpd.GeoDataFrame(acces, geometry=[shapely.LineString([xy[noeud_acces(r.ID)], xy[r.noeud_rtss]])
                                      for r in acces.itertuples()], crs=cfg.crs
                     ).to_file(sortie, layer="acces", driver="GPKG")
    resultat.to_file(sortie, layer="controle_liaisons", driver="GPKG")
    rapport = suffixer(chemin_absolu(cfg.rapport), bool(emprise))
    rapport.parent.mkdir(parents=True, exist_ok=True)
    rapport.write_text(ecrire_rapport(cfg, emprise is not None, len(lignes), liens, g, km_total, km_garde,
                                      nl, loin, resultat, s), encoding="utf-8")
    print(f"      → {sortie}\n      → {rapport}")


def ecrire_rapport(cfg: ConfigReseau, echantillon: bool, n_lignes: int, liens: gpd.GeoDataFrame, g,
                   km_total: float, km_garde: float, nl: pd.DataFrame, loin: pd.DataFrame,
                   resultat: pd.DataFrame, s: dict) -> str:
    """Rapport Markdown du point de contrôle (lisible dans VS Code ou sur GitHub)."""
    p = cfg.controle
    pct = lambda x: f"{x:.0%}"  # noqa: E731
    lignes = [
        f"# Point de contrôle du graphe RTSS ({date.today().isoformat()})",
        "",
        f"Emprise : **{cfg.echantillon.nom if echantillon else 'tout le Québec'}**.",
        "",
        "## Graphe",
        f"- {n_lignes:,} sous-routes actives → {len(liens):,} liens après découpe aux jonctions.",
        f"- Composante principale : {g.number_of_nodes():,} nœuds, {g.number_of_edges():,} liens, "
        f"{km_garde:,.0f} km sur {km_total:,.0f} ({km_garde / km_total:.1%} de la longueur).",
        f"- Accès des {len(nl)} nœuds des liaisons : {nl['n_acces'].median():.0f} connecteurs par nœud "
        f"(médiane) ; nœud RTSS le plus proche à {nl['dist_km'].median():.2f} km en médiane, "
        f"{nl['dist_km'].max():.2f} km au plus ; "
        f"{len(loin)} au-delà de {cfg.graphe.rattachement_max_km} km.",
        "",
        "## Contrôle contre les tracés OSRM",
        "",
        f"**Topologie** (réussi / échoué) : le plus court chemin en km sur le RTSS ne dépasse pas "
        f"{p.ratio_longueur_max} × la longueur du tracé OSRM, pour au moins {pct(p.part_liaisons_min)} des "
        f"liaisons interurbaines (tracé ≥ {p.seuil_interurbain_km:.0f} km). Mesuré en km, ce test ne dépend "
        "d'aucune hypothèse de vitesse.",
        "",
        f"**Accord de route** (information) : au moins {pct(p.recouvrement_min)} du chemin le plus rapide "
        f"à moins de {p.tampon_recouvrement_m:.0f} m du tracé OSRM. Il dépend des vitesses supposées ; le "
        "choix de route reviendra au modèle calé.",
        "",
        "| Indicateur | Valeur |",
        "|---|---|",
        f"| Liaisons comparées (hors « hors Québec ») | {s['liaisons']} |",
        f"| Interurbaines sans détour | {s['ok_interurbaines']} / {s['interurbaines']} "
        f"({pct(s['part_ok_interurbaines'])}) |",
        f"| Ratio km médian (interurbaines) | {s['ratio_km_median']:.3f} |",
        f"| Urbaines sans détour | {pct(s['part_ok_urbaines'])} sur {s['urbaines']} |",
        f"| Sans chemin | {s['sans_chemin']} |",
        f"| Accord de route avec OSRM | {pct(s['accord_route'])} (recouvrement médian "
        f"{pct(s['recouvrement_median'])}) |",
        f"| **Topologie (seuil {pct(p.part_liaisons_min)})** | **{'réussi' if s['passe'] else 'échoué'}** |",
        "",
        "Les liaisons urbaines courtes passent surtout par des rues municipales, absentes du RTSS : "
        "leur écart est une limite des données, pas de la topologie.",
        "",
        "## Décision de routage par liaison",
        "",
        f"- **{(resultat['routage'] == 'rtss').sum()} liaisons** sans détour sur le RTSS recevront "
        "des routes "
        "alternatives tirées du RTSS (colonne `routage` = rtss).",
        f"- **{(resultat['routage'] == 'osrm').sum()} liaisons** gardent leur route OSRM actuelle "
        "comme route "
        "unique (`routage` = osrm) : routes hors du réseau du MTQ (Baie-James, traversiers), parties hors "
        "Québec, rangs et rues municipales.",
        "",
    ]
    if len(loin):
        lignes += ["## Nœuds loin du réseau RTSS", "",
                   "| Nœud | Type | Distance (km) |", "|---|---|---|"]
        lignes += [f"| {r.NOM} | {r.TYPE} | {r.dist_km:.2f} |"
                   for r in loin.sort_values("dist_km").itertuples()]
        lignes.append("")
    hors = resultat[(resultat["topologie"] != "ok") & (resultat["statut"] != "hors_quebec")]
    if len(hors):
        lignes += ["## Liaisons avec détour (couche controle_liaisons, à vérifier dans QGIS)", "",
                   "| Liaison | Type | km OSRM | km RTSS | Ratio km | Recouvrement |",
                   "|---|---|---|---|---|---|"]
        for r in hors.sort_values("ratio_km", ascending=False).itertuples():
            rec = pct(r.recouvrement) if r.recouvrement == r.recouvrement else "–"
            lignes.append(f"| {r.ID_ARC} · {r.VILLE_A} – {r.VILLE_B} | {r.type} | {r.km_osrm:.1f} | "
                          f"{r.km_rtss:.1f} | {r.ratio_km:.2f} | {rec} |")
    return "\n".join(lignes) + "\n"
