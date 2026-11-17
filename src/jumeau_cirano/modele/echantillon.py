"""
echantillon.py
==============
Sous-artefact du couloir de l'échantillon (emprise de configs/reseau.yaml) : un calage complet en
quelques minutes, pour mettre au point une accélération ou une ablation avant de la lancer sur tout
le Québec. Il dérive de l'ENR : il reste dans outputs/ (hors Git), comme l'artefact complet.

Règle : une paire de lieux n'entre dans l'échantillon que si TOUS ses itinéraires n'empruntent que
des routes entièrement comprises dans l'emprise. Lui retirer une partie de ses itinéraires changerait
son choix de route, donc le modèle lui-même. Les sections gardées sont celles des routes gardées :
leurs comptages ne voient que la demande des paires gardées, et le fond local complète, comme ailleurs.
L'échantillon sert à comparer deux calculs sur les mêmes données, pas à estimer le modèle.

Pseudo-code
-----------
routes_dans_emprise()   routes de reseau_multi_routes.gpkg dont la géométrie est entièrement dans l'emprise
echantillonner()        paires dont tous les itinéraires restent sur ces routes → leurs itinéraires, paires
                        (indices renumérotés), groupes de camions ; postes et sections de ces routes
                        → mêmes fichiers que l'artefact, plus meta.json
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

from jumeau_cirano.config import ConfigReseau, Emprise, chemin_absolu


def routes_dans_emprise(cr: ConfigReseau, emprise: Emprise | None = None) -> set[str]:
    """Identifiants des routes du réseau à plusieurs routes entièrement comprises dans l'emprise."""
    e = emprise or cr.echantillon
    routes = gpd.read_file(chemin_absolu(cr.sortie), layer="routes", columns=["ID_ROUTE"]).to_crs("EPSG:4326")
    dedans = routes.within(shapely.box(e.lon_min, e.lat_min, e.lon_max, e.lat_max))
    return set(routes.loc[dedans, "ID_ROUTE"])


def echantillonner(source: Path, cible: Path, gardees: set[str], emprise: Emprise | None = None) -> dict:
    """Écrit dans `cible` le sous-artefact des paires dont tous les itinéraires restent sur `gardees`.

    Renvoie les effectifs (écrits aussi dans meta.json).
    """
    routes = pd.read_parquet(source / "routes.parquet")
    chemins = pd.read_parquet(source / "chemins.parquet")
    paires = pd.read_parquet(source / "paires.parquet").reset_index(drop=True)
    groupes = pd.read_parquet(source / "groupes.parquet")
    postes = pd.read_parquet(source / "postes.parquet")
    sections = pd.read_parquet(source / "sections.parquet")
    rs = pd.read_parquet(source / "routes_sections.parquet")

    dedans = chemins["routes"].str.split("|").map(lambda liste: all(r in gardees for r in liste))
    paire_gardee = dedans.groupby(chemins["paire"]).all()
    garder = np.sort(paire_gardee.index[paire_gardee.to_numpy()].to_numpy())
    nouvel_indice = pd.Series(np.arange(len(garder)), index=garder)

    chemins = chemins[chemins["paire"].isin(garder)].assign(paire=lambda c: c["paire"].map(nouvel_indice))
    groupes = groupes[groupes["paire"].isin(garder)].assign(paire=lambda g: g["paire"].map(nouvel_indice))
    routes = routes[routes["ID_ROUTE"].isin(gardees)]
    rs = rs[rs["ID_ROUTE"].isin(gardees)]
    sections = sections[sections["ide_sectn_trafc"].isin(set(rs["ide_sectn_trafc"]))]
    postes = postes[postes["ID_ROUTE"].isin(gardees)]

    cible.mkdir(parents=True, exist_ok=True)
    routes.reset_index(drop=True).to_parquet(cible / "routes.parquet")
    paires.iloc[garder].reset_index(drop=True).to_parquet(cible / "paires.parquet")
    chemins.reset_index(drop=True).to_parquet(cible / "chemins.parquet")
    groupes.reset_index(drop=True).to_parquet(cible / "groupes.parquet")
    postes.reset_index(drop=True).to_parquet(cible / "postes.parquet")
    sections.reset_index(drop=True).to_parquet(cible / "sections.parquet")
    rs.reset_index(drop=True).to_parquet(cible / "routes_sections.parquet")
    meta = {"genere_le": date.today().isoformat(), "source": source.name,
            "emprise": None if emprise is None else emprise.model_dump(),
            "routes": len(routes), "paires": len(garder), "paires_avec_chemin": int(chemins["paire"].nunique()),
            "chemins": len(chemins), "postes_localises": len(postes), "groupes": len(groupes),
            "camions": int(groupes["n"].sum()), "sections_mesurees": int(sections["observee"].sum())}
    (cible / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta
