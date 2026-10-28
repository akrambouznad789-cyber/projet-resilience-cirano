"""
Fixtures partagées : un petit réseau synthétique, jamais les données de l'ENR.

Réseau jouet à 5 nœuds (A à E) : deux façons d'aller de A à D (par B, rapide, ou
par C, plus courte en km mais plus lente), puis D – E. Les tests du modèle
s'en servent pour vérifier des résultats connus d'avance.

    A ── B ── D ── E
     \\       /
      ── C ──
"""

from __future__ import annotations

import networkx as nx
import pytest


@pytest.fixture
def reseau_jouet() -> nx.Graph:
    """Graphe non orienté avec km et minutes sur chaque lien."""
    g = nx.Graph()
    for u, v, km, minutes in [
        ("A", "B", 50.0, 30.0),
        ("B", "D", 50.0, 30.0),
        ("A", "C", 40.0, 40.0),
        ("C", "D", 40.0, 40.0),
        ("D", "E", 20.0, 15.0),
    ]:
        g.add_edge(u, v, km=km, minutes=minutes)
    return g
