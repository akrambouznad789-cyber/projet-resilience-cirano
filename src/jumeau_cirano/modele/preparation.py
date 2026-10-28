"""
preparation.py
==============
Assemble l'artefact du modèle dans outputs/jumeau/artefact/ (Parquet, local : il
dérive de l'ENR). La calibration, les scénarios et l'API ne lisent que lui.

Pseudo-code
-----------
routes  ← couche « routes » de reseau_multi_routes.gpkg ; liaisons ← graphe du Volet 1
demande ← lieux, camions, postes de l'ENR
paires  = (lieu de ramassage, lieu de livraison) : trajets par an (n3), camions enquêtés
chemins = itinéraires de chaque paire (Yen sur le graphe des routes, accès aux frontières) + taille de chemin
postes  → route : la plus proche du lieu du poste parmi celles qui portent son numéro de route
groupes = (paire, poste d'interception, postes franchis localisés) → nombre de camions
sections mesurées, et incidence route → section
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import geopandas as gpd
import pandas as pd

from jumeau_cirano.config import ConfigModele, ConfigReseau, charger, chemin_absolu
from jumeau_cirano.donnees.demande import construire_demande, volet2
from jumeau_cirano.donnees.liaisons import charger_liaisons, charger_noeuds
from jumeau_cirano.reseau.chemins import ensembles_par_paire, graphe_des_routes, itineraires, taille_de_chemin


def postes_sur_routes(postes: pd.DataFrame, routes: gpd.GeoDataFrame, crs: str, interceptes: pd.DataFrame,
                      routes_par_paire: dict[int, set[str]], rayon_km: float = 15.0) -> pd.DataFrame:
    """code → ID_ROUTE, par les données : parmi les routes à moins de `rayon_km` du lieu du poste, celle que
    peuvent emprunter le plus de camions interceptés à ce poste (au moins un de leurs itinéraires y passe).

    Les coordonnées des postes sont celles de la localité (approximatives) et les routes OSRM n'ont pas de
    numéro de route : la route la plus proche « du bon numéro » se trompe souvent de liaison. En cas
    d'égalité de couverture, la route qui porte le numéro du poste, puis la plus proche, l'emporte.
    `interceptes` : poste, paire, n (camions).
    """
    v2 = volet2()
    lignes = []
    for r in postes.itertuples():
        lieu = v2.LOCALITES_POSTES.get(v2.normaliser(r.libelle.split(" (")[0]))
        if lieu is None:
            continue
        point = gpd.GeoSeries(gpd.points_from_xy([lieu[1]], [lieu[0]]), crs="EPSG:4326").to_crs(crs).iloc[0]
        dist = routes.geometry.distance(point)
        proches = routes[dist <= rayon_km * 1000].assign(dist=dist)
        if proches.empty:
            proches = routes.assign(dist=dist).nsmallest(1, "dist")
        ici = interceptes[interceptes["poste"] == r.code]
        total = max(float(ici["n"].sum()), 1.0)
        proches["couverture"] = [float(ici.loc[[rid in routes_par_paire.get(pa, ()) for pa in ici["paire"]], "n"].sum())
                                 / total for rid in proches["ID_ROUTE"]]
        numero = re.search(r"\(\s*[AR]-(\d+)", r.libelle)
        proches["meme_numero"] = (proches["routes"].fillna("").str.contains(rf"[AR]-{numero.group(1)}\b", regex=True)
                                  if numero else False)
        choix = proches.sort_values(["couverture", "meme_numero", "dist"], ascending=[False, False, True]).iloc[0]
        lignes.append({"code": r.code, "ID_ROUTE": choix["ID_ROUTE"], "dist_km": choix["dist"] / 1000,
                       "couverture": choix["couverture"], "localite": lieu[2], "libelle": r.libelle})
    return pd.DataFrame(lignes)


def preparer(cfg: ConfigModele) -> Path:
    """Construit et écrit l'artefact ; renvoie son dossier."""
    cr = charger(cfg.reseau, ConfigReseau)
    sortie_reseau = chemin_absolu(cr.sortie)
    routes = gpd.read_file(sortie_reseau, layer="routes")
    liaisons = charger_liaisons(cr)
    frontieres = set(charger_noeuds(cr).query("TYPE.str.startswith('frontiere')", engine="python")["ID"])

    print("  demande ENR")
    lieux, camions, postes = construire_demande()
    paires = camions.groupby(["o_pt", "d_pt"]).agg(n3=("n3", "sum"), nobs=("id", "size")).reset_index()
    candidats = lieux.set_index("pt")["candidats"]
    besoin = {(no, nd) for o, d in zip(paires["o_pt"], paires["d_pt"], strict=True)
              for no, _ in candidats.loc[o] for nd, _ in candidats.loc[d] if no != nd}
    print(f"  ensembles de chemins : {len(besoin):,} paires de nœuds")
    h = graphe_des_routes(routes, liaisons)
    ensembles = ensembles_par_paire(h, sorted(besoin), frontieres, cfg.chemins, routes)
    minutes = dict(zip(routes["ID_ROUTE"], routes["minutes"], strict=True))
    chemins = itineraires(paires, lieux, ensembles, minutes, cfg.chemins)
    chemins["ps"] = taille_de_chemin(chemins, dict(zip(routes["ID_ROUTE"], routes["km"], strict=True)))
    print(f"  {len(chemins):,} itinéraires pour {chemins['paire'].nunique():,} paires de lieux sur {len(paires):,}")

    print("  postes et groupes de camions")
    cle = camions.merge(paires.reset_index(names="paire")[["paire", "o_pt", "d_pt"]], on=["o_pt", "d_pt"])
    routes_par_paire: dict[int, set[str]] = {}
    for pa, ch in zip(chemins["paire"], chemins["routes"], strict=True):
        routes_par_paire.setdefault(int(pa), set()).update(ch)
    interceptes = cle.dropna(subset=["poste"]).groupby(["poste", "paire"]).size().rename("n").reset_index()
    places = postes_sur_routes(postes, routes, cr.crs, interceptes, routes_par_paire)
    par_poste = interceptes.groupby("poste")["n"].sum().reindex(places["code"]).to_numpy()
    couverture = (places["couverture"].to_numpy() * par_poste).sum() / interceptes["n"].sum()
    print(f"  couverture des postes (camions qui peuvent passer par leur poste) : {couverture:.0%}")
    connus = set(places["code"])
    cle["poste"] = cle["poste"].where(cle["poste"].isin(connus))
    cle["franchis"] = cle["postes_franchis"].map(lambda t: "|".join(s for s in t if s in connus))
    groupes = cle.groupby(["paire", "poste", "franchis"], dropna=False).agg(n=("id", "size"), n3=("n3", "sum"))

    dossier = chemin_absolu(cfg.dossier_sortie) / "artefact"
    dossier.mkdir(parents=True, exist_ok=True)
    attributs = ["ID_ROUTE", "ID_ARC", "rang", "source", "km", "minutes", "km_autoroute", "km_estime", "routes"]
    routes[attributs].to_parquet(dossier / "routes.parquet")
    paires.to_parquet(dossier / "paires.parquet")
    chemins.assign(routes=chemins["routes"].map("|".join)).to_parquet(dossier / "chemins.parquet")
    places.to_parquet(dossier / "postes.parquet")
    groupes.reset_index().to_parquet(dossier / "groupes.parquet")
    sections = gpd.read_file(sortie_reseau, layer="sections")
    sections.to_wkt().to_parquet(dossier / "sections.parquet")
    gpd.read_file(sortie_reseau, layer="routes_sections").to_parquet(dossier / "routes_sections.parquet")
    meta = {"genere_le": date.today().isoformat(), "routes": len(routes), "paires": len(paires),
            "paires_avec_chemin": int(chemins["paire"].nunique()), "chemins": len(chemins),
            "postes_localises": len(places), "groupes": len(groupes), "camions": int(len(camions)),
            "sections_mesurees": int(sections["observee"].sum())}
    (dossier / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return dossier
