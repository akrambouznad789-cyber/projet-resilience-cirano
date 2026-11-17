"""Règles de la complétion du Module 1 (scripts/completion_donnees_randomforest.py), sur des séries synthétiques.

Retour du professeur (2026-11-17) : prolongée sur plusieurs années à partir de 2 ou 3 comptages, la droite du
gradient local tombait à 0 camion. Une valeur extrapolée reste désormais dans l'intervalle [min, max] des valeurs
mesurées sur la section, et controler_completion() refuse une sortie qui viole une règle.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

CHEMIN = Path(__file__).resolve().parents[1] / "scripts" / "completion_donnees_randomforest.py"
ANNEES = list(range(2025, 2015, -1))          # ordre du fichier du MTQ : annee_1 = 2025 … annee_10 = 2016


@pytest.fixture(scope="module")
def completion():
    spec = importlib.util.spec_from_file_location("completion_donnees_randomforest", CHEMIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def serie(valeurs: dict[int, float]) -> np.ndarray:
    """Série dans l'ordre du fichier (2025 → 2016) ; NaN pour une année non mesurée."""
    return np.array([valeurs.get(a, np.nan) for a in ANNEES], dtype=float)


def rang(*annees: int) -> list[int]:
    return [ANNEES.index(a) for a in annees]


def test_une_baisse_ne_tombe_plus_a_zero(completion):
    # Section 33889 (route 132) : 9,2 % de camions en 2017, 6,0 % en 2018 ; l'ancienne règle donnait 0 dès 2020
    vals = serie({2017: 9.2, 2018: 6.0})
    ancienne, _ = completion._extrapoler_extremites_detail(vals, ANNEES, borner=False)
    nouvelle, bornees = completion._extrapoler_extremites_detail(vals, ANNEES)
    assert ancienne[rang(2025)[0]] == 0.0
    assert nouvelle[rang(2025)[0]] == pytest.approx(6.0)
    assert nouvelle.min() == pytest.approx(6.0) and nouvelle.max() == pytest.approx(9.2)
    assert bornees.sum() == 8                  # 2016 au-dessus du maximum, 2019 à 2025 sous le minimum


def test_une_hausse_ne_depasse_pas_le_maximum(completion):
    nouvelle, _ = completion._extrapoler_extremites_detail(serie({2016: 5.0, 2017: 8.0, 2018: 11.0}), ANNEES)
    assert nouvelle.max() == pytest.approx(11.0)
    assert nouvelle[rang(2025)[0]] == pytest.approx(11.0)


def test_une_tendance_qui_reste_dans_l_intervalle_est_gardee(completion):
    # Forte baisse puis reprise : le gradient local de la fin (+300 par an) reste dans [5 300 ; 8 700]
    vals = serie({2017: 8700, 2018: 7400, 2019: 6200, 2020: 5300, 2021: 5600, 2022: 5900})
    ancienne, _ = completion._extrapoler_extremites_detail(vals, ANNEES, borner=False)
    nouvelle, bornees = completion._extrapoler_extremites_detail(vals, ANNEES)
    fin = rang(2023, 2024, 2025)
    np.testing.assert_allclose(nouvelle[fin], ancienne[fin])
    np.testing.assert_allclose(nouvelle[fin], [6200, 6500, 6800])
    assert not bornees[fin].any()
    assert bornees[rang(2016)[0]] and nouvelle[rang(2016)[0]] == pytest.approx(8700)


def test_une_seule_mesure_est_recopiee(completion):
    nouvelle, bornees = completion._extrapoler_extremites_detail(serie({2020: 7.5}), ANNEES)
    assert np.all(nouvelle == 7.5) and not bornees.any()


def test_serie_complete_intacte_et_mesures_jamais_modifiees(completion):
    complete = np.linspace(1000, 1900, 10)
    v, methode, n = completion._completer_serie(complete.copy(), ANNEES)
    np.testing.assert_array_equal(v, complete)
    assert (methode, n) == ("complet", 0)

    trouee = serie({2016: 4.0, 2019: 3.0, 2020: 9.0, 2023: 6.5})
    v, methode, _ = completion._completer_serie(trouee.copy(), ANNEES)
    connues = ~np.isnan(trouee)
    np.testing.assert_array_equal(v[connues], trouee[connues])
    assert methode == "extrapolation" and 3.0 <= v.min() and v.max() <= 9.0


def table(completion, djma: np.ndarray, cam: np.ndarray, methode_cam: str = "extrapolation") -> pd.DataFrame:
    """Une section au format du fichier complété (colonnes lues par controler_completion)."""
    t = pd.DataFrame([np.concatenate([djma, cam])], columns=completion.DJMA_COLS + completion.CAM_COLS)
    return t.assign(ide_sectn_trafc=33889, methode_djma="extrapolation", methode_cam=methode_cam,
                    n_bornees_djma=0, n_bornees_cam=0)


def test_controle_accepte_une_sortie_conforme(completion):
    djma_brut, cam_brut = serie({2017: 1800, 2018: 1700}), serie({2017: 9.2, 2018: 6.0})
    djma, _ = completion._extrapoler_extremites_detail(djma_brut, ANNEES)
    cam, _ = completion._extrapoler_extremites_detail(cam_brut, ANNEES)
    brut = table(completion, djma_brut, cam_brut)[completion.DJMA_COLS + completion.CAM_COLS]
    completion.controler_completion(table(completion, djma, cam), brut)


@pytest.mark.parametrize("defaut, message", [
    ("zero", "≤ 0 malgré une mesure"),
    ("publiee", "valeur publiée modifiée"),
    ("hors", "hors de l'intervalle mesuré"),
    ("manquante", "valeurs manquantes"),
])
def test_controle_refuse_une_sortie_fautive(completion, defaut, message):
    djma_brut, cam_brut = serie({2017: 1800, 2018: 1700}), serie({2017: 9.2, 2018: 6.0})
    djma = completion._extrapoler_extremites_detail(djma_brut, ANNEES)[0]
    cam, _ = completion._extrapoler_extremites_detail(cam_brut, ANNEES, borner=False)   # ancienne règle
    if defaut == "publiee":
        cam = completion._extrapoler_extremites_detail(cam_brut, ANNEES)[0]
        cam[rang(2018)[0]] = 7.0
    elif defaut == "hors":
        cam = completion._extrapoler_extremites_detail(cam_brut, ANNEES)[0]
        cam[rang(2016)[0]] = 12.4
    elif defaut == "manquante":
        cam = completion._extrapoler_extremites_detail(cam_brut, ANNEES)[0]
        cam[rang(2025)[0]] = np.nan
    brut = table(completion, djma_brut, cam_brut)[completion.DJMA_COLS + completion.CAM_COLS]
    with pytest.raises(ValueError, match=message):
        completion.controler_completion(table(completion, djma, cam), brut)
