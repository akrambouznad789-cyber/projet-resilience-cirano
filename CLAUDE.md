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
- `/docs/` : Documentation et notes de recherche

## Pipeline
- Étape 1 (Ingestion) : `python scripts/1_ingestion.py`
- Étape 2 (Noeuds) : `python scripts/2_extraction_noeuds.py`
- Étape 3 (Liens) : `python scripts/3_liaison_topologique.py`
- Étape 4 (Visualisation) : `streamlit run scripts/4_visualisation.py`

## Gestion du contexte
- Utiliser `/compact` régulièrement entre les agents
- Écrire l'état d'avancement dans `docs/progress.md` avant chaque `/compact`
- Une session = un agent = un objectif clair

## Commandes Git
- Avant tout travail : `git checkout -b feature/nom-agent`
- Après validation : `git add . && git commit -m "message clair"`
