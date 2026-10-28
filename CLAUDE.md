# Projet résilience réseau routier CIRANO

## Contexte
Projet de recherche sur la résilience des réseaux de transport multimodaux.
Collaboration : Polytechnique Montréal / CIRANO / Transport Canada / MTQ.
Chercheur principal : Akram Bouznad.

## Règles absolues
1. TOUJOURS demander un plan en pseudo-code avant d'écrire le moindre script.
2. TOUJOURS travailler sur `/data/sample/` avant de valider sur l'ensemble du Québec.
3. TOUJOURS créer une branche Git avant une nouvelle fonctionnalité.
4. Ne JAMAIS modifier les fichiers dans `/data/raw/`.

## Directives de développement
- Langage : Python 3.11+
- Librairies : Geopandas, NetworkX, Shapely, Pydeck, Streamlit
- Style : Code modulaire, typage explicite (Type Hints), docstrings en français
- Un fichier par agent, un agent par responsabilité

## Architecture
- `/data/raw/` : Données brutes MTQ (lecture seule)
- `/data/sample/` : Échantillon de test géographiquement restreint
- `/data/processed/` : nodes.geojson, edges.geojson
- `/scripts/` : Scripts Python modulaires
- `/notebooks/` : Exploration et prototypage
- `/qgis/` : Projets et styles QGIS
- `/outputs/` : Exports finaux
- `/docs/` : Documentation et notes de recherche ; `docs/jumeau.md` (fiche du modèle), `docs/progress.md` (journal)
- `/src/jumeau_cirano/`, `/configs/`, `/tests/` : le jumeau (Volet 3), ses configurations YAML et ses tests

## Pipeline
Volets 1 et 2 (venv `~/.venvs/portfolio`) :
- Complétion des comptages : `python scripts/completion_donnees_randomforest.py`
- Routage et rattachement des sections : `python scripts/algo_jointure_routes_liens.py` (OSRM)
- Trafic par liaison (m1 à m4) : `python scripts/calcul_djma_methodes.py`, puis `comparaison_methodes_djma.py`
- Pages : `python scripts/visualisation_volet1.py` ; `ENR_DIR=… python scripts/visualisation_volet2.py --publique`

Volet 3, le jumeau (paquet `src/jumeau_cirano/`, environnement `uv`, Python 3.12) :
- `uv run jumeau reseau` → `uv run jumeau preparer` (ENR) → `uv run jumeau calibrer` → `uv run jumeau scenarios`
- Page : `uv run python scripts/visualisation_jumeau.py --publique`
- API : `uv run jumeau exporter`, puis `JUMEAU_ARTEFACT=outputs/jumeau/service uv run jumeau api` ; contrat : `uv run jumeau openapi`
- Tests : `uv run pytest` (réseaux synthétiques, jamais l'ENR)
- Thème des pages : `scripts/gabarits/theme/jetons.json` → `python scripts/theme_pages.py`

## Gestion du contexte
- Utiliser `/compact` régulièrement entre les agents
- Écrire l'état d'avancement dans `docs/progress.md` avant chaque `/compact`
- Une session = un agent = un objectif clair

## Commandes Git
- Avant tout travail : `git checkout -b feature/nom-agent`
- Après validation : `git add . && git commit -m "message clair"`
