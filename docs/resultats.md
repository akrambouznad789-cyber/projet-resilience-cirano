[← Méthodes](methodes.md) · **Résultats** · [README](../README.md)

---

## Résultats

L'ensemble du pipeline — complétion, routage, agrégation DJMA — transforme les 307 liens ville-à-ville du graphe de départ en un réseau routier enrichi en trafic réel, prêt à servir de base à l'analyse de résilience : **285 arcs sur 307 (92,8 %)** portent désormais une valeur DJMA fiable, construite à partir de 3 306 segments de comptage MTQ.

![Réseau enrichi — DJMA par arc et couverture du routage](../figures/carte_reseau_resultats.png)

Les 22 arcs en échec ne sont pas un problème de complétion des données : la cascade d'imputation (section précédente) porte la complétude temporelle des stations MTQ *existantes* à 100 %, mais elle ne peut pas faire apparaître une station là où le MTQ n'en a jamais installé. Le KNN géographique de la [complétion des données](completion.md#3-knn-géographique--idw) comble les trous temporels d'une station *déjà présente* dans `DebitCirculation.gpkg` en lui empruntant la série d'une station voisine — il n'invente jamais une station là où le MTQ n'en a posé aucune. Les arcs en échec, eux, se jouent une étape plus loin : après routage et filtrage géométrique, aucun segment RTSS porteur d'une mesure ne se retrouve associé à l'arc, et il n'existe à ce stade aucun mécanisme de repli — m1 à m4 n'ont alors rien à agréger.

Concrètement, 19 arcs restent sans valeur parce qu'ils empruntent un tracé purement intraurbain (surtout île de Montréal) que ne croise aucune station de comptage — le MTQ compte le réseau provincial, pas les rues municipales. Les 3 derniers sortent du territoire québécois (liaisons interprovinciales) et n'ont donc aucun segment RTSS/DJMA à associer.

![Zoom — Grand Montréal](../figures/carte_montreal_resultats.png)

13 des 19 échecs intraurbains se concentrent dans un rayon de 55 km autour de Montréal (île, couronnes nord et sud) — trop denses pour rester lisibles à l'échelle du Québec ci-dessus. Ce zoom les isole individuellement : chacun correspond à une paire de villes limitrophes (ex. Pointe-Claire–Dollard-des-Ormeaux, Hampstead–Côte-Saint-Luc) où le tracé le plus court ne croise jamais le réseau provincial compté par le MTQ.

| Indicateur | Valeur |
|---|---|
| Arcs enrichis | 285 / 307 (92,8 %) |
| Segments DJMA mobilisés | 3 306 (médiane 8 par arc) |
| Longueur de tracé médiane | 25,8 km (couverte à 24,9 km par le RTSS québécois) |
| Échecs — intraurbain, aucune station MTQ | 19 |
| Échecs — hors territoire québécois | 3 |

---

[← Méthodes](methodes.md) · [README](../README.md)
