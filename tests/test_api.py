"""API du jumeau sur le paquet synthétique (réseau jouet A–B–C) : aucune donnée de l'ENR."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from jumeau_cirano.api.synthetique import ecrire


@pytest.fixture(scope="module")
def client(tmp_path_factory, monkeypatch_module):
    paquet = ecrire(tmp_path_factory.mktemp("paquet"))
    monkeypatch_module.setenv("JUMEAU_ARTEFACT", str(paquet))
    from jumeau_cirano.api.app import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def monkeypatch_module():
    m = pytest.MonkeyPatch()
    yield m
    m.undo()


def test_sante_et_modele(client):
    r = client.get("/sante").json()
    assert r["statut"] == "ok" and r["routes"] == 4 and r["liaisons"] == 2 and r["itineraires"] == 8
    m = client.get("/modele").json()
    assert m["parametres"]["mu"] == pytest.approx(0.1) and m["ic95"]["mu"] == [0.08, 0.12]


def test_affectation_conserve_la_demande(client):
    r = client.post("/affectation", json={}).json()
    flux = {x["ID_ROUTE"]: x["camions_jour"] for x in r["routes"]}
    # A–B : 100 trajets/j + A–C : 20 trajets/j passent tous par la liaison 1 (routes 1-1 ou 1-2)
    assert flux["1-1"] + flux["1-2"] == pytest.approx(120.0)
    assert flux["2-1"] + flux["2-2"] == pytest.approx(70.0)
    assert set(r["metriques"]) >= {"r2_log", "erreur_mediane", "part_30", "nll_enquete"}


def test_theta_modifie_change_le_partage(client):
    cale = {x["ID_ROUTE"]: x["camions_jour"] for x in client.post("/affectation", json={}).json()["routes"]}
    r = client.post("/affectation", json={"theta": {"beta_hors_autoroute": 2.0}}).json()
    neuf = {x["ID_ROUTE"]: x["camions_jour"] for x in r["routes"]}
    assert r["theta"]["beta_hors_autoroute"] == 2.0 and neuf["1-1"] > cale["1-1"]   # nationale pénalisée


def test_scenario_report_et_coupure(client):
    r = client.post("/scenario", json={"routes": ["1-1"]}).json()
    reports = {x["ID_ROUTE"]: x["delta_camions_jour"] for x in r["reports"]}
    assert r["fermees"] == ["1-1"] and reports["1-2"] > 0 and reports["1-1"] < 0
    assert r["impacts"]["trajets_non_desservis"] == 0 and r["impacts"]["delta_heures_equivalentes"] > 0
    coupe = client.post("/scenario", json={"liaisons": [1]}).json()
    assert coupe["impacts"]["trajets_non_desservis"] == pytest.approx(36500 + 7300)    # A isolé


def test_erreurs_de_demande(client):
    assert client.post("/scenario", json={"liaisons": [99]}).status_code == 422
    assert client.post("/scenario", json={}).status_code == 422
    assert client.post("/affectation", json={"theta": {"mu": -1}}).status_code == 422
    assert client.post("/affectation", json={"theta": {"inconnu": 1}}).status_code == 422


def test_criticite_triee(client):
    r = client.get("/criticite", params={"limite": 5}).json()
    assert [x["ID_ARC"] for x in r] and r[0]["rang"] == 1
    assert r[0]["delta_heures_equivalentes"] >= r[-1]["delta_heures_equivalentes"]
    assert client.get("/criticite", params={"mesure": "rien"}).status_code == 422


def test_contrat_openapi_sans_donnees():
    from jumeau_cirano.api.app import app

    schema = app.openapi()
    assert {"/sante", "/modele", "/affectation", "/scenario", "/criticite"} <= set(schema["paths"])
