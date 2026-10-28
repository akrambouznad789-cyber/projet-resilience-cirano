"""
demande.py
==========
Demande de camionnage tirée de l'ENR 2006-2007. Confidentielle : tout ce qui en
dérive reste dans outputs/jumeau/, jamais dans Git, et les tests ne la lisent pas.

La lecture, le filtre Québec et la localisation des lieux (SDR 2001) sont ceux
du Volet 2, réutilisés par import de scripts/visualisation_volet2.py.

Pseudo-code
-----------
camions = filtre Québec (37 603) ∩ lieux localisés aux deux bouts
lieu québécois  → nœud municipal le plus proche
lieu hors Québec → frontières candidates, km d'accès = 1,2 × vol d'oiseau
postes franchis → MASTER_SITES_PASSED_LIST.csv, colonnes qc#### = « T »
poste d'interception → dcs_code s'il est au Québec (sinon aucun conditionnement)
trajets locaux (même nœud aux deux bouts) → exclus, comme au Volet 2
"""

from __future__ import annotations

import importlib
import sys
from types import ModuleType

import numpy as np
import pandas as pd

from jumeau_cirano.config import RACINE


def volet2() -> ModuleType:
    """Module scripts/visualisation_volet2.py (lecture de l'ENR, localisation, rattachement)."""
    dossier = str(RACINE / "scripts")
    if dossier not in sys.path:
        sys.path.insert(0, dossier)
    return importlib.import_module("visualisation_volet2")


def charger_postes_franchis(ids: pd.Series) -> pd.Series:
    """Pour chaque camion (id), le tuple trié des postes québécois franchis (codes en majuscules)."""
    v2 = volet2()
    table = pd.read_csv(v2.DIR_ENR / "MASTER_SITES_PASSED_LIST.csv", dtype=str,
                        usecols=lambda c: c == "nrsid" or c.lower().startswith("qc"))
    table = table[table["nrsid"].isin(set(ids.astype(str)))]
    colonnes = np.array([c.upper() for c in table.columns if c != "nrsid"])
    passe = table.drop(columns="nrsid").eq("T").to_numpy()
    return pd.Series([tuple(colonnes[ligne]) for ligne in passe], index=table["nrsid"].to_numpy())


def construire_demande() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """(lieux, camions, postes).

    lieux   : pt, prov, noeud (lieu québécois) ou candidats [(frontière, km d'accès)] (hors Québec)
    camions : id, o_pt, d_pt, n3 (trajets par an), t3 (tonnes par an), cat, poste (interception au Québec, sinon None),
              postes_franchis (tuple des postes QC franchis)
    postes  : code, libelle (« VERCHERES (A-30 EAST/EST: MONTREAL - SOREL) ») des postes du Québec
    """
    v2 = volet2()
    df = v2.classer_population(v2.charger_enr())
    df, points, _ = v2.localiser_points(df)
    _, _, noeuds = v2.charger_graphe()
    points = v2.rattacher_noeuds(points, noeuds)
    lieux = points.reset_index(names="pt")[["pt", "prov", "noeud", "candidats", "lon", "lat"]]

    c = df[df["garde"] & df["localise"]].copy()
    c["o_pt"], c["d_pt"] = c["o_pt"].astype(int), c["d_pt"].astype(int)
    noeud = lieux.set_index("pt")["noeud"]
    local = c["o_pt"].map(noeud).notna() & (c["o_pt"].map(noeud) == c["d_pt"].map(noeud))
    c = c[~local]
    c["poste"] = c["dcs_code"].where(c["dcs_code"].str.startswith("QC"))
    franchis = charger_postes_franchis(c["id"])
    c["postes_franchis"] = c["id"].astype(str).map(franchis).map(lambda t: t if isinstance(t, tuple) else ())
    camions = c[["id", "o_pt", "d_pt", "n3", "t3", "cat", "poste", "postes_franchis"]].reset_index(drop=True)
    qc = df[df["dcs_code"].str.startswith("QC")]
    postes = qc.groupby("dcs_code")["wp_dcs"].first().str.upper().str.strip().reset_index()
    postes.columns = ["code", "libelle"]
    return lieux, camions, postes
