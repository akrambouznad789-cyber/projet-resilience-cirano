"""
jumeau_cirano
=============
Jumeau numérique du camionnage québécois (projet CIRANO).

Trois briques, une responsabilité par module :
- reseau/      : graphe routier RTSS, routes alternatives par liaison, sections de comptage
- modele/      : affectation paramétrique des camions (PyTorch), calibration
- scenarios/   : fermetures de liaisons et criticité (résilience)

Les données confidentielles de l'ENR ne sont jamais lues par les tests : ceux-ci
tournent sur des réseaux synthétiques.
"""

__version__ = "0.1.0"
