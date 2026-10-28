# Volet 3 · Le jumeau : fiche du modèle

Modèle d'affectation des camions sur le réseau routier québécois, calé sur des observations, puis utilisé pour simuler des fermetures de liaisons. Cette fiche suit le format des *model cards* : à quoi sert le modèle, sur quoi il a été calé, ce qu'il vaut sur des données mises de côté, et ce qu'il ne faut pas lui demander.

**[Ouvrir la page du jumeau →](https://akrambouznad789-cyber.github.io/projet-resilience-cirano/docs/jumeau/)** · Code : [`src/jumeau_cirano/`](../src/jumeau_cirano/) · Journal des décisions : [`progress.md`](progress.md) · Contrat de l'API : [`api/openapi.json`](api/openapi.json)

| | |
|---|---|
| Version | `jumeau-cirano` 0.1.0, objectif « équilibre », calé le 2026-10-28 |
| Type | Logit à taille de chemin (*path-size logit*), non congestionné, 10 paramètres libres et 7 β de points frontières |
| Cadre | PyTorch (calibration différentiable), JavaScript (même calcul dans la page), FastAPI (service) |
| Auteur | Akram Bouznad, Polytechnique Montréal / CIRANO |

## Usage prévu

- **Prévu :** classer les liaisons du réseau interurbain selon ce que coûterait leur fermeture ; voir où se reporte le trafic de camions quand une liaison ou une route ferme ; explorer la sensibilité de ces résultats aux paramètres du modèle.
- **Destinataires :** l'équipe du jumeau numérique du CIRANO (intégration par l'API ou les modules JavaScript), les analystes du MTMD et de Transports Canada.
- **Hors usage :** prévoir le trafic d'une journée précise ; étudier la congestion (le modèle n'a pas de capacités) ; chiffrer officiellement une perte économique (les $ sont provisoires) ; analyser les rues municipales (le réseau est celui des 307 liaisons interurbaines).

## Données

| Source | Rôle | Volume |
|---|---|---|
| Réseau routier RTSS (MTMD) | graphe routier noué, plusieurs routes par liaison | 459 routes pour 307 liaisons, dont 111 à deux ou trois routes |
| Débits de circulation 2025 (MTMD) | **cibles** : camions par jour mesurés | 680 sections mesurées en 2025, sur 3 648 portées par une route |
| Enquête nationale en bordure de route 2006-2007 (MTQ, Transports Canada) | demande (origine, destination, marchandise) et **cibles** : postes franchis par chaque camion | 37 235 camions enquêtés, 20 484 paires de lieux, 257 952 itinéraires |

Les valeurs imputées par le Volet 1 (RandomForest) ne servent jamais de cibles : on n'apprend pas nos propres estimations. Les données de l'ENR sont confidentielles (voir plus bas).

## Le modèle

Chaque camion de l'enquête choisit parmi les itinéraires de sa paire de lieux (au plus 20, fixés une fois pour toutes), d'autant plus souvent qu'ils sont peu coûteux :

```
coût_p   = Σ_r∈p (minutes_r + β_ha · km hors autoroute_r + β_e · km estimés_r) + minutes d'accès_p + Σ_f β_f · passages_pf
P(p | i) = softmax sur les itinéraires de la paire i de ( −μ · coût_p + ln PS_p )      PS : taille de chemin
flux_r   = g · Σ_p trajets ENR_i(p) · P(p | i) · δ_rp / 365                             camions par jour, 2025
ŷ_s      = Σ_r flux_r · δ_rs + fond[classe RTSS de s]                                  camions par jour sur la section s
```

`g` met à l'échelle les trajets de 2006-2007 vers 2025 ; `fond` compte les camions absents de l'enquête (trafic local), par classe de route. Le même calcul existe en JavaScript ([`modele.js`](../scripts/gabarits/js/modele.js)) ; un test vérifie que les deux donnent les mêmes résultats à 1e-6 près.

## Calibration

- **Perte** (objectif « équilibre ») : déviance de Poisson relative sur les comptages + vraisemblance composite de l'enquête, poste par poste et conditionnelle au poste d'interception (correction de l'échantillonnage fondé sur le choix) + rappel léger vers les valeurs initiales.
- **Erreur de classement des postes ε**, apprise : P(poste noté franchi) = ε + (1 − 2ε) · q. Sans elle, l'enquête seule poussait μ vers zéro (postes mal localisés).
- **Optimisation** : Adam (200 itérations) puis L-BFGS (40), graines fixées. Intervalles à 95 % par bootstrap (20 tirages, poids de Poisson). Chaque essai est suivi dans MLflow (base locale).

| Paramètre | Calé | IC 95 % | Lecture |
|---|---|---|---|
| μ | 0,078 /min | 0,069 ; 0,085 | sensibilité au coût |
| β hors autoroute | −0,27 min/km | −0,28 ; −0,25 | corrige surtout les vitesses supposées hors autoroute |
| β km estimés | 6,7 min/km | 6,2 ; 7,0 | raccords et accès absents du RTSS |
| g | 0,60 | 0,50 ; 0,83 | confondu avec le fond local : ne pas l'interpréter seul |
| ε | 0,27 | 0,26 ; 0,28 | plus d'un quart des postes notés seraient mal notés ou mal localisés |
| fond (autoroutes) | 3 760 camions/j | 3 131 ; 4 239 | camions absents de l'enquête |

## Évaluation sur des données mises de côté

Validation croisée à 5 plis : blocs de sections de 50 km × postes d'enquête entiers (aucune fuite spatiale). Moyenne ± écart type.

| | R² en log | erreur médiane | part à ±30 % | NLL de l'enquête | poste retrouvé |
|---|---|---|---|---|---|
| B0 : routage du Volet 2 (plus court, une route) | **0,552** ± 0,09 | **0,442** | 0,344 | 2,183 | 0,491 |
| B1 : plus rapide sur le réseau à plusieurs routes | 0,544 ± 0,09 | 0,450 | 0,353 | 2,215 | **0,539** |
| Modèle calé | 0,531 ± 0,11 | **0,442** | **0,354** | **2,032** | 0,468 |

**Le modèle calé ne bat pas le routage du Volet 2 sur les comptages** (l'écart est inférieur à un écart type). Il gagne nettement sur la vraisemblance de l'enquête (−0,15 nat par camion). Son apport n'est pas de mieux prédire les débits : c'est une répartition calée entre itinéraires, sans laquelle aucun report de trafic ne peut être simulé (B0 et B1 envoient tout sur un seul chemin).

## Scénarios de résilience

Chaque liaison est fermée à tour de rôle (307 scénarios), puis chaque route d'une liaison qui en a plusieurs (263). Les camions se reportent selon le logit ; une paire privée de tout itinéraire prend le plus court détour du réseau dégradé ; sans détour, ses trajets ne sont pas desservis.

- **Classement** sur les heures équivalentes perdues (coût composite du logit, « logsum »), qui ne peut que monter quand une option disparaît. Le temps moyen, qui peut baisser à tort, reste descriptif.
- **Seconde mesure** : la perte sur le meilleur itinéraire seulement. Pour les liaisons, les deux concordent (τ de Kendall 0,925, rapport médian 1,04). Pour une seule route, le logsum vaut en médiane 2,1 fois la perte du meilleur itinéraire : les chiffres par route se lisent avec cette seconde mesure.
- **Liaisons les plus critiques** : Gatineau–Ontario-Sud (2,05 M heures équivalentes par an de fermeture), Saint-Zotique–Ontario-Sud par l'A-20 (2,03 M), Cabano–Nouveau-Brunswick (1,87 M).
- **Robustesse** : sur 10 tirages du bootstrap, τ de Kendall moyen 0,994 (minimum 0,980), top 20 retrouvé à 99,5 %.

## Hypothèses et limites

- **Pas de congestion** : faute de capacités, le temps d'une route ne dépend pas de son trafic. Une fonction de type BPR pourra s'ajouter.
- **Demande de 2006-2007**, mise à l'échelle par un facteur g confondu avec le fond local. La structure des échanges a pu changer depuis.
- **Ensembles d'itinéraires fixés** : les résultats dépendent de leur construction (au plus 20 par paire, détour limité).
- **Vitesses par classe de route supposées**, non mesurées.
- **Postes d'enquête mal localisés** : 69 % de couverture, un seul nœud frontière par État (Lacolle notamment). C'est le premier levier d'amélioration, avant tout raffinement du modèle.
- **Coupures** (27 liaisons) : surtout des artefacts du graphe à 307 liaisons (Bas-Saint-Laurent : l'A-20 et la R-132 sur les mêmes liaisons, ni R-204 ni traversiers).
- **Les intervalles et la robustesse** ne couvrent que l'incertitude d'échantillonnage, pas celle de la structure du modèle. Un classement stable n'est pas un classement exact.
- **Montants provisoires** : 90 $/h par camion et 3 000 $/t, à remplacer par le guide avantages-coûts du MTMD et les $/t du Volet 2. Une fermeture réelle dure des jours : les montants sont donnés par an de fermeture et par jour.

## Confidentialité

L'artefact du modèle (itinéraires, demande par paire de lieux) dérive des microdonnées de l'ENR : il reste dans `outputs/`, hors du dépôt, et n'entre jamais dans l'image Docker (il y est monté en lecture seule). Sont publiés seulement : les paramètres et les métriques, la criticité par liaison et par route, le report de trafic par route, la page publique. Aucune de ces sorties ne contient de flux entre lieux. Les tests et la CI tournent sur un réseau synthétique.

## Intégration au jumeau numérique officiel

| Brique | Pour quoi | Où |
|---|---|---|
| API HTTP | `GET /sante`, `GET /modele`, `POST /affectation`, `POST /scenario`, `GET /criticite` | [`api/app.py`](../src/jumeau_cirano/api/app.py), contrat [`openapi.json`](api/openapi.json) |
| Conteneur | image `python:3.12-slim`, utilisateur sans privilèges, `HEALTHCHECK`, construite et testée par la CI | [`Dockerfile`](../Dockerfile) |
| Paquet de démonstration | réseau jouet, sans donnée confidentielle, pour brancher l'API avant d'avoir le vrai paquet | `python -m jumeau_cirano.api.synthetique demo/` |
| Modules JavaScript | affectation et fermetures dans le navigateur, sans DOM | [`modele.js`](../scripts/gabarits/js/modele.js), [`scenario.js`](../scripts/gabarits/js/scenario.js) |
| Jetons de design | thème sombre et clair, palettes validées (contraste, daltonisme), format Style Dictionary | [`jetons.json`](../scripts/gabarits/theme/jetons.json) |

## Reproduire

```bash
uv sync --all-extras --group dev                                  # Python 3.12, PyTorch CPU
uv run jumeau reseau                                              # graphe RTSS, routes, sections (données MTMD)
uv run jumeau preparer                                            # artefact : nécessite l'ENR (ENR_DIR)
uv run jumeau calibrer --objectif configs/objectifs/equilibre.yaml
uv run jumeau scenarios                                           # 570 fermetures et robustesse (≈ 17 min)
uv run python scripts/visualisation_jumeau.py --publique          # pages locale et publique
uv run jumeau exporter && JUMEAU_ARTEFACT=outputs/jumeau/service uv run jumeau api
uv run pytest                                                     # tests, dont la parité JavaScript / Python
```
