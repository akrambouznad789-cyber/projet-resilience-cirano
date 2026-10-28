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

## Phase 1c · Routes alternatives par liaison (2026-10-28)

**Fait** — `reseau/alternatives.py` (méthode des pénalités), intégré à `jumeau reseau` ; couches `routes` (une ligne par route : km, minutes, km d'autoroute, km estimés, numéros de route) et `routes_liens` (incidence route → liens RTSS) ; 3 tests sur un réseau synthétique.

**Chiffres (tout le Québec, 29 s)** : 459 routes pour 307 liaisons. Sur les 252 liaisons routées sur le RTSS : 141 n'ont qu'une route, 70 en ont deux, 41 en ont trois ; une alternative prend en médiane 1,18 × le temps de la meilleure route. Exemples conformes au terrain : Trois-Rivières–Donnacona (A-40 ou R-138), Lavaltrie–Louiseville (R-138 ou A-40), Nicolet–Drummondville (R-255 ou A-55).

**Revue critique**
- Les règles (×1,3 au plus, 60 % de recouvrement au plus, pas à moins de 2 km d'une autre ville) sont des choix de modélisation, pas des faits ; elles sont dans `configs/reseau.yaml`.
- Le temps d'une route dépend des vitesses supposées par classe ; le modèle calé (phase 2) apprendra la préférence réelle entre autoroute et route nationale, ce qui corrige en partie ce biais.
- Les 55 liaisons sur OSRM n'ont qu'une route, avec un temps estimé à 70 km/h : le choix de route n'y est pas modélisé.
- `reseau_multi_routes.gpkg` n'est pas commité : il se régénère en 30 s par `jumeau reseau`.

## Phase 1d · Sections de comptage par route (2026-10-28)

**Fait** — `donnees/observations.py` (mesuré contre complété), `reseau/sections.py` (rattachement par numéro de route + recouvrement ≥ 50 % à 30 m ; DJMA m1 à m4 par route) ; couches `routes_sections` et `sections`.

**Chiffres (tout le Québec)** : 3 648 sections portées par au moins une route (Volet 1 : 2 648) ; **680 d'entre elles ont un DJMA et un % camions bruts de 2025** : ce sont les cibles de calibration (au Volet 1, seules 72 liaisons avaient une section m4 mesurée). 94 % des routes portent au moins une section ; 131 des 152 routes alternatives ont au moins une section mesurée, ce qui rend le choix de route apprenable. Camions m4 de la meilleure route contre le m4 du Volet 1 : rapport médian 1,03, corrélation 0,78 (en log), 75 liaisons sur 247 à plus de 50 % d'écart.

**Revue critique**
- Plan initial : jointure par chaînage RTSS exact. Fait autrement : numéro de route exact + recouvrement géométrique. Le chaînage se complique avec les codes de côté (G/D/C) et de sous-route ; le numéro de route suffit à empêcher qu'une autoroute « vole » les sections d'une route qu'elle croise, ce qui était le vrai risque.
- Les 75 grands écarts avec le Volet 1 ne sont pas tranchés : sections différentes (filtres du Volet 1 contre numéro de route ici) et parfois route différente. Le modèle calé, qui part des sections mesurées et non du m4 par liaison, n'en dépend pas.
- 387 sections n'ont pas de classe RTSS (code de sous-route introuvable) : poids 1 dans m3, comme au Volet 1.
- La phase 1e (montrer les routes sur la carte du Volet 1) est reportée à la phase 5, qui refait l'interface ; c'est plus économique que de modifier deux fois le gabarit.

## Phase 1f · Validation spatiale du RandomForest (2026-10-28)

**Fait** — `scripts/validation_spatiale_randomforest.py` (nouveau script ; les scripts existants ne bougent pas) : mêmes données, variables et modèle que la validation publiée, seul le découpage change (GroupKFold sur des carrés de 50 km, 149 blocs).

**Chiffres** : R² 0,306 (segments mélangés, publié, reproduit à l'identique) → **0,187** (blocs spatiaux). Autoroutes 0,38 → 0,07 ; nationales 0,44 → 0,33. `docs/completion.md` montre désormais les deux, avec l'explication.

**Revue critique** : la validation mélangée laissait fuir l'information des segments voisins (la position est une variable du modèle). C'est exactement le type d'erreur qu'un relecteur ML repère ; elle est maintenant écrite et chiffrée. Conséquence directe pour la suite : le modèle d'affectation ne se calera que sur les 680 sections mesurées, jamais sur des valeurs complétées.

## Phases 2-3 · Modèle d'affectation et calibration (2026-10-27 → 28)

**Fait** — `modele/` (logit à taille de chemin en PyTorch, tenseurs creux, artefact Parquet local), `calibration/` (déviance de Poisson sur les 680 sections mesurées + vraisemblance composite des postes franchis, Adam puis L-BFGS, plis spatiaux et par poste, bases B0 et B1, bootstrap, MLflow) ; `jumeau preparer` (2 min 30) et `jumeau calibrer` (23 min). Artefact : 20 484 paires de lieux, 257 952 itinéraires, 37 235 camions enquêtés, 49 postes localisés (couverture 69 %).

**Ce que le point de contrôle a révélé, dans l'ordre**
1. Vraisemblance de l'enquête : le crédit partiel de Jaccard biaisait μ de +4 % ; la correspondance exacte ne gardait que 34 % des camions. Retenu : une vraisemblance composite poste par poste, conditionnelle au poste d'interception (tests : μ et β retrouvés à 0,5 % près).
2. La calibration complète du 27 a planté à l'export JSON, après 20 min (clés en tuple) : corrigé, testé.
3. **Bootstrap faux.** Ses intervalles à 95 % excluaient l'estimation centrale (μ = 0,0041 hors de [0,0026 ; 0,0028]). Le point final avait pourtant convergé (gradients ~1e-7, 900 pas de plus sans effet). Cause : le départ à chaud ancrait le terme de rappel sur le point final, donc chaque tirage calait une autre perte. Corrigé (`entrainer(..., ancre=)`).
4. **L'enquête écrasait μ.** Calibrée seule, elle donnait μ = 0,0004/min (0,004 avec le rappel) : le temps n'expliquerait plus le choix de route. Les comptages seuls donnaient 0,060. Diagnostic : erreurs de classement. Un poste « franchi » qu'aucun chemin plausible ne traverse (poste mal localisé, déclaration erronée) coûte moins cher si l'on étale toutes les probabilités. Remède : un taux d'erreur ε, appris (P(noté franchi) = ε + (1 − 2ε)·q).

| Calage (complet, sans validation) | μ (1/min) | β hors autoroute | ε | R² log | NLL enquête |
|---|---|---|---|---|---|
| comptages seuls | 0,060 | +4,4 | – | 0,601 | – |
| enquête seule, sans ε | 0,0041 | −0,52 | – | 0,190 | 2,595 |
| enquête seule, sans ε ni rappel | 0,00035 | −5,5 | – | 0,174 | 2,493 |
| enquête seule, ε appris | 0,099 | −0,28 | 0,274 | 0,060 | 2,029 |
| **équilibre, ε appris (retenu)** | **0,078** | **−0,27** | **0,273** | **0,560** | **2,034** |

ε converge vers 0,274 depuis deux départs (0,06 et 0,31) : il est identifié. Les bases B0 et B1 reçoivent aussi leur propre ε, pour que les NLL restent comparables.

**Chiffres (objectif équilibre, IC bootstrap à 95 %, 20 tirages)** : μ = 0,078 [0,069 ; 0,085] ; β hors autoroute = −0,27 min/km [−0,28 ; −0,25] ; β estimé = 6,7 min/km [6,2 ; 7,0] ; g = 0,60 [0,50 ; 0,83] ; ε = 0,27 [0,26 ; 0,28] ; fond local 3 760 camions/j sur les autoroutes [3 131 ; 4 239].

Validation croisée (5 plis : blocs de 50 km × postes entiers, moyenne ± écart type) :

| | R² log | erreur médiane | part à ±30 % | NLL enquête | poste retrouvé |
|---|---|---|---|---|---|
| B0 : routage du Volet 2 | **0,552** ± 0,09 | **0,442** | 0,344 | 2,183 | 0,491 |
| B1 : plus rapide | 0,544 ± 0,09 | 0,450 | 0,353 | 2,215 | **0,539** |
| modèle calé | 0,531 ± 0,11 | **0,442** | **0,354** | **2,032** | 0,468 |

**Revue critique**
- **Le modèle calé ne bat pas le routage du Volet 2 sur les comptages** (R² log 0,53 contre 0,55, écart inférieur à un écart type) ; il gagne sur la vraisemblance de l'enquête (−0,15 nat par camion). Le plus rapide (B1) reste le meilleur pour retrouver le poste d'interception sur le chemin le plus probable. Son apport réel : une répartition calée entre itinéraires, qui permet de simuler un report de trafic. B0 et B1 ne savent pas le faire.
- ε = 0,27 est élevé : plus d'un quart des lignes (camion, poste) informatives seraient mal notées. C'est cohérent avec la couverture de 69 % et avec Lacolle (un seul nœud frontière par État). Mieux localiser les postes est le levier principal, avant tout raffinement du modèle.
- β hors autoroute < 0 alors que les comptages seuls disent +4,4 : l'enquête domine ce paramètre. Lecture probable : les vitesses supposées hors autoroute sont trop basses, et β corrige la vitesse plus qu'il ne mesure une préférence.
- g = 0,60 n'est pas une croissance du camionnage depuis 2006 : g et le fond local par classe sont confondus (le fond absorbe 3 760 camions/j sur autoroute). Ne pas l'interpréter seul.
- Non identifiés, tenus par le rappel : `fond_autre` (aucune section mesurée de cette classe) et la différence F2 − F3 (|β| < 0,003 min, sans effet).
- Les intervalles ne couvrent que l'incertitude d'échantillonnage, pas celle de la structure du modèle (ensembles de chemins, vitesses, localisation des postes).

## Phase 4 · Résilience (2026-10-28)

**Fait** — `scenarios/resilience.py` et `jumeau scenarios` (17 min), `configs/scenarios.yaml` ; 6 tests en formule fermée. Chaque liaison est fermée (toutes ses routes, 307 scénarios), puis chaque route d'une liaison qui en a plusieurs (263). Les camions se reportent selon le logit calé sur les itinéraires restants ; une paire privée de tous ses itinéraires est détournée par le plus court chemin du réseau dégradé (tout-ou-rien) ; sans chemin, ses trajets ne sont pas desservis. Sorties : `outputs/jumeau/criticite_équilibre.csv` (local) et `docs/jumeau/criticite_équilibre.csv` (public : résultats par liaison et par route, aucun flux entre lieux, comme au Volet 2).

**Ce que le point de contrôle a révélé**
1. Premier essai classé sur le Δ du temps moyen : 39 fermetures de liaison sur 307, et 157 fermetures de route sur 263, *réduisaient* les camions-heures. C'est un artefact du logit : une partie des camions prend un itinéraire plus lent, et le supprimer fait baisser la moyenne. Remplacé par le coût composite du logit (logsum, Williams 1977), qui ne peut que monter quand une option disparaît : 0 Δ négatif ensuite. Le temps moyen reste dans le tableau, à titre descriptif.
2. Les coupures (27 liaisons) étaient classées en bas de liste, car leurs trajets non desservis n'entrent dans aucun Δ d'heures. Elles sont maintenant signalées par la colonne `coupure`.

**Chiffres (objectif équilibre ; par an de fermeture, en 2025)**
- Liaisons les plus critiques : Gatineau–Ontario (2,05 M heures équivalentes), Saint-Zotique–Ontario par l'A-20 (2,03 M), Cabano–Nouveau-Brunswick (1,87 M), Rivière-du-Loup–Cabano (1,86 M, coupure partielle), puis l'A-20 au sud de Québec et entre Drummondville et Saint-Apollinaire, et l'A-20 et l'A-30 sur la Rive-Sud de Montréal.
- Par camion touché, en heures équivalentes : médiane 0,54 h ; 9e décile 2,0 h.
- Coût de perturbation provisoire (90 $/h) : 184 M$ par an de fermeture pour la première liaison, 1,4 G$ cumulés pour les dix premières, chacune fermée seule.
- Coupures : Lévis–Montmagny, Montmagny–La Pocatière, Saint-Pascal–La Pocatière et Saint-Pascal–Rivière-du-Loup laissent chacune 0,7 à 0,8 M de trajets non desservis par an.
- **Robustesse** (10 tirages du bootstrap rejoués sur les 307 liaisons) : τ de Kendall moyen 0,994 (minimum 0,980) ; recouvrement du top 20 : 99,5 %.

**Revue critique**
- La robustesse ne couvre que l'incertitude d'échantillonnage des paramètres, très étroite ici. Elle ne dit rien de la structure du modèle (ensembles de chemins, vitesses, demande de 2006-2007 mise à l'échelle par un g confondu avec le fond local). Un classement stable n'est pas un classement exact.
- **Les coupures sont surtout des artefacts du graphe à 307 liaisons.** Entre Lévis et Rivière-du-Loup, l'A-20 et la R-132 appartiennent aux mêmes liaisons, et aucune autre liaison ne relie le Bas-Saint-Laurent (pas de R-204, pas de traversiers) : la fermeture de toute la liaison isole la région. En réalité, un détour existe ; il est long, mais il existe.
- **Fermer une seule route :** le coût composite y vaut en médiane 11 fois le temps réellement perdu. Il compte la « valeur de la diversité » du logit (perdre une option sur deux à peu près équivalentes coûte ln 2/μ ≈ 9 min par camion, sans aucun détour) et les pénalités β (6,7 min par km estimé). Pour les fermetures de liaison du top 20, les deux mesures concordent (rapport de 0,89 à 1,19) : le classement principal n'en dépend pas, les chiffres par route oui.
- Les $ sont provisoires (90 $/h, 3 000 $/t), à remplacer par le guide avantages-coûts du MTQ et les $/t du Volet 2. Une fermeture réelle dure des jours, pas un an : il faut diviser en conséquence.

## Phase 4b · Meilleur itinéraire et report de trafic (2026-10-28)

**Fait**
- Troisième mesure dans chaque scénario : `delta_heures_meilleur`, le Δ du coût généralisé du meilleur itinéraire restant (pénalités β comprises, sans la valeur de la diversité du logit). Les paires détournées en tout-ou-rien gardent la même règle que pour le logsum.
- `fermer_avec_flux` : camions par jour sur chaque route après report, détours compris. `jumeau scenarios` écrit `reports_équilibre.csv` (Δ par route et par scénario, |Δ| ≥ 0,5 camion/j), publiable comme la criticité : un résultat par route, aucun flux entre lieux.
- 3 tests en formule fermée de plus (fermer l'option la moins bonne ne coûte que la diversité ; report intégral sur la route restante).

**Chiffres**
- Classement et robustesse inchangés : même top 15, τ de Kendall 0,994 (min. 0,980), top 20 retrouvé à 99,5 %.
- Fermetures de liaison : τ entre logsum et meilleur itinéraire = 0,925 ; rapport médian logsum / meilleur = 1,04 ; 7,5 % des fermetures n'allongent le meilleur itinéraire d'aucune paire.
- Fermetures d'une seule route : τ = 0,57 ; rapport médian 2,1 ; 47 % n'allongent le meilleur itinéraire d'aucune paire (la route fermée n'était le meilleur choix de personne).

**Revue critique**
- Pour les liaisons, les deux mesures concordent : le classement principal ne dépend pas de la valeur de la diversité.
- Pour une seule route, le logsum double la perte du meilleur itinéraire, et une fois sur deux il ne compte que de la valeur d'option. Les chiffres par route se lisent avec le meilleur itinéraire, le logsum reste l'ordre de classement.
- Le « 11 fois » du point de contrôle précédent comparait le logsum au temps de parcours seul. Contre le coût généralisé du meilleur itinéraire (pénalités β comprises), le rapport tombe à 2,1 : l'essentiel de l'écart venait des pénalités, pas de la diversité.
