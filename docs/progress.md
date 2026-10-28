# Journal d'avancement du jumeau numérique

Une entrée par point de contrôle : ce qui a été décidé, ce que disent les chiffres, et ce qu'on referait autrement.
Le plan d'ensemble : réseau à plusieurs routes par liaison → modèle d'affectation calé → résilience → interface → API.

## Phase 0 · Socle (2026-10-27)

**Fait**
- Carte du Volet 1 (`6531659`) et SDR 2001 (`244820e`) commitées sur `feature/sdr-2001`.
- Paquet `src/jumeau_cirano/` (configuration YAML validée par pydantic, CLI à commandes importées à la demande).
- Environnement `uv` en Python 3.12 (`pyproject.toml`, `uv.lock`) : PyTorch 2.14 en version CPU, MLflow, FastAPI, pytest, ruff. Le venv `portfolio` des scripts existants n'est pas touché.
- Tests sur un réseau jouet à 5 nœuds ; CI GitHub (ruff + pytest) à chaque push.

**Revue critique**
- La configuration refuse toute clé inconnue : une faute de frappe dans un YAML échoue au lieu d'être ignorée en silence.
- Les chemins de données sont dans les YAML : on pourra substituer un autre réseau sans toucher au code. C'était la condition de modularité demandée pour le livrable du professeur, jamais remplie jusqu'ici.
- Deux environnements Python coexistent (`portfolio` en 3.14 pour les scripts, `.venv` en 3.12 pour le jumeau). C'est voulu pour ne rien casser, mais à terme les scripts du Volet 2 devraient passer dans le paquet.

## Phase 1a · Correctifs du Volet 1 (2026-10-28)

**Fait**
- `algo_jointure_routes_liens.py` : les couches `trajets_segments` et `trace_osrm` étaient écrites en `mode="a"` et s'empilaient à chaque exécution (9 855 lignes pour 3 306 rattachements, 921 tracés pour 307 arcs). Écriture en mode remplacement ; le fichier `graphe_routier.gpkg` a été réparé sans relancer OSRM, en gardant la dernière copie de chaque ligne (vérifié : elle reproduit les valeurs de `arcs_enrichis`, aux arrondis près).
- `calcul_djma_methodes.py` : m3 prend la classe de chaque segment dans le RTSS, par son code de sous-route (7 436 segments sur 7 823 retrouvés) ; la complétion compare les tracés OSRM par la distance moyenne.
- Figures, `docs/methodes.md`, site du Volet 1 et cartes du Volet 2 régénérés.

**Chiffres (285 arcs mesurés)** : m1 et m4 inchangés ; médiane m2 14 216 → 14 318, m3 12 152 → 12 183 ; arcs à plus de 30 % d'écart entre méthodes 134 → 132. Sur les 19 arcs complétés, 14 changent de donneur (distance médiane 2,0 km au lieu de 0).

**Revue critique**
- Le nouveau critère de complétion est plus juste que l'ancien (qui prenait le premier voisin venu), mais il peut choisir une route parallèle : Stanstead–Vermont emprunte maintenant 0 camion, parce que le segment m4 de son donneur affiche 0 % de camions. C'est un artefact des données, pas du critère ; le rattachement exact par code RTSS (phase 1d) doit rendre ces emprunts presque inutiles.
- Le rapport intermédiaire MTMD (local, hors Git) cite encore les anciennes valeurs de m2 et m3 ; le portfolio CPCS, déjà remis, n'est pas modifié.
