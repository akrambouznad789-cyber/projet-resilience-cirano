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
- `/docs/` : Documentation et notes de recherche ; `docs/jumeau.md` (fiche du modèle), `docs/progress.md` (journal), `docs/donnees_ml.md` (atlas des données pour l'apprentissage automatique), `docs/pistes_ml.md` (carte des pistes de recherche)
- `/src/jumeau_cirano/`, `/configs/`, `/tests/` : le jumeau (Volet 3), ses configurations YAML et ses tests

## Pipeline
Volets 1 et 2 (venv `~/.venvs/portfolio`) :
- Complétion des comptages : `python scripts/completion_donnees_randomforest.py` (`--echantillon` d'abord : `outputs/module1/echantillon/`) ; contrôles intégrés, la sortie est refusée si une règle est violée
- Routage et rattachement des sections : `python scripts/algo_jointure_routes_liens.py` (OSRM)
- Temps de parcours de chaque liaison, sans refaire le routage : `python scripts/temps_parcours_liaisons.py` (`--echantillon` d'abord : `outputs/module1/echantillon/`), puis `calcul_djma_methodes.py` pour reporter les colonnes ; vitesses camion du Module 3 (`configs/reseau.yaml`)
- Après une nouvelle complétion, sans refaire le routage : `python scripts/actualiser_valeurs_liaisons.py --reference <ancien debits_completes.gpkg>`
- Trafic par liaison (m1 à m4) : `python scripts/calcul_djma_methodes.py`, puis `comparaison_methodes_djma.py`
- Pages : `python scripts/visualisation_volet1.py` ; `ENR_DIR=… python scripts/visualisation_volet2.py --publique`

Volet 3, le jumeau (paquet `src/jumeau_cirano/`, environnement `uv`, Python 3.12) :
- `uv run jumeau reseau` → `uv run jumeau preparer` (ENR) → `uv run jumeau calibrer` → `uv run jumeau scenarios`
- Calage sur l'échantillon (mise au point, ablations) : `uv run jumeau calibrer --echantillon` → `outputs/jumeau/echantillon/` (ENR, jamais publié) ; un essai hors production prend une config avec `publier: false`
- Page technique : `uv run python scripts/visualisation_jumeau.py --publique`
- Page de présentation : `uv run python scripts/visualisation_demo.py` (lit la page publique, ni artefact ni ENR)
- Noms publics dans les pages et le README : Module 1 (Estimation du trafic par liaison), Module 2 (Valorisation économique du fret), Module 3 (Jumeau numérique et analyse de résilience). Les chemins et le code gardent `volet`.
- API : `uv run jumeau exporter`, puis `JUMEAU_ARTEFACT=outputs/jumeau/service uv run jumeau api` ; contrat : `uv run jumeau openapi`
- Tests : `uv run pytest` (réseaux synthétiques, jamais l'ENR)
- Thème des pages : `scripts/gabarits/theme/jetons.json` → `python scripts/theme_pages.py`
- Atlas des données pour le ML : `uv run python scripts/atlas_donnees_ml.py` → `docs/donnees_ml/agregats.json`, `figures/ml_*.png` (après `jumeau reseau` ; ni ENR ni modèle)
- Tables d'apprentissage : `uv run jumeau tables [--echantillon]` → `outputs/ml/*.parquet`, `docs/donnees_ml/tables.json` (après `jumeau reseau` ; ni ENR ni modèle ; cibles = valeurs publiées seulement, lire par `tables.exemples()`)

## Gestion du contexte
- Utiliser `/compact` régulièrement entre les agents
- Écrire l'état d'avancement dans `docs/progress.md` avant chaque `/compact`
- Une session = un agent = un objectif clair

## Commandes Git
- Avant tout travail : `git checkout -b feature/nom-agent`
- Après validation : `git add . && git commit -m "message clair"`

## Labo ML (session parallèle)
- Un labo d'apprentissage automatique travaille sur ce dépôt depuis un worktree séparé, `~/projects/jumeau-labo`, sur des branches `feature/labo-NN-nom`. Ne jamais extraire, modifier, rebaser ni supprimer une branche `feature/labo-*`.
- Ses fichiers : `labos/` (code des expériences), `docs/labos/` (pages publiques, CSV, figures), `scripts/visualisation_labos.py`, `scripts/gabarits/labo.html`. Ne pas les modifier depuis ici.
- Les fichiers partagés (`README.md`, `docs/progress.md`, la documentation des modules, `pyproject.toml`, `uv.lock`) ne reçoivent les ajouts du labo qu'à sa fusion, par une PR qu'Akram fusionne lui-même.
- Le labo a sa propre base MLflow et n'écrit jamais dans `data/` ni dans `outputs/` de ce dossier.
- Une seule session publie à la fois : avant de pousser sur `main`, demander à Akram.
