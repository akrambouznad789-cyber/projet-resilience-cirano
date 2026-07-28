[← Données](donnees.md) · **Complétion** · [Routage →](routage.md) · [README](../README.md)

---

## Complétion des données

Source : `DebitCirculation.gpkg` (MTQ), 7 823 segments routiers, chacun avec jusqu'à 10 années de mesures DJMA (débit) et %camion. Une grande partie de ces mesures sont manquantes : seuls **2 716 segments (35 %)** ont un DJMA complet sans aucun trou, et seulement **132 segments (2 %)** ont un %camion complet.

La complétion se fait en cascade, du plus fiable au plus incertain.

### 1. Interpolation / extrapolation temporelle

Les trous internes (un an manquant entouré de valeurs connues) sont comblés par interpolation linéaire. Aux deux extrémités de la plage manquante, chaque bord prolonge son propre **gradient local** — une régression sur les 3 points connus les plus proches de ce bord — plutôt qu'une tendance unique calculée sur toute la plage connue : si le début et la fin de la série n'évoluent pas dans le même sens, un bord ne doit pas hériter de la pente de l'autre.

![Interpolation et extrapolation — gradient local par bord](../figures/extrapolation_gradient.png)

Exemple réel (segment #28787) : entre 2017 et 2022, le DJMA chute d'abord fortement (8 700 → 6 200) puis remonte légèrement (5 300 → 5 900). Une régression globale unique sur ces 6 points aurait extrapolé une pente moyenne descendante sur toute la plage — 2016 estimé à 9 007, 2025 à 3 247 — en contradiction avec la reprise observée en fin de série. En prolongeant plutôt le gradient propre à chaque bord (2017-2019 pour 2016, 2020-2022 pour 2023-2025), 2016 est estimé à 10 300 (cohérent avec la chute qui précède) et 2025 à 6 933 (cohérent avec la reprise qui suit).

### 2. RandomForest (IterativeImputer, MICE)

Pour les 910 segments avec un DJMA connu mais **aucune** valeur de %camion, un `RandomForestRegressor` (50 arbres) apprend la relation entre %camion et 38 features : le profil DJMA, les débits de pointe (DJME/DJMH), le profil horaire (30e heure), le type de route (jointure spatiale la plus proche avec le réseau RTSS — autoroute/nationale/régionale/collectrice) et la localisation géographique du segment.

**Validation honnête** (validation croisée 5-fold, hors échantillon) :

![Validation RandomForest](../figures/validation_randomforest.png)

R² = 0,306, RMSE = 6,1 points. Avec le seul DJMA comme prédicteur, le modèle était peu informatif (R² = 0,037) : la corrélation brute DJMA ↔ %camion est quasi nulle (Pearson −0,13), un segment à fort débit n'étant pas nécessairement plus emprunté par les camions. En ajoutant le type de route, les débits de pointe et la localisation, le modèle capte un signal réel — le type d'axe (autoroute logistique vs route urbaine) explique une bonne part du %camion, là où le débit seul n'en disait presque rien.

![RandomForest — prédictions par type de route](../figures/randomforest_subsets.png)

Ce signal se voit en séparant les prédictions par type de route, le "subset" le plus structurant du modèle : R² = 0,44 sur les routes nationales et 0,38 sur les autoroutes, mais seulement 0,11 sur les collectrices et −0,03 sur les segments hors classification (« Autre ») — moins bon que la moyenne. Le résultat reste donc imparfait et inégal selon le type d'axe, mais le RandomForest enrichi est un estimateur nettement plus convaincant que le modèle DJMA-seul pour ces 910 segments qui, sans lui, n'auraient aucune valeur du tout.

### 3. KNN géographique + IDW

En dernier recours, pour les segments sans aucune mesure d'aucune année, complétion par pondération inverse à la distance (IDW, poids = 1/distance²) sur les 5 stations voisines les plus proches, avec priorité aux voisins de même direction (`index_agreg`, nord/ouest).

![Complétion géographique — KNN + IDW](../figures/knn_geographique.png)

Exemple réel (segment #30014) : ses 5 voisins pondérés vont de 1 520 à 14 015 véh./jour selon leur distance — le plus proche (2 421 véh./jour, à ~1 km) pèse 43 % de l'estimation finale, le plus éloigné (14 015 véh./jour, à ~4,3 km) seulement 14 %. La valeur résultante (5 302 véh./jour) est une moyenne pondérée par cette proximité, pas une simple moyenne des 5.

**Important — à ne pas confondre avec les échecs de la section [Résultats](resultats.md) :** ce KNN comble les trous temporels d'une station de comptage MTQ qui *existe déjà* dans `DebitCirculation.gpkg`, en lui empruntant la série d'une station voisine. Il n'invente jamais une station là où le MTQ n'en a posé aucune — c'est cette distinction qui explique pourquoi certains arcs du réseau restent malgré tout sans valeur DJMA après le routage.

### Synthèse — évolution de la complétude

![Évolution de complétude des données](../figures/evolution_completion.png)

DJMA et %camion ne progressent pas au même rythme à travers la cascade : l'étape 1 comble la majorité du DJMA (35 % → 58 %) mais seulement la moitié du %camion (2 % → 53 %), faute de valeurs connues sur ces segments pour interpoler. L'étape 2 (RandomForest) est spécifique au %camion — elle ne touche jamais le DJMA, d'où le palier à 58 % sur cette courbe. L'étape 3 (KNN géographique) ferme les deux à 100 % : les **7 823 segments × 10 ans** ont désormais une valeur DJMA et %camion, sans aucune case vide.

---

[← Données](donnees.md) · [Routage →](routage.md) · [README](../README.md)
