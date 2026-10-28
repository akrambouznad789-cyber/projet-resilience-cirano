"""
synthetique.py
==============
Paquet de service synthétique : le réseau jouet des tests, sans aucune donnée de l'ENR.

    A ══ 1-1 (autoroute, 60 min) ══ B ══ 2-1 (autoroute, 30 min) ══ C
      ── 1-2 (nationale, 55 min) ──     ── 2-2 (nationale, 32 min) ──

Il sert aux tests de l'API, au test du conteneur dans la CI, et à l'équipe du jumeau officiel
pour brancher l'API avant d'avoir accès au vrai paquet (confidentiel).

Usage : python -m jumeau_cirano.api.synthetique <dossier>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

from jumeau_cirano.api.paquet import ecrire_paquet

ROUTES = pd.DataFrame({
    "ID_ROUTE": ["1-1", "1-2", "2-1", "2-2"], "ID_ARC": [1, 1, 2, 2], "rang": [1, 2, 1, 2], "source": "rtss",
    "km": [100.0, 50.0, 50.0, 40.0], "minutes": [60.0, 55.0, 30.0, 32.0], "km_autoroute": [100.0, 0.0, 50.0, 0.0],
    "km_estime": 0.0, "routes": ["A-20", "R-132", "A-20", "R-132"],
})
CHEMINS = [(0, ["1-1"]), (0, ["1-2"]), (1, ["2-1"]), (1, ["2-2"]),
           (2, ["1-1", "2-1"]), (2, ["1-1", "2-2"]), (2, ["1-2", "2-1"]), (2, ["1-2", "2-2"])]
BOUTS = {0: ("A", "B"), 1: ("B", "C"), 2: ("A", "C")}
LIAISONS = pd.DataFrame({"ID_ARC": [1, 2], "ID_A": ["A", "B"], "ID_B": ["B", "C"],
                         "VILLE_A": ["Aville", "Beauport"], "VILLE_B": ["Beauport", "Cantley"]})
PARAMETRES = {"mu": 0.1, "beta_hors_autoroute": 0.2, "beta_estime": 0.0, "croissance": 1.0, "erreur_postes": 0.05,
              "fond_10": 100.0, "fond_20": 50.0, "fond_30": 1.0, "fond_40": 1.0, "fond_autre": 1.0}


def ecrire(dossier: Path) -> Path:
    """Écrit le paquet synthétique dans `dossier` (classement de criticité compris) et le renvoie."""
    from jumeau_cirano.api.service import Service
    from jumeau_cirano.scenarios.resilience import toutes_les_fermetures

    dossier = Path(dossier)
    art = dossier / "artefact"
    art.mkdir(parents=True, exist_ok=True)
    ROUTES.to_parquet(art / "routes.parquet", index=False)
    pd.DataFrame({"paire": [p for p, _ in CHEMINS], "routes": ["|".join(r) for _, r in CHEMINS],
                  "entree": [BOUTS[p][0] for p, _ in CHEMINS], "sortie": [BOUTS[p][1] for p, _ in CHEMINS],
                  "km_acces": 0.0, "minutes_acces": 0.0, "ps": 1.0}).to_parquet(art / "chemins.parquet", index=False)
    pd.DataFrame({"o_pt": [0, 1, 0], "d_pt": [1, 2, 2], "n3": [36500.0, 18250.0, 7300.0],
                  "t3": [365e3, 182.5e3, 73e3], "nobs": [10, 5, 4]}).to_parquet(art / "paires.parquet", index=False)
    pd.DataFrame({"ID_ROUTE": ROUTES["ID_ROUTE"], "ide_sectn_trafc": ["S1", "S2", "S3", "S4"],
                  "part": 1.0}).to_parquet(art / "routes_sections.parquet", index=False)
    pd.DataFrame({"ide_sectn_trafc": ["S1", "S2", "S3", "S4"], "classe": ["10", "20", "10", "20"],
                  "observee": True, "camions_obs": [180.0, 110.0, 150.0, 90.0]}).to_parquet(art / "sections.parquet",
                                                                                               index=False)
    pd.DataFrame({"code": ["P1", "P3"], "ID_ROUTE": ["1-2", "2-2"]}).to_parquet(art / "postes.parquet", index=False)
    pd.DataFrame({"paire": [0, 1, 2], "poste": ["P1", "P3", None], "franchis": ["P1", "P3", "P3"],
                  "n": [10.0, 5.0, 4.0]}).to_parquet(art / "groupes.parquet", index=False)
    (art / "meta.json").write_text(json.dumps({"synthetique": True, "routes": 4, "paires": 3}), encoding="utf-8")
    calibration = {"genere_le": "2026-10-28", "objectif": {"nom": "démonstration", "poids_comptages": 1.0,
                                                          "poids_enquete": 1.0},
                   "parametres": PARAMETRES, "ic95": {"mu": [0.08, 0.12]}, "metriques_finales": {},
                   "validation_croisee": {}, "observations": {"sections_mesurees": 4, "camions_enquetes": 19}}
    ecrire_paquet(dossier, art, calibration, LIAISONS, [], "démonstration", 90.0, 3000.0)

    s = Service.charger(dossier)
    crit, _ = toutes_les_fermetures(s.cale, 90.0, 3000.0)
    crit = crit.merge(LIAISONS[["ID_ARC", "VILLE_A", "VILLE_B"]], on="ID_ARC")
    crit["coupure"] = crit["trajets_non_desservis"] > 0
    arcs = crit[crit["scenario"] == "liaison"].sort_values("delta_heures_equivalentes", ascending=False)
    crit = crit.merge(arcs[["ID_ARC"]].assign(rang=range(1, len(arcs) + 1)), on="ID_ARC", how="left")
    crit.round(2).to_csv(dossier / "criticite.csv", index=False)
    return dossier


if __name__ == "__main__":
    print(f"→ {ecrire(Path(sys.argv[1] if len(sys.argv) > 1 else 'paquet_synthetique'))}")
