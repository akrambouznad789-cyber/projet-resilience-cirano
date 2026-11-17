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

## Phase 5 · Interface sombre et page du Volet 3 (2026-10-28)

**Fait**
- **Thème, source unique** : `scripts/gabarits/theme/jetons.json` (format DTCG, importable tel quel par Style Dictionary) → `scripts/theme_pages.py` → `theme.css` et `palette.js` pour l'application officielle, et injection dans chaque page. Sombre par défaut (inspiré de Dracula : fond #191a21, surfaces #21222c / #282a36, accent #bd93f9), clair conservé (bouton « Thème clair », ou `?theme=clair`).
- **Polices OFL auto-hébergées** dans `data/reference/fonts/`, intégrées en base64 (pages autonomes, même hors ligne) : Hammersmith One (titres, étapes, boutons), Atkinson Hyperlegible (texte et chiffres), Atkinson Hyperlegible Mono (tableaux, code).
- **Volets 1 et 2** : couleurs codées en dur → jetons, barre d'étapes à 3 volets, pages régénérées.
- **Page du Volet 3** : `scripts/visualisation_jumeau.py` + `scripts/gabarits/jumeau.html`, 5 étapes (plusieurs routes, modèle et réglages, calibration, fermer une liaison, liaisons critiques), onglet « Méthode » avec le code réellement exécuté.
  - Locale (`outputs/jumeau/index.html`, 11,3 Mo) : curseurs μ, β, g, fond local (mode expert), préréglages des trois objectifs, recalcul en direct, fermeture calculée dans la page, reclassement des 307 liaisons avec les réglages courants.
  - Publique (`docs/jumeau/index.html`, 2,2 Mo) : résultats précalculés seulement, aucun itinéraire tiré de l'ENR.
- **JavaScript portable**, sans DOM : `js/modele.js` (= `affectation.py`), `js/scenario.js` (= `resilience.py`), `js/moteur_canvas.js` (rendu sans WebGL). Les données du moteur viennent de `src/jumeau_cirano/interface/moteur.py` (45 000 séquences de routes uniques pour 258 000 itinéraires, tableaux en base64).
- Préréglages : calages rapides « comptages » (μ 0,060, β hors autoroute +4,4, β estimé −2,2) et « enquête » (μ 0,099, β −0,28, ε 0,27).

**Chiffres**
- Parité JS / Python (`tests/test_parite_js.py`, réseau synthétique avec accès, tailles de chemin et un détour tout-ou-rien) : écart < 1e-6 sur les probabilités, les flux, les sections, le logsum et les 9 impacts de 6 fermetures.
- Sur les données réelles, dans la page : R² log 0,560 (PyTorch : 0,560) ; fermeture de Gatineau–Ontario-Sud 2,05 M h équivalentes, 184 M$ (identique au calcul Python) ; affectation complète en 23 ms, une fermeture en 0,46 s.
- Couleurs validées avec le validateur du skill dataviz, contre la terre sombre #22232d : catégorielle à 3 teintes, toutes paires (ΔE daltonisme 9,4 ; vision normale 20,9 ; ≥ 3:1) ; rampes ordinales bleue et or (bas de gamme 2,35:1 et 2,5:1). Textes ≥ 4,5:1 (texte discret 5,4:1 sur les cartes), bordures des contrôles ≥ 3:1.

**Revue critique**
- Écart voulu au plan : les chiffres ne sont pas en Hammersmith One. Son « 1 » se confond avec un « I » (« 19 » se lisait « I9 ») ; ils passent en Atkinson Hyperlegible, conçue pour ça.
- Terre et eau sombres ne diffèrent que de 1,1:1 en luminance : la teinte (eau plus bleue) les sépare. C'est un fond, pas une donnée, mais les côtes restent discrètes.
- 7 routes courtes, faites de raccords estimés absents du RTSS, n'ont pas de tracé : elles sont dessinées en ligne droite et l'infobulle le dit.
- Le moteur Canvas existe en trois copies (les Volets 1 et 2 gardent la leur). À unifier sur `js/moteur_canvas.js` lors d'une prochaine refonte de ces pages.
- Vérifié en captures sans WebGL (Chrome headless, moteur Canvas forcé), en sombre et en clair, et sans débordement à la largeur minimale de Chrome (504 px). Pas vérifié : un vrai lecteur d'écran, un vrai téléphone.
- Le classement recalculé dans la page avec d'autres réglages ne refait pas la robustesse : il n'est qu'indicatif.

## Phase 6 · API, Docker, fiche du modèle (2026-10-28)

**Fait**
- `api/service.py` : le jumeau en service, indépendant de HTTP (l'application officielle peut l'importer). Il lit un **paquet** (artefact, calibration, liaisons, frontières, criticité) désigné par `JUMEAU_ARTEFACT`.
- `api/app.py` (FastAPI) : `GET /sante`, `GET /modele`, `POST /affectation`, `POST /scenario` (liaisons ou routes fermées, θ partiel facultatif), `GET /criticite`. Schémas pydantic stricts (`api/schemas.py`) ; contrat exporté dans `docs/api/openapi.json` (`jumeau openapi`), et la CI échoue s'il n'est plus à jour.
- `jumeau exporter` écrit le vrai paquet (`outputs/jumeau/service/`, 21 Mo, confidentiel). `api/synthetique.py` écrit un paquet de démonstration sur le réseau jouet, sans aucune donnée de l'ENR : il sert aux tests, à la CI et à l'équipe du jumeau officiel.
- `Dockerfile` en deux étapes (python:3.12-slim, environnement verrouillé, extra `api` sans MLflow), utilisateur sans privilèges, `HEALTHCHECK`, paquet monté en lecture seule ; `.dockerignore` en liste blanche (seul le code entre dans l'image). Nouveau job CI : construire l'image, générer le paquet synthétique dans le conteneur, lancer l'API, interroger `/sante`, `/scenario` et `/criticite`.
- Fiche du modèle `docs/jumeau.md`, section Volet 3 et résumé anglais du README, section Pipeline du `CLAUDE.md` réalignée sur les vrais scripts.

**Deux défauts trouvés en chemin**
- `pyarrow` n'arrivait que par MLflow (extra `suivi`) : une image sans MLflow n'aurait pas pu lire l'artefact Parquet. Ajouté aux dépendances principales.
- `tenseurs.py` construisait une matrice des passages 0 × 0 (au lieu de P × 0) pour un réseau sans point frontière : n'importe quel autre réseau que le Québec aurait planté. Révélé par le paquet synthétique, corrigé.

**Chiffres**
- 37 tests, dont 7 sur l'API (paquet synthétique, `TestClient`).
- Sur le vrai paquet, en local : `/scenario` répond en 0,29 s, avec les chiffres exacts du classement précalculé (liaison 1 : 25 592 heures équivalentes par an, 55 routes touchées).

**Revue critique**
- **Docker n'a pas été lancé localement** (absent de WSL) : la première vraie construction de l'image est celle de la CI. À vérifier dans l'onglet Actions après le push.
- L'image embarque GeoPandas et Shapely, dont l'API n'a pas besoin (dépendances principales du paquet). Plus lourde que nécessaire ; à séparer en extras si sa taille gêne.
- Aucune authentification ni limite de débit : c'est à la passerelle de l'application officielle de les fournir. Des requêtes différentielles répétées (θ, fermetures) pourraient en principe renseigner sur la demande : l'API ne doit tourner qu'à l'intérieur de l'infrastructure du CIRANO.
- Le premier appel avec un nouveau θ reconstruit un contexte (environ 1 s) ; les 8 derniers sont gardés en cache, sans limite de mémoire plus fine.

## Version de présentation et normalisation des noms (2026-10-30)

Branche `feature/demo-academique`. Demande : une version simple pour des universitaires non spécialisés, à côté de la version technique, et des titres plus rigoureux.

**Fait**
- `scripts/visualisation_demo.py` + `scripts/gabarits/demo.html` → `docs/demo/index.html` (1,6 Mo) : 4 étapes, une seule commande (choisir une liaison), résultats par jour de fermeture. Seule entrée : le JSON de `docs/jumeau/index.html`, filtré par liste blanche ; rien de nouveau n'est publié et le script ne lit ni l'artefact ni l'ENR.
- Mesure de la version de présentation : le temps sur le meilleur itinéraire restant (`delta_heures_meilleur`). Dollars retirés (hypothèses provisoires).
- Noms publics : Module 1 (Estimation du trafic par liaison), Module 2 (Valorisation économique du fret), Module 3 (Jumeau numérique et analyse de résilience). « Module » plutôt que « Axe », qui se confondrait avec « axe routier ». Chemins, URL, fichiers et variables gardent `volet`.
- Tutoiement retiré des trois pages techniques. Trois captures du README refaites.

**Chiffres**
- 46 tests (9 nouveaux, sur données synthétiques).
- Texte visible, listes déroulantes exclues : 423 mots contre 1 655 pour la page technique du jumeau, soit 26 %.
- 307 liaisons passées une à une dans la page : 614 chiffres identiques à `criticite_équilibre.csv`, 27 messages de prudence, aucune erreur en console (MapLibre), aucun débordement à 390 px.
- Pages techniques régénérées : données identiques à `main`, hormis les dates de génération.

**Revue critique**
- **Liaisons sans itinéraire** : 27 liaisons non classées, car leurs heures n'incluent pas les camions sans chemin. Il ne faut pas les lire comme peu critiques. Certaines sont une vraie dépendance (138 sur la Côte-Nord), d'autres un effet du graphe (Lévis–Rivière-du-Loup).
- **Reports lointains** : 20 fermetures sur 307 ajoutent au moins 100 camions/j à la frontière Rouyn–Ontario, surtout autour de Valleyfield et Châteauguay ; 305 à 330 camions/j sur la 117 pour Saint-Hyacinthe–Drummondville. Les hors-Québec choisissent leur frontière avec un accès estimé à 1,2 × le vol d'oiseau, et le réseau ontarien n'est pas modélisé. Mention ajoutée aux limites ; le mécanisme exact n'a pas été retracé paire par paire.
- **Liaisons frontalières en tête du classement** : 1 à 4 h de détour par camion, peut-être surestimées (peu de points frontières dans le graphe).
- La mesure du temps perdu reste un coût composite (pénalités hors autoroute comprises) : un ordre de grandeur, pas des heures chronométrées.

## Terrain ML · phase 0 · Clôture de la version de présentation (2026-11-16)

Première étape d'une course en cinq phases qui prépare le jumeau à l'apprentissage automatique (atlas des données, tables d'apprentissage, calage plus rapide, carte des pistes). Un labo d'apprentissage automatique travaille en parallèle sur ce dépôt ; ses questions lui sont réservées et ses fichiers ne sont pas touchés d'ici.

**Fait**
- `CLAUDE.md` : section « Labo ML (session parallèle) », en commit séparé (`28c16bd`).
- `tests/test_demo.py` : 4 lignes de plus de 120 caractères repliées. `ruff check` échouait : l'étape « Style » de la CI aurait échoué au premier push de la branche.
- Les quatre pages régénérées par leurs scripts : Module 1, Module 2 (publique), Module 3 (publique et locale), version de présentation.

**Chiffres**
- 46 tests réussis ; `ruff check src tests` : 4 erreurs → 0 ; contrat `docs/api/openapi.json` inchangé après `jumeau openapi`.
- Pages : dans les quatre fichiers, seule la date de génération change (2026-10-30 → 2026-11-16). Données, textes et scripts intégrés sont identiques à l'octet près.

**Revue critique**
- Les tests passaient, mais pas le style : la CI ne tourne qu'au push et cette branche n'a jamais été poussée. Avant chaque commit : `uv run ruff check src tests && uv run pytest`.
- Une régénération identique prouve que les pages se reproduisent sur cette machine (mêmes artefacts locaux, mêmes versions de bibliothèques). Elle ne revalide pas les chiffres qu'elles affichent.

## Terrain ML · phase 1 · Atlas des données pour l'apprentissage automatique (2026-11-16)

Deuxième étape d'une course en cinq phases qui prépare le jumeau à l'apprentissage automatique : clôture de la version de présentation, atlas des données, tables d'apprentissage, calage plus rapide, carte des pistes. Un labo d'apprentissage automatique travaille en parallèle sur ce dépôt. Ses questions lui sont réservées : aucun modèle n'est entraîné ici, et l'atlas ne mesure ni la dynamique des séries, ni le lien entre une cible et ses variables, ni la ressemblance entre sections voisines.

**Plan en pseudo-code (écrit avant tout script)**

```
1. inventaire()        chaque fichier de data/ et chaque sortie publique : couches, lignes, champs, CRS, taille,
                       empreinte SHA-256, suivi par git ou non, commande de régénération
2. sous-agents         un par famille de données, en parallèle, en lecture seule, scripts jetables hors dépôt :
                       A comptages bruts · B complétion (mesuré / complété, valeur par valeur) · C RTSS et graphe
                       D liaisons, sorties du Module 1, réseau à plusieurs routes · E ENR (par le code seulement)
                       F provenance, licences, git
3. scripts/atlas_donnees_ml.py    recalcule de façon indépendante chaque agrégat cité dans l'atlas
     series()          fichier brut → format long (section × année × mesure) ; mesuré = valeur présente
                       dans le fichier du MTQ
     methode()         valeur absente du brut → interpolation (entre deux mesures), extrapolation (au-delà),
                       forêt aléatoire (aucun % de camions mesuré), KNN + IDW (aucun DJMA mesuré)
     blocs()           bloc de 50 km = (⌊x / 50 km⌋, ⌊y / 50 km⌋) du centroïde en EPSG:32198, exactement
                       comme scripts/validation_spatiale_randomforest.py (contrôle : 149 blocs)
     taille_effective()  par cible : valeurs, sections, blocs occupés
     figures()         couverture temporelle (mesuré / complété, par année) et spatiale (par bloc)
     → docs/donnees_ml/agregats.json et figures/ml_*.png : agrégats seulement
4. docs/donnees_ml.md  rédigé à partir de agregats.json ; les chiffres des sous-agents sont confrontés au script
5. contrôles           uv run pytest, ruff, chaque chiffre relu contre sa source, aucun fichier de l'ENR ouvert
```

**Fait**
- Six audits en lecture seule, un par famille de données, menés en parallèle par des sous-agents : comptages publiés, complétion, RTSS et graphe, liaisons et sorties du Module 1, ENR (par la lecture du code seulement), provenance et licences.
- `scripts/atlas_donnees_ml.py` recalcule chaque agrégat de façon indépendante, à partir des fichiers publics : `docs/donnees_ml/agregats.json` et deux figures de couverture (`figures/ml_couverture_temporelle.png`, `figures/ml_couverture_spatiale.png`). Il échoue si la définition des blocs ne redonne plus les 149 blocs de la validation spatiale.
- `docs/donnees_ml.md` : trois règles de lecture, le statut de chaque valeur (publiée ou complétée, et par quelle méthode), une fiche par jeu de données, les cibles possibles avec leur taille effective et leur piège de fuite, les écarts relevés avec la documentation existante.

**Chiffres**
- Valeurs publiées par le MTQ : 37 344 DJMA sur 78 230 (47,7 %) et 12 498 % de camions (16,0 %). Le reste est complété : 41,5 % des DJMA par KNN + IDW, dans 3 247 sections sans aucun DJMA.
- 788 % de camions publiés ont été remplacés par la complétion, dans 455 sections sans DJMA.
- La forêt aléatoire du Module 1 est entraînée et validée sur 36 660 cibles, dont 68,1 % sont elles-mêmes complétées.
- Taille effective : 182 blocs de 50 km pour toutes les sections, 159 pour le DJMA publié, 149 pour le % de camions publié, 88 pour les 680 sections de calage du Module 3.
- Graphe RTSS : 7 326 sections sur 7 823 se rattachent à un lien ; les 1 244 sections étiquetées en 2025 longent 8 391 liens, dans 113 blocs.
- Relecture : 349 nombres dans la page. 310 se retrouvent tels quels dans `agregats.json` ; les 39 autres (pourcentages dérivés, volumes de l'ENR déjà publiés, chiffres du journal) ont été vérifiés un par un. 37 tests réussis, `ruff` propre.

**Revue critique**
- « Mesuré » est un abus de langage, hérité des phases précédentes. La fiche du jeu sur Données Québec dit que ces débits viennent d'une méthode statistique d'estimation. Un modèle évalué contre eux mesure son accord avec l'estimation du ministère, pas avec un comptage.
- Deux constats touchent le Module 1 déjà publié : des valeurs publiées écrasées par le KNN, et une forêt aléatoire validée contre des cibles aux deux tiers complétées. Ni le script de complétion ni ses pages ne sont modifiés ici : l'atlas consigne les écarts, la correction revient au labo qui reprend cette complétion.
- Le recoupement a servi. Un rapport d'audit donnait 26 blocs pour les sections de calage ; le script et deux autres rapports en donnent 88. Un autre annonçait 21 fichiers non suivis dans `data/processed/` ; il y en a 18. Aucun chiffre d'un rapport n'est entré dans la page sans être recalculé.
- La copie des données du MTQ n'a pas de date de téléchargement, alors que la source est mise à jour chaque jour. Les empreintes SHA-256 de `agregats.json` figent au moins ce qui a été audité.
- Le script n'a pas de test automatique et la CI ne le lance pas. Il dépend de `reseau_multi_routes.gpkg`, qui n'est pas dans git.
- La page n'est liée depuis aucune autre : le README n'est pas modifié sur cette branche, pour ne pas entrer en conflit avec la branche de la version de présentation. Lien à ajouter après la fusion.

## Terrain ML · phase 2 · Tables d'apprentissage reproductibles (2026-11-16 → 17)

Branche `feature/ml-tables`, posée sur `feature/ml-atlas` : elle complète `docs/donnees_ml.md`, créé par la phase 1.

**Plan en pseudo-code (écrit avant tout script)**

```
uv run jumeau tables [--echantillon]            → outputs/ml/ (hors git), en Parquet

donnees/blocs.py      bloc_50km()               ⌊x / 50 km⌋_⌊y / 50 km⌋ du centroïde en EPSG:32198 : source unique,
                                                 la même que scripts/validation_spatiale_randomforest.py
donnees/tables.py
  series_longues()    brut + complété → une ligne par section et par année, pour le DJMA et le % de camions :
                        *_cible     valeur publiée par le MTQ, lue dans le fichier BRUT ; vide sinon
                        *_complete  valeur du fichier complété (jamais vide)
                        *_mesure    drapeau ; *_methode : mesure, interpolation, extrapolation, recopie,
                                    foret_aleatoire, knn_idw ; *_ecrase : valeur publiée que la complétion a remplacée
  attributs_sections() type de route (code RTSS de la section, sinon ligne RTSS la plus proche), position (x, y),
                        longueur, bloc de 50 km
  verifier_cibles()   refuse une table où une colonne *_cible porte une valeur non publiée
  exemples()          lignes dont la cible est publiée, sans les colonnes qui la répètent : le seul accès conseillé
  table_noeuds(), table_aretes()                graphe RTSS : position, bloc, classe, tronçon estimé ou non
  table_aretes_sections()                       incidence lien × section (même numéro de route, à 30 m ou moins)
  etiquettes_aretes() lien × année : moyenne des sections qui longent le lien, pondérée par la longueur commune ;
                        accepte la liste des sections d'entraînement, pour ne jamais étiqueter un lien avec une
                        section mise de côté
donnees/commande.py   lit configs/tables.yaml, construit, vérifie (dont les 149 blocs), écrit les tables et meta.json
tests/test_tables.py  données synthétiques : statut de chaque valeur, cible jamais complétée, valeur écrasée,
                        blocs, rattachement des liens, sections mises de côté
ordre : échantillon (couloir Montréal–Québec), puis tout le Québec ; fiches dans docs/donnees_ml.md
```

**Fait**
- `uv run jumeau tables [--echantillon]` : `donnees/blocs.py` (le bloc de 50 km, source unique), `donnees/tables.py`, `donnees/commande.py`, `configs/tables.yaml`. Cinq tables Parquet dans `outputs/ml/` (hors git), et un `meta.json` : colonnes, version du code, empreintes des quatre fichiers lus, contrôles. Pour tout le Québec, `meta.json` est copié dans `docs/donnees_ml/tables.json`, suivi par git. 6 s pour tout le Québec, 4 s pour l'échantillon.
- Section « Tables d'apprentissage » dans `docs/donnees_ml.md` : schéma, colonnes d'une mesure, garde-fous contre la fuite, type de route et jonctions, contrôles. Ses 62 nombres se retrouvent dans `tables.json` ou `agregats.json`, sauf la durée, les nombres de colonnes et des constantes (SHA-256, Module 3).
- 11 tests sur données synthétiques (48 en tout) : statut de chaque valeur, cible jamais complétée (sentinelles), valeur publiée écrasée, fichiers incohérents refusés, type de route par le numéro, écart de jonction, étiquettes des liens et sections mises de côté, commande de bout en bout et copie publique.

**Ce que le point de contrôle a révélé, dans l'ordre**
1. **Le repli prévu pour le type de route était faux.** 387 sections ont un code RTSS introuvable. Pour elles, la « ligne RTSS la plus proche » du plan ne portait le même numéro de route que 13 % du temps : c'était le plus souvent une route qui croise la section, et 107 sections sont à plus de 100 m de toute ligne. Ces sections sont surtout sur des routes absentes du RTSS : 258 numéros inconnus du fichier, dont 36 codes « Segment… » qui ne sont pas des codes RTSS. Remplacé par la règle de rattachement du Module 3 : lignes de même numéro à 30 m ou moins (75 sections), sinon `Inconnu` (312, dont 69 avec un DJMA publié). Le pseudo-code ci-dessus garde la règle initiale.
2. **DJME et DJMH restaient dans les exemples du DJMA.** L'atlas les désigne comme des cibles sœurs, issues de la même estimation du ministère ; les deux sont publiés sur 33 105 des 37 344 exemples. `exemples()` les retire pour le DJMA et les camions, avec le nombre d'années publiées de la cible : toujours d'au moins 1 dans les exemples, il est nul là où l'on voudrait prédire, et sa distribution change donc entre l'apprentissage et l'usage.
3. **Le drapeau de nœud repris du graphe couvrait 66 % des nœuds.** Appelé `jonction_reparee`, il réunissait les carrefours en T (10 291 extrémités posées à 2 m ou moins sur une autre ligne, que le RTSS ne coupe pas) et les extrémités pendantes raccordées jusqu'à 100 m (1 780 au-delà de 2 m, médiane 16 m). Renommé `jonction_nouee`, et complété par `ecart_jonction_m`, mesuré sur la géométrie des liens sans modifier `jumeau reseau` : 1 222 nœuds dépassent 2 m ; aux extrémités communes, l'écart ne dépasse jamais 0,59 m.
4. **Deux incohérences de fichier passaient sans erreur.** Une section sans % de camions publié, ni « forêt aléatoire » ni KNN, aurait été étiquetée « extrapolation » ; les créneaux de DJME et de DJMH n'étaient pas comparés à ceux du DJMA. Les deux cas sont maintenant refusés ; aucun ne se présente dans les fichiers actuels.

**Chiffres (tout le Québec ; échantillon Montréal–Québec entre parenthèses)**
- Sections : 7 823 dans 182 blocs (4 720 dans 23 blocs). Cibles : 37 344 DJMA (18 438) et 12 498 % de camions (6 248), autant que de valeurs publiées ; 788 % de camions écrasés par la complétion (668). Méthodes identiques à l'atlas, recopie comprise (3 078 DJMA, 6 453 % de camions).
- Exemples : DJMA 37 344 lignes dans 159 blocs ; % de camions 12 498 dans 149 ; camions 11 693, sur 3 666 sections et 149 blocs, comme l'atlas.
- Graphe : 7 326 sections rattachées (4 539), 21 870 liens longés par une section (13 363), 8 391 liens étiquetés en camions en 2025 (5 004) ; 16 460 nœuds (9 822), dont 1 222 jonctions estimées (978). 1 732 des 46 583 paires lien–section relient deux blocs différents.
- Vérifications indépendantes, dans des scripts jetables hors du dépôt : les cibles, DJME et DJMH (78 230 cases chacune) sont identiques au fichier brut relu à la main ; aucune étiquette de lien n'est hors des bornes de ses sections (181 009 cases de DJMA, 77 049 de camions) ; les empreintes des quatre fichiers lus sont celles de `agregats.json`.

**Revue critique**
- Les tables ne corrigent pas les défauts du Module 1 relevés par l'atlas : elles les rendent visibles (`_ecrase`, `_methode`) et empêchent d'en faire des cibles. Les variables `_complete` restent disponibles ; qui les emploie en accepte la méthode.
- Le contrôle de fuite des exemples (égalité et association entre chaque colonne restante et la cible) a été fait hors du dépôt. Ses mesures d'association ne sont pas publiées, pour la raison qui arrête l'atlas à la structure : ces questions reviennent au labo.
- Les 312 sections de type `Inconnu` portent une valeur manquante explicite, pas une imputation ; un modèle devra la traiter comme telle.
- `ecart_jonction_m` se déduit de la géométrie, car `jumeau reseau` n'écrit pas la distance de chaque raccord. Un nœud qui réunit plusieurs raccords peut dépasser la tolérance de 100 m (159,3 m au plus).
- Le graphe reste non orienté, sans les connecteurs d'accès des villes : les tables décrivent le réseau routier, pas les itinéraires du Module 3.
- `tables.json` cite le commit du code ; des tables produites depuis un arbre modifié portent le suffixe `-dirty`.
- Comme le script de l'atlas, la commande complète ne tourne pas en CI : elle demande `reseau_multi_routes.gpkg`, hors git. Ses fonctions y tournent, sur données synthétiques et sur les fichiers publics suivis.

## Terrain ML · phase 3 · Calage du Module 3 plus rapide (2026-11-17)

Branche `feature/calage-rapide`, partie de `main`. But : rendre faisables les validations croisées et les ablations du Module 3, sans changer ni le modèle ni la perte. Référence : l'essai MLflow `e98fc7bb` du 2026-10-28 (calage complet « équilibre », 1 382 s). L'essai `bfff3e2d`, resté « RUNNING », appartient au labo : il n'est pas touché.

**Tolérance acceptée (écrite avant d'optimiser)**
- Évaluation, à θ égal : perte, gradient, probabilités des itinéraires, flux par route et camions par section à un écart relatif ≤ 1e-10. Tout est en float64 ; seul l'ordre des sommes peut changer.
- Calage complet, même graine et mêmes itérations : chaque paramètre final à |Δ| ≤ 1e-6·|θ| + 1e-8 ; métriques de la validation croisée et finales à |Δ| ≤ 1e-6 ; bornes des intervalles du bootstrap à |Δ| ≤ 1e-6·|b| + 1e-8.
- Une accélération qui dépasse la tolérance est retirée, même si elle fait gagner du temps.

**Plan en pseudo-code (écrit avant tout script)**

```
1. profil               script jetable : chargement de l'artefact, une évaluation (avant + arrière),
                        torch.profiler sur 20 évaluations ; chaque étape de `jumeau calibrer` chronométrée
2. échantillon          modele/echantillon.py : sous-artefact du couloir Montréal–Québec de configs/reseau.yaml
                          routes entièrement dans l'emprise ; paires dont TOUS les itinéraires n'empruntent
                          que ces routes ; leurs itinéraires, groupes de camions et postes ; sections de ces routes
                        → outputs/jumeau/artefact_echantillon/ (local : dérivé de l'ENR)
                        `jumeau calibrer --echantillon` : résultats dans outputs/jumeau/echantillon/, jamais publiés
3. référence            le code de `main`, exporté tel quel (git archive) hors du dépôt, lancé sur un dossier
                        d'essai (outputs/jumeau/calage_rapide/<cas>/, artefact par lien symbolique) :
                        les sorties de production et docs/jumeau/ ne bougent pas ; échantillon, puis Québec
4. accélérer            l'implémentation seulement, d'après le profil (produits creux, structures constantes,
                        chargement) ; même optimiseur, mêmes itérations, même graine
5. comparer             même dossier d'essai, même base MLflow : essai accéléré contre essai de référence,
                        échantillon puis Québec ; écarts contre la tolérance ; temps avant / après
6. tests                parité de l'ancienne et de la nouvelle évaluation (perte, gradient) sur réseau
                        synthétique ; sous-artefact cohérent ; uv run ruff check src tests && uv run pytest
```

**Fait**
- Profil (Québec) : une évaluation de la perte, avant et arrière, coûtait 313 ms, dont 76 % dans `addmm`, c'est-à-dire les produits creux en COO. La moitié de ce temps partait dans leur rétropropagation, qui retransposait la matrice à chaque appel. Le chargement de l'artefact ne pèse que 3 s.
- `modele/affectation.py` : `OperateurCreux`, le produit x ↦ A·x stocké en CSR, avec Aᵀ convertie une seule fois et une fonction autograd qui fait passer le gradient par elle ; les zéros explicites sont retirés. Il sert les trois produits chauds : flux par route, camions par section, probabilités des postes. Les matrices COO restent disponibles pour les scénarios, l'API et la page. Les groupes informatifs de l'enquête, constants, sont calculés une fois.
- `modele/echantillon.py` et `jumeau calibrer --echantillon` : sous-artefact du couloir Montréal–Québec (258 routes, 2 271 paires, 13 712 itinéraires, 2 693 groupes de camions, 298 sections mesurées). Les résultats vont dans `outputs/jumeau/echantillon/` et ne sont jamais publiés. Une clé `publier` (vraie par défaut) dans la configuration du modèle sert aux essais hors production.
- Référence : le code de `main`, exporté hors du dépôt (`git archive`), a tourné sur des dossiers d'essai (`outputs/jumeau/calage_rapide/`), chacun avec sa base MLflow. Les sorties de production et `docs/jumeau/` n'ont pas bougé.
- 5 tests : opérateur creux, perte et gradient contre la formulation COO, flux et camions par section, sous-artefact. 42 tests sur la branche.

**Chiffres**

| | Référence (code de `main`) | Accéléré | Rapport |
|---|---|---|---|
| Une évaluation de la perte, Québec | 313 ms | 29 ms | 10,8 |
| Une évaluation, base B0 | 238 ms | 13 ms | 18 |
| Calage complet, échantillon (durée MLflow) | 56 s | 15 s | 3,6 |
| Validation croisée, Québec | 885 s | 75 s | 11,8 |
| Calage complet, Québec (durée MLflow) | 1 345 s | 127 s | 10,6 |
| Calage complet, Québec (commande, à l'horloge) | 22 min 34 s | 2 min 15 s | 10,0 |

- La référence est déterministe : l'essai d'aujourd'hui redonne à l'identique (écart nul) les 37 grandeurs de l'essai `e98fc7bb` du 2026-10-28.
- **Critère 2, le calage de bout en bout : tenu.** Échantillon : 50 grandeurs comparées, le plus grand écart vaut 2,5e-5 fois sa tolérance. Québec : 71 grandeurs, le plus grand écart vaut 1,4e-2 fois sa tolérance (une borne du bootstrap de β pour la frontière F2, un paramètre non identifié qui vaut environ 2e-8).
- **Critère 1, l'évaluation à θ égal : tenu, sauf pour le gradient au point calé.** Perte ≤ 2,6e-16, probabilités identiques, flux et camions par section ≤ 2e-14, tous les gradients à θ₀ ≤ 2,4e-12. À θ̂, le gradient s'écarte de 4,6e-9 en relatif au Québec et de 5e-10 sur l'échantillon, au-delà du seuil de 1e-10.

**Ce que le point de contrôle a révélé**
1. **Le critère 1 était mal posé pour le gradient au point calé.** À θ̂, le gradient vaut presque zéro (de 7e-9 à 3e-6 selon le paramètre) : c'est une somme de termes qui se compensent, et le moindre changement dans l'ordre des sommes y déplace l'arrondi relatif bien au-delà de 1e-10. Le calcul de référence ne tient pas ce critère contre lui-même : entre 1 et 4 threads, son gradient à θ̂ change de 1,4e-8 en relatif, plus que l'écart du nouveau calcul (4,6e-9). En absolu, tous ces écarts restent de l'ordre de 1e-16. Le critère n'a pas été réécrit après coup ; la décision de garder l'accélération repose sur le critère 2, mesuré de bout en bout et tenu avec au moins deux ordres de grandeur de marge.
2. Sur l'échantillon, ε atteint sa borne de 0,5 : l'enquête n'y apporte plus d'information, car celle-ci tient surtout aux longs trajets qui franchissent plusieurs postes. L'échantillon sert à comparer deux calculs, pas à estimer le modèle.

**Revue critique**
- La règle écrite avant d'optimiser voulait qu'une accélération hors tolérance soit retirée. Le critère 1 est dépassé pour le gradient à θ̂, et l'accélération est gardée : c'est un écart à la règle. Il se justifie parce que le code de référence la dépasse lui-même, mais il reste un écart, soumis à validation. Un critère mieux posé aurait borné l'écart absolu du gradient, rapporté à la somme des valeurs absolues de ses termes.
- Tout le gain vient d'un seul changement, les produits creux. Les autres opérations (copies, indexations : environ les trois quarts des 29 ms qui restent) n'ont pas été touchées. Avec une validation croisée en 75 s, le besoin de la phase est couvert.
- `--echantillon` reprend l'emprise de `configs/reseau.yaml` ; un autre couloir demanderait une autre emprise. Les comptages du sous-artefact ne voient que la demande des paires gardées.
- Les scénarios, l'API et la page passent aussi par le nouvel opérateur (leurs tests sont verts), mais la durée de `jumeau scenarios` (17 min) n'a pas été remesurée : elle sort du périmètre de la phase.
- La CSR de PyTorch est encore en « bêta » (PyTorch l'annonce par un avertissement, rendu muet ici). Une version future pourrait en changer le comportement ; les tests de parité le détecteraient.

## Terrain ML · phase 4 · Carte des pistes de recherche (2026-11-17)

Branche `feature/ml-pistes`, posée sur `feature/ml-tables` et non sur `main` : la carte part de l'atlas, qu'elle cite et vers lequel elle renvoie. Ses coûts de calcul viennent des temps mesurés en phase 3 (branche `feature/calage-rapide`). La phase ne comporte aucun script : la règle du plan préalable ne s'applique pas, la méthode est décrite ici.

**Méthode** — partir des cibles de l'atlas et des limites écrites dans ce journal ; écarter toute question des labos 1 à 4, même voisine ; pour chaque piste, sept rubriques en dix lignes au plus (question, données, cible, validation sans fuite, base de comparaison, coût sur ce portable, risque) ; classer selon quatre critères annoncés ; ne rien calculer.

**Fait**
- `docs/pistes_ml.md` : dix pistes classées, trois recommandées (ablations du Module 3, localisation des postes de l'ENR, demande régionale), cinq idées notées en chemin et laissées aux labos ou aux pistes, la frontière avec les labos 1 à 4.
- L'atlas renvoie à la carte (en-tête et section des cibles).

**Chiffres**
- 124 nombres dans la page. Tous se retrouvent dans l'atlas, ses agrégats, les tables ou ce journal, sauf quatre : la mémoire du portable et le seuil de 30 min (cahier des charges de la course), le rayon de 15 km (`modele/preparation.py`), et 46 971 paires de liaisons (307 × 306 / 2).
- Les coûts sont des estimations tirées de durées mesurées : 29 ms par évaluation, 75 s par validation croisée, 2 min 15 s par calage complet (phase 3) ; 0,29 s par fermeture (API, phase 6) ; 2 min 30 pour `jumeau preparer` et 17 min pour `jumeau scenarios` (phases 2 à 4).

**Revue critique**
- Le classement est un jugement, pas une mesure : ses critères sont écrits, leur pondération ne l'est pas.
- Deux pistes frôlent des questions réservées. La piste 7 (anomalies) n'utilise qu'une année à la fois, pour ne pas devenir une prévision des séries (labo 3) ; la piste 8 (complétion du DJMA le long du réseau) exclut les réseaux de neurones sur graphe (labo 4). Le chercheur peut juger la frontière trop mince et les écarter.
- La piste 10 dépend de données que le ministère n'a pas encore fournies ; toute nouvelle donnée passe d'abord par l'accord du Pr Trépanier.
- La durée de `jumeau scenarios` date d'avant l'accélération : les coûts des pistes 6 et 9 sont probablement surestimés.
- Aucune piste n'a été essayée, même sommairement. Leurs chances de succès ne sont pas évaluées ici.

## Terrain ML · clôture · Intégration des cinq phases (2026-11-17)

Branche `integration/terrain-ml`, partie de `main` : elle réunit, par fusions explicites, `feature/demo-academique` (phase 0), `feature/ml-pistes` (phases 1, 2 et 4, empilées) et `feature/calage-rapide` (phase 3). Rien n'est poussé ; `main` n'a pas bougé.

**Fait**
- Conflits résolus à la main : ce journal (entrées remises dans l'ordre des phases) et la section Pipeline du `CLAUDE.md` (lignes de la version de présentation gardées, ligne `--echantillon` ajoutée). Le reste a fusionné sans conflit.
- README : une puce et une section « Terrain pour l'apprentissage automatique », l'organisation du dépôt, les commandes, le résumé anglais. La ligne du README qui compte les tests n'est pas modifiée : elle est réservée au labo.
- `CLAUDE.md` : la carte des pistes dans l'architecture.

**Chiffres**
- 62 tests réussis sur la branche intégrée : 37 de `main`, 9 de la version de présentation, 11 des tables, 5 du calage. `ruff check src tests` propre ; contrat `docs/api/openapi.json` inchangé après `jumeau openapi` ; `uv sync --locked` sans changement : aucune dépendance ajoutée.

**Revue critique**
- La CI n'a pas tourné : rien n'a été poussé. La construction de l'image Docker n'est vérifiée que par elle (Docker est absent de WSL).
- Le README annonce toujours 37 tests alors qu'il y en a 62 : correction laissée au labo, à qui cette ligne est réservée.
- Les pages publiques n'ont pas été régénérées : aucune donnée qu'elles affichent n'a changé, et l'onglet « Méthode » du jumeau ne montre que des extraits que la phase 3 n'a pas touchés.

**Décisions d'Akram (2026-11-17)**
- Publication : fusion de `integration/terrain-ml` dans `main`, puis envoi de `main` et des cinq branches.
- Calage rapide : l'accélération est gardée malgré le critère 1 manqué au point calé (voir la phase 3) ; la décision repose sur le calage de bout en bout.

## Retours du directeur de recherche sur le Module 1 (2026-11-17)

Branche `feature/retours-prof-module1`, partie de `main` (`d98455e`). Quatre retours sur la page du Module 1 : une section avec 2 ou 3 comptages finit à 0 camion après extrapolation (« le genre de faute qui passe entre les mailles de la complétion ») ; le bloc « Cadrer la carte » encombre le panneau ; les nœuds doivent rapetisser quand on zoome ; l'étape finale doit montrer le graphe avec ses données, puisque c'est le livrable du module.

**Diagnostic**
- `_extrapoler_extremites` prolongeait sur jusqu'à 8 ans la droite ajustée sur les 3 années connues les plus proches, puis la tronquait à 0. % de camions : 53 sections recevaient au moins une année à 0, dont 19 en 2025 ; DJMA : 7 sections, dont 1 en 2025. Toutes avaient des mesures positives du ministère, qui ne publie jamais 0 pour le DJMA et une seule fois pour le % de camions. Exemple : section 33889 (route 132), 9,2 % en 2017, 6,0 % en 2018, puis 0 de 2020 à 2025.
- Le 0 n'est que le cas extrême : en 2025, l'extrapolation du % de camions sortait de l'intervalle mesuré de sa section dans 1 730 cas (742 en dessous, 988 au-dessus), du DJMA dans 304 cas.
- Trouvé en écrivant les contrôles : la phase KNN réécrivait aussi le % de camions des 455 sections sans DJMA qui en ont un publié, soit 788 valeurs mesurées remplacées (l'atlas l'avait relevé, sans correction).
- Trouvé en relisant la page : la tuile « (0 km) » et la phrase « il touche l'arc complété dans les 19 cas » sont fausses pour Gatineau – Ontario-Sud, qui emprunte à Lachute – Thurso (39 km) depuis le correctif du 28 octobre ; `methodes.md` donnait déjà les bonnes distances.

**Fait**
- Page : étape 5 en mode graphe par défaut (ligne droite entre les deux nœuds, mêmes 307 identifiants que les tracés), bouton « Représentation : Graphe (résultat) | Tracés routiers » ; nœuds de rayon décroissant avec le zoom (2,6/3,6 px au zoom 4 → 1,6/2,2 px au zoom 11), contour sombre, couche de survol invisible ; bloc de cadrage retiré ; étape 2 : libellé de l'extrapolation bornée et années bornées dans la fiche d'une section ; tuile et note des 19 emprunts corrigées.
- Complétion (`98e0116`) : une valeur extrapolée est ramenée dans l'intervalle [min, max] mesuré de sa section (ancienne règle gardée en option, `borner=False`) ; colonnes `n_bornees_djma`, `n_bornees_cam` ; `controler_completion()` refuse une sortie avec une valeur manquante, un DJMA ≤ 0, un % de camions hors de [0, 100], une valeur complétée nulle sur une section mesurée ou une extrapolation hors de l'intervalle mesuré ; option `--echantillon`.
- KNN (`b293509`, commit séparé) : les sections qui ont un % de camions publié gardent leur propre série ; le contrôle vérifie qu'aucune valeur publiée n'est modifiée.
- `scripts/actualiser_valeurs_liaisons.py` : reporte les valeurs 2025 sur les liaisons sans refaire le routage OSRM. Contrôle : avec l'ancien fichier complété, il redonne le graphe à l'identique (0 écart sur 285 liaisons et 3 306 rattachements ; fichier réécrit identique, couche par couche).
- Figure `extrapolation_gradient.png` redessinée (sections 28787 et 33889, avec l'ancienne règle) ; couleurs vérifiées au validateur (bleu et saumon séparés pour les daltoniens).
- Données régénérées : complétion (échantillon de 4 720 sections, puis tout le Québec en 1 min 26 s), valeurs des liaisons, m1-m4, figures m1-m4 (sans la validation croisée de la forêt, réservée au labo 1), `jumeau reseau`, `jumeau tables`, atlas, pages des Modules 1 et 2.
- Anciennes données archivées hors Git dans `archives_locales/donnees_avant_bornage_2026-11-17/` (le labo 1 en a besoin pour reproduire 0,306 et 0,187 ; elles restent aussi dans l'historique Git).

**Chiffres**
- Bornées : 2 200 sections de % de camions (7 653 valeurs) et 551 de DJMA (2 102 valeurs). Valeurs complétées nulles : 0 pour le DJMA ; 9 pour le % de camions, qui recopient le 0 publié par le ministère sur la section 30625 (route 111).
- % de camions, par section : extrapolation 2 912 → 3 367, KNN 3 247 → 2 792 ; par valeur : KNN 31 672 → 27 920. Valeurs publiées écrasées : 788 → 0.
- 255 liaisons sur 307 et 2 128 rattachements sur 3 306 changent de valeur ; rattachements, statuts et donneurs identiques.
- 285 liaisons mesurées : médianes m1 11 947 → 11 899, m2 14 318 → 14 123, m3 et m4 inchangées ; maximum de m4 185 912 → 179 122 (Laval – Ville de Mont-Royal).
- Camions par jour (m4) : 165 liaisons sur 304 changent, écart médian −0,8 %, total −0,9 % ; 36 changent de plus de 10 %. Stanstead – Vermont et Cowansville – Vermont : 0 → 186. Joliette – Lavaltrie : 2 423 → 4 747 (le % publié de 19 % n'est plus écrasé). Sherbrooke – Cookshire : 1 299 → 274 (m4 bascule sur un autre segment).
- Module 3 : dans `reseau_multi_routes.gpkg`, seules les colonnes de valeurs complétées changent ; `observee`, `camions_obs` et `classe`, seules lues par le calage, sont identiques : pas de nouveau calage.
- 72 tests réussis (62 + 10 de la complétion) ; `ruff check src tests` propre.

**Revue critique**
- La borne est choisie par principe, pas mesurée : savoir si elle prédit mieux que le gradient libre ou qu'une simple recopie est la question du labo 3 (backtest de l'extrapolation), qui peut tester les deux règles.
- Elle aplatit les tendances réelles : une section mesurée en 2017 ne monte plus au-delà de son maximum, même si le trafic a crû depuis.
- Les R² de la forêt aléatoire (0,306 ; 0,187) datent d'avant le correctif et n'ont pas été recalculés (labo 1).
- m4 reste fragile pour les camions : il retient le % d'un seul segment, et un petit changement de DJMA peut faire basculer ce segment (Sherbrooke – Cookshire).
- Le rapport d'équipe (hors Git) et le portfolio CPCS décrivent encore l'ancienne règle.
