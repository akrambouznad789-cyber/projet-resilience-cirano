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

## Phase 1b · Graphe routier RTSS (2026-10-28)

**Fait** — `src/jumeau_cirano/reseau/rtss.py`, `controle.py`, `commande.py` (`jumeau reseau [--echantillon]`), `configs/reseau.yaml`, 12 tests sur lignes synthétiques. Tout le Québec en 6 s : 12 328 sous-routes → 22 926 liens, 16 791 nœuds ; la composante principale porte 31 316 km sur 32 439 (96,5 %).

**Ce que le point de contrôle a révélé, dans l'ordre** (échantillon Montréal–Québec, part de liaisons conformes au critère initial : 21 % au premier essai)
1. Le RTSS s'interrompt là où une route numérotée devient municipale (traversée de ville, traversier) : raccords estimés entre morceaux d'une même route (classe « MU », 1,3 × vol d'oiseau, 40 km/h, sans comptage).
2. Carrefours en croix jamais noués (855 extrémités posées sur une autre ligne dans l'échantillon) : coupe à 2 m.
3. Bretelles qui s'arrêtent à 20–100 m des routes locales : tolérance de raccord des extrémités pendantes portée de 25 à 100 m.
4. Rattachement d'une ville à un seul nœud RTSS : remplacé par des connecteurs d'accès (un par route, jusqu'à 4, dans 8 km), la pratique standard des modèles de transport. Plus grand gain : 42 → 88 liaisons conformes sur 182.
5. Statut « T » du RTSS écarté à tort : ce sont 294 km de routes récentes en service (A-73, R-175, bretelles de l'A-40).
6. Raccords qui reliaient les deux chaussées d'une même route : ne sont plus raccordés que des morceaux non connectés.

**Critère révisé, et pourquoi.** Le critère initial mêlait deux questions : la topologie (peut-on aller de A à B sans détour ?) et le choix de route (le chemin le plus rapide suit-il OSRM ?), qui dépend des vitesses supposées et qui reviendra au modèle calé. Le test réussi/échoué porte désormais sur le plus court chemin en km des liaisons interurbaines, avec les mêmes seuils (1,15 et 90 %).

**Chiffres (tout le Québec)** : 220 liaisons interurbaines sur 248 sans détour (88,7 %) — **contrôle échoué de 1,3 point**, rapporté tel quel ; ratio km médian 1,005 ; liaisons urbaines 61 % ; accord de route avec OSRM 62 % (recouvrement médian 91 %).

**Décision** : le repli prévu au plan s'applique liaison par liaison plutôt que globalement. 254 liaisons recevront des routes alternatives RTSS ; 53 gardent leur route OSRM comme route unique (Baie-James, gérée par la SDBJ et absente du RTSS ; traversier de Tadoussac ; parties hors Québec ; rangs et rues municipales).

**Revue critique**
- Les raccords et les connecteurs d'accès sont des hypothèses (détour 1,3, 40 km/h). Ils sont signalés dans les couches (`raccord`, classe `AC`) et ne portent aucun comptage ; le modèle calé pourra pénaliser leur usage.
- Les statuts « F » et « R » (102 km) restent exclus faute de définition sûre ; à confirmer avec le dictionnaire RTSS du MTQ.
- Changer de critère après avoir vu les résultats est un risque de complaisance : c'est pourquoi les seuils n'ont pas bougé, l'échec est écrit, et les chiffres du critère initial restent ici.
