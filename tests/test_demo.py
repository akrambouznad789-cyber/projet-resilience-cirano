"""Version de présentation : réduction des données publiques et sobriété du gabarit (données synthétiques)."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "scripts"))

import visualisation_demo as demo  # noqa: E402


@pytest.fixture
def publiques() -> dict:
    """Mêmes clés que les données de docs/jumeau/index.html, sur deux liaisons et trois tracés."""
    colonnes = ["scenario", "ID_ARC", "ID_ROUTE", "rang", "coupure", "camions_sur_liaison", "delta_heures_equivalentes",
                "delta_heures_meilleur", "delta_camions_heures", "delta_camions_km", "trajets_non_desservis",
                "tonnes_sur_liaison", "valeur_sur_liaison", "cout_perturbation"]
    trace = {"type": "LineString", "coordinates": [[-73.0, 45.0], [-72.0, 46.0]]}
    routes = [{"type": "Feature", "geometry": trace,
               "properties": {"i": i, "ID_ROUTE": r, "arc": a, "rang": 1, "km": 10.0, "min": 8.0, "auto": 5.0,
                              "nums": "A-20", "src": "rtss", "multi": 0, "schema": 0, "a": "x", "b": "y"}}
              for i, (r, a) in enumerate([("1-1", 1), ("2-1", 2), ("2-2", 2)])]
    return {
        "meta": {"genere_le": "2026-01-02", "publique": True, "n_liaisons": 2, "n_routes": 3, "n_sections_mesurees": 7},
        "fond": {"ocean": {}, "lacs": {}, "admin": {}},
        "routes": {"type": "FeatureCollection", "features": routes},
        "noeuds": [{"id": "1", "nom": "Montréal", "type": "municipalite", "lon": -73.5, "lat": 45.5},
                   {"id": "2", "nom": "Petiteville", "type": "municipalite", "lon": -73.0, "lat": 45.0},
                   {"id": "F1", "nom": "Ontario", "type": "frontiere_canada", "lon": -79.0, "lat": 45.0},
                   {"id": "F2", "nom": "Nunavut", "type": "frontiere_canada", "lon": -70.0, "lat": 62.0}],
        "liaisons": {"ID_ARC": [1, 2], "VILLE_A": ["Saint-Hyacinthe", "Noeud_Drum"],
                     "VILLE_B": ["Drummondville", "Ontario"]},
        "cale": {"flux": [100.0, 40.0, 60.0], "observations": {"camions_enquetes": 50},
                 "validation": {"modèle calé": {"erreur_mediane": {"moyenne": 0.44, "ecart_type": 0.1}}},
                 "parametres": {"mu": 0.08}, "ic95": {}, "metriques": {}, "objectif": {}, "sections": {}},
        "scenarios": {
            "colonnes": colonnes,
            "lignes": [["liaison", 1, "", 2, False, 36500, 0, 7300, 0, 0, 0, 0, 3000, 900],
                       ["liaison", 2, "", 1, True, 73000, 0, 3650, 0, 0, 18250, 0, 3000, 900],
                       ["route", 2, "2-1", 1, False, 7300, 0, 730, 0, 0, 0, 0, 3000, 900]],
            "reports": {"L1": [["2-1", "2-2"], [12.0, 4.0]], "R2-1": [["1-1"], [9.0]]},
            "robustesse": {},
        },
        "moteur": {"secret": 1}, "prereglages": {"équilibre": {}}, "theta_cale": {"mu": 0.08},
        "code": {"1": ["fichier", "extrait"]},
    }


def test_liste_blanche(publiques: dict) -> None:
    sortie = demo.reduire(publiques)
    assert set(sortie) == {"meta", "fond", "routes", "noeuds", "flux", "liaisons"}
    texte = json.dumps(sortie, ensure_ascii=False)
    for interdit in ("moteur", "prereglages", "parametres", "theta", "secret", "cout_perturbation",
                     "valeur_sur_liaison", "ic95"):
        assert interdit not in texte
    assert set(sortie["routes"]["features"][0]["properties"]) == {"i", "arc", "km", "nums"}


def test_fermetures_ramenees_a_un_jour(publiques: dict) -> None:
    premiere, seconde = demo.reduire(publiques)["liaisons"]
    assert (premiere["camions_jour"], premiere["heures_jour"], premiere["sans_itineraire"]) == (100, 20, False)
    assert (seconde["camions_jour"], seconde["sans_chemin_jour"], seconde["sans_itineraire"]) == (200, 50, True)


def test_seules_les_fermetures_de_liaison_sont_gardees(publiques: dict) -> None:
    assert [f["id"] for f in demo.reduire(publiques)["liaisons"]] == [1, 2]


def test_detours_en_indices_et_filtres(publiques: dict) -> None:
    premiere, seconde = demo.reduire(publiques)["liaisons"]
    assert premiere["detours"] == [[1], [12.0]]            # 4 camions/j : sous le seuil
    assert seconde["detours"] == [[], []]                  # liaison sans écart publié


def test_noms_lisibles(publiques: dict) -> None:
    assert demo.nom_lisible("Noeud_Drum") == "Carrefour de Drummondville"
    assert demo.nom_lisible("Nouvelle-Ecosse") == "Nouvelle-Écosse"
    assert demo.nom_lisible("Sherbrooke") == "Sherbrooke"
    assert demo.reduire(publiques)["liaisons"][1]["a"] == "Carrefour de Drummondville"


def test_noeuds_affiches(publiques: dict) -> None:
    noms = [n["nom"] for n in demo.reduire(publiques)["noeuds"]]
    assert noms == ["Montréal", "Ontario"]                 # ni petite ville, ni frontière lointaine


def test_exemple_par_defaut(publiques: dict) -> None:
    assert demo.reduire(publiques)["meta"]["exemple"] == 1
    publiques["liaisons"]["VILLE_A"][0] = "Ailleurs"
    assert demo.reduire(publiques)["meta"]["exemple"] == 1  # à défaut : la première liaison


def test_refuse_une_page_locale(tmp_path: Path) -> None:
    page = tmp_path / "index.html"
    page.write_text('<script id="donnees" type="application/json">{"meta":{"publique":false}}</script>',
                    encoding="utf-8")
    with pytest.raises(ValueError, match="version publique"):
        demo.publiques(page)


def test_gabarit_sans_jargon() -> None:
    gabarit = (RACINE / "scripts" / "gabarits" / "demo.html").read_text(encoding="utf-8")
    for terme in (r"logsum", r"\bNLL\b", r"Kendall", r"bootstrap", r"DJMA", r"[Vv]olet \d", r"\bR²", r"μ", r"θ",
                  r"\bClique\b", r"\bChoisis\b", r"\b[Tt]u\b", r"\bton navigateur\b", r"\$/"):
        assert re.search(terme, gabarit) is None, terme
