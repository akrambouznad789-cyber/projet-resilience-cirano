"""Socle : configuration YAML validée, réseau jouet cohérent, CLI qui répond."""

from __future__ import annotations

import networkx as nx
import pytest
from pydantic import ValidationError

from jumeau_cirano import __version__
from jumeau_cirano.cli import main
from jumeau_cirano.config import Schema, charger


class ExempleConfig(Schema):
    nom: str
    tolerance_m: float = 1.0


def test_config_valide(tmp_path):
    f = tmp_path / "ex.yaml"
    f.write_text("nom: essai\ntolerance_m: 2.5\n", encoding="utf-8")
    c = charger(f, ExempleConfig)
    assert c.nom == "essai" and c.tolerance_m == 2.5


def test_config_refuse_cle_inconnue(tmp_path):
    f = tmp_path / "ex.yaml"
    f.write_text("nom: essai\ntolerence_m: 2.5\n", encoding="utf-8")   # faute de frappe volontaire
    with pytest.raises(ValidationError):
        charger(f, ExempleConfig)


def test_reseau_jouet(reseau_jouet):
    assert nx.shortest_path(reseau_jouet, "A", "D", weight="minutes") == ["A", "B", "D"]
    assert nx.shortest_path(reseau_jouet, "A", "D", weight="km") == ["A", "C", "D"]


def test_cli_version(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
    assert __version__ in capsys.readouterr().out
