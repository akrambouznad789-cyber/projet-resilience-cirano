[← README](../README.md) · [Atlas des données](donnees_ml.md) · [Journal du projet](progress.md) · **Carte des pistes de recherche**

---

# Carte des pistes de recherche en apprentissage automatique

Dix questions que les données du projet permettent d'étudier, au-delà de celles des labos 1 à 4. Chaque piste tient en sept rubriques : la question, les données, la cible, une validation sans fuite, la base de comparaison, le coût de calcul sur le portable du projet (Intel i5-1135G7, 15,7 Go de mémoire, sans GPU) et le risque principal. Aucune n'est implémentée : le choix revient au chercheur.

Les données et leurs pièges sont décrits dans l'[atlas](donnees_ml.md). Les coûts s'appuient sur des durées mesurées : un calage complet du Module 3 en 2 min 15 s (validation croisée, calibration finale, bootstrap), une validation croisée seule en 75 s, une évaluation de la perte en 29 ms (phase 3 du [journal](progress.md)) ; `jumeau preparer` en 2 min 30 et `jumeau scenarios` en 17 min (phases 2 à 4). Les durées d'une piste sont des estimations faites à partir de ces mesures.

## Classement

| Rang | Piste | Ce qu'elle apporte | Coût | Risque principal |
|---|---|---|---|---|
| 1 | Ablations du Module 3 | savoir ce qui porte la vraisemblance du modèle | faible | écarts plus petits que la variation entre plis |
| 2 | Localisation des postes de l'ENR | le levier désigné par ε ≈ 0,27 | moyen | circularité entre modèle et localisation |
| 3 | Demande régionale (matrice origine–destination) | défaire la confusion entre g et le fond local | moyen | sur-ajustement sur 88 blocs |
| 4 | Où compter ensuite | un plan de comptage utile au ministère | moyen | dépend d'un modèle imparfait |
| 5 | Hétérogénéité du choix d'itinéraire | des camionneurs qui ne pèsent pas le temps de la même façon | moyen à élevé | identification avec 49 postes |
| 6 | Sensibilité structurelle du classement | une robustesse qui ne se limite pas à l'échantillonnage | élevé | variantes choisies après coup |
| 7 | Anomalies par cohérence du réseau | des valeurs publiées suspectes, sans modèle des séries | faible | conservation rompue aux échangeurs |
| 8 | Complétion spatiale du DJMA le long du réseau | 3 247 sections sans DJMA aujourd'hui comblées par KNN + IDW | faible à moyen | frontière avec le labo 4 |
| 9 | Émulateur des fermetures multiples | des scénarios que le modèle exact ne peut énumérer | moyen à élevé | apprendre les défauts du modèle |
| 10 | Durée et fréquence des fermetures | passer d'un impact « par an de fermeture » à un coût attendu | faible | données à obtenir du ministère |

Critères, dans l'ordre : l'apport à la crédibilité du Module 3, la faisabilité sur ce portable, le risque de fuite et l'indépendance vis-à-vis des questions des labos.

## Les dix pistes

### 1. Ablations du Module 3
- **Question :** quels éléments du modèle portent sa vraisemblance hors échantillon : la taille de chemin, β pour les kilomètres estimés, les β des frontières, le taux d'erreur ε, le fond local par classe, l'enquête elle-même ?
- **Données :** artefact du Module 3 (local, dérivé de l'ENR) ; 680 sections de calage, 49 postes.
- **Cible :** R² log des comptages et NLL de l'enquête en validation croisée.
- **Validation sans fuite :** les cinq plis actuels (blocs de 50 km × postes entiers), identiques pour toutes les variantes ; comparaison appariée, pli par pli.
- **Base de comparaison :** le modèle complet, B0 (routage du Module 2) et B1 (plus rapide).
- **Coût :** une validation croisée par variante, 75 s ; huit variantes en une dizaine de minutes.
- **Risque :** l'écart type du R² log entre plis vaut 0,09 à 0,11 ; une moyenne ne suffit pas, il faut l'écart apparié et sa dispersion.

### 2. Localisation des postes de l'ENR
- **Question :** la règle qui place chaque poste d'interception sur une route (la route voisine qui couvre le plus de camions interceptés) explique-t-elle une part du taux d'erreur ε ≈ 0,27 ?
- **Données :** artefact ; pour chaque poste, les routes candidates à moins de 15 km.
- **Cible :** vraisemblance de l'enquête et part des camions dont le poste est sur l'itinéraire le plus probable.
- **Validation sans fuite :** postes entiers mis de côté ; la localisation d'un poste n'est jamais choisie avec ses propres camions de validation.
- **Base de comparaison :** la règle actuelle, avec ε appris.
- **Coût :** 49 postes × quelques candidats, chacun évalué par la perte (29 ms par évaluation), puis un calage complet par configuration retenue.
- **Risque :** circularité : un poste « bien placé » par le modèle peut masquer un défaut du modèle. ε peut aussi venir des déclarations, pas des postes. Résultats publiés par poste seulement.

### 3. Demande régionale (matrice origine–destination)
- **Question :** des facteurs de croissance par région, à la place du facteur g unique, prédisent-ils mieux les comptages de 2025 hors échantillon, et séparent-ils g du fond local, aujourd'hui confondus ?
- **Données :** artefact (paires de l'ENR regroupées par région d'origine et de destination) ; 680 sections de calage.
- **Cible :** camions par jour en 2025 des sections de calage.
- **Validation sans fuite :** blocs de 50 km entiers ; un facteur régional ne doit pas s'ajuster sur les sections de son propre bloc de validation.
- **Base de comparaison :** g unique (modèle actuel) et B0.
- **Coût :** quelques dizaines de paramètres de plus, même coût par évaluation ; un calage complet par variante (2 min 15 s).
- **Risque :** 88 blocs de calage pour des dizaines de facteurs : rappel vers g indispensable. La structure des échanges reste celle de 2006-2007.

### 4. Où compter ensuite
- **Question :** quelles sections, si le ministère y publiait un comptage de camions, réduiraient le plus l'incertitude des paramètres du Module 3 ou l'instabilité de son classement de criticité ?
- **Données :** modèle calé, tirages du bootstrap, sections sans valeur publiée.
- **Cible :** réduction de la variance des paramètres (information de Fisher) ou de la variation du classement.
- **Validation sans fuite :** rétrospective : masquer des sections mesurées, en choisir k par la règle, mesurer le gain sur les autres sections, dans des blocs distincts.
- **Base de comparaison :** choix au hasard ; sections les plus chargées ; règle actuelle du ministère si elle est connue.
- **Coût :** un calage par choix simulé (2 min 15 s), ou un calcul d'information de Fisher en quelques évaluations (29 ms).
- **Risque :** le plan hérite des défauts du modèle ; utile seulement si le Module 3 est jugé crédible par ailleurs.

### 5. Hétérogénéité du choix d'itinéraire
- **Question :** un logit mixte, ou à classes latentes (longs trajets contre trajets régionaux), décrit-il mieux les postes franchis qu'un logit à taille de chemin avec un μ unique ?
- **Données :** artefact : groupes de camions, postes franchis, itinéraires.
- **Cible :** NLL de l'enquête sur des postes mis de côté.
- **Validation sans fuite :** postes entiers mis de côté, comme aujourd'hui ; classes définies par des attributs connus avant le trajet (distance de la paire), jamais par le poste franchi.
- **Base de comparaison :** le logit actuel, B0, B1.
- **Coût :** deux à cinq fois une évaluation actuelle par classe ou par point d'intégration ; quelques calages complets.
- **Risque :** 49 postes localisés et ε ≈ 0,27 : l'hétérogénéité peut absorber des erreurs de localisation plutôt que des préférences.

### 6. Sensibilité structurelle du classement de criticité
- **Question :** le classement des liaisons critiques survit-il à d'autres ensembles de chemins (k, détour maximal), à d'autres vitesses par classe et à d'autres localisations de postes ? La robustesse publiée (τ de Kendall 0,994) ne couvre que l'échantillonnage.
- **Données :** artefact régénéré pour chaque variante, puis recalé.
- **Cible :** τ de Kendall et recouvrement du top 20 entre variantes.
- **Validation sans fuite :** chaque variante est recalée sur les mêmes plis ; seules les variantes déclarées avant les calculs comptent.
- **Base de comparaison :** le classement actuel.
- **Coût :** par variante, `jumeau preparer` (2 min 30), un calage (2 min 15 s) et `jumeau scenarios` (17 min, mesurées avant l'accélération), soit environ 22 min, sous le seuil de 30 min ; six variantes demandent plus de deux heures et se lancent une à une.
- **Risque :** le choix des variantes est un choix de modélisation : à préenregistrer.

### 7. Anomalies des comptages publiés par cohérence du réseau
- **Question :** la conservation des débits le long d'une route (une section entre deux voisines de même numéro, les deux chaussées d'une route divisée) permet-elle de repérer des DJMA publiés suspects, sans modèle des séries ?
- **Données :** tables `sections_annees` et `aretes_sections`, graphe RTSS.
- **Cible :** aucune étiquette ; anomalies injectées (valeur doublée ou divisée par deux, section déplacée).
- **Validation sans fuite :** injections dans des blocs mis de côté ; rappel à taux de fausses alarmes fixé ; une même année à la fois, sans recours aux autres années de la section.
- **Base de comparaison :** écart à la médiane des voisines de même numéro.
- **Coût :** quelques secondes par configuration.
- **Risque :** la conservation est rompue légitimement aux échangeurs et aux limites de ville ; les DJMA sont déjà une estimation lissée par le ministère.

### 8. Complétion spatiale du DJMA le long du réseau
- **Question :** pour les 3 247 sections sans aucun DJMA publié, une méthode qui suit le réseau (distance le long des routes, même numéro, même classe) fait-elle mieux que le KNN + IDW actuel, qui mesure les distances à vol d'oiseau ?
- **Données :** table `sections_annees` (cibles publiées seulement), graphe RTSS.
- **Cible :** DJMA publié (37 344 valeurs, 159 blocs).
- **Validation sans fuite :** blocs de 50 km entiers mis de côté ; toutes les années d'une section du même côté du découpage.
- **Base de comparaison :** le KNN + IDW du Module 1, réévalué sur les mêmes blocs.
- **Coût :** faible : plus courts chemins sur 16 460 nœuds, quelques minutes.
- **Risque :** à tenir à distance du labo 4 : ni réseau de neurones sur graphe, ni PyTorch Geometric ; une méthode sans apprentissage profond (krigeage le long du réseau, plus proches voisins sur le graphe).

### 9. Émulateur des fermetures multiples
- **Question :** un modèle appris sur les sorties du jumeau peut-il prédire l'effet d'une fermeture double ou régionale, que le modèle exact ne peut énumérer ?
- **Données :** scénarios simples (570) et un échantillon de fermetures doubles calculées par le modèle.
- **Cible :** Δ heures du meilleur itinéraire et trajets non desservis.
- **Validation sans fuite :** les scénarios d'une région entière mis de côté.
- **Base de comparaison :** la somme des effets simples ; le modèle exact sur l'échantillon.
- **Coût :** une fermeture coûte 0,29 s par l'API (phase 6, avant l'accélération du calage) : mille fermetures doubles prennent environ 5 min, l'énumération des 46 971 paires de liaisons près de 3 h 50, au-delà du seuil de 30 min.
- **Risque :** l'émulateur apprend le modèle, défauts compris ; les combinaisons les plus critiques sont rares.

### 10. Durée et fréquence des fermetures
- **Question :** avec un historique des fermetures (tronçon, date, durée, cause, détour balisé), quelle est la durée attendue d'une fermeture par liaison, et le coût annuel attendu qui en découle ?
- **Données :** historique demandé au ministère, non disponible aujourd'hui ; criticité du Module 3.
- **Cible :** durée et fréquence des fermetures par tronçon.
- **Validation sans fuite :** découpage chronologique (années récentes mises de côté) ; tronçons entiers.
- **Base de comparaison :** durée uniforme ; moyenne par classe de route.
- **Coût :** faible.
- **Risque :** les données n'existent peut-être pas sous cette forme ; toute nouvelle donnée passe d'abord par l'accord du Pr Trépanier.

## Trois pistes recommandées

1. **Les ablations du Module 3, d'abord.** Le modèle calé ne bat pas le routage simple sur les comptages (R² log 0,53 contre 0,55) et gagne sur l'enquête : il faut savoir quels éléments justifient sa complexité avant d'en ajouter. C'est la piste la moins coûteuse depuis l'accélération du calage, et chacune des autres pistes du Module 3 aura besoin de ses variantes comme référence.
2. **La localisation des postes, ensuite.** Le journal la désigne depuis la calibration comme le levier principal : ε ≈ 0,27, une couverture des postes de 69 %, et Lacolle mal placé, faute de plus d'un nœud frontière par État dans le graphe. C'est un problème à variable latente (quelle route pour chaque poste ?), qui améliore directement la vraisemblance du choix d'itinéraire.
3. **La demande régionale, enfin.** Elle s'attaque à la plus vieille limite du jumeau : une demande de 2006-2007 mise à l'échelle par un seul facteur, confondu avec le fond local. Son estimation passe par le même modèle différentiable, donc par le même code, et c'est elle qui rendrait les reports de trafic crédibles pour 2025.

La quatrième, « où compter ensuite », a le plus de valeur pour le ministère ; elle gagne à venir après les trois premières, qui rendront le modèle sur lequel elle s'appuie plus sûr.

## Idées notées en chemin

- **Phase 2, tables :** l'écart de jonction (`ecart_jonction_m`) pourrait pondérer les arêtes estimées dans un apprentissage sur graphe. C'est la question du labo 4 : l'idée est notée, pas suivie.
- **Phase 2, tables :** DJME et DJMH sont des cibles sœurs du DJMA. Une décomposition saisonnière relève de la prévision des séries (labo 3) : notée, pas suivie.
- **Phase 3, calage :** sur le couloir Montréal–Québec, le taux d'erreur ε atteint sa borne de 0,5 : l'enquête n'y apporte plus d'information. Celle-ci tient surtout aux longs trajets qui franchissent plusieurs postes, ce qui nourrit les pistes 2 et 4.
- **Atlas :** les dix blocs les plus denses réunissent 56 % des sections. Toute validation par blocs gagne à rapporter, à côté de la moyenne, le résultat par bloc ou pondéré par bloc.
- **Atlas :** 455 sections ont un % de camions publié mais aucun DJMA. Leur complétion relève du labo 1 : notée, pas suivie.

## Ce que cette carte ne touche pas

Les questions des labos 1 à 4 sont réservées : la complétion du % de camions (labo 1), les intervalles par segment et la prédiction conforme (labo 2), le backtest de l'extrapolation du DJMA et toute prévision des séries (labo 3), les réseaux de neurones sur graphe (labo 4). Aucune piste n'en reprend la question, et aucun calcul n'a été fait pour écrire cette carte : ses chiffres viennent de l'atlas et du journal.
