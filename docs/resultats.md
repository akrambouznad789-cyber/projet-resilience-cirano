[← Méthodes](methodes.md) · **Résultats** · [README](../README.md)

---

## Résultats

L'ensemble du pipeline — complétion, routage, agrégation DJMA — transforme les 307 liens ville-à-ville du graphe de départ en un réseau routier enrichi en trafic réel, prêt à servir de base à l'analyse de résilience : **285 arcs sur 307 (92,8 %)** portent une valeur DJMA calculée directement à partir de 3 306 segments de comptage MTQ, et **19 autres** en reçoivent une par emprunt au plus proche voisin, ce qui porte la couverture à **304 arcs sur 307 (99 %)**.

![Réseau enrichi — DJMA par arc et couverture du routage](../figures/carte_reseau_resultats.png)

Les 22 arcs en échec ne sont pas un problème de complétion des données : la cascade d'imputation (section précédente) porte la complétude temporelle des stations MTQ *existantes* à 100 %, mais elle ne peut pas faire apparaître une station là où le MTQ n'en a jamais installé. Le KNN géographique de la [complétion des données](completion.md#3-knn-géographique--idw) comble les trous temporels d'une station *déjà présente* dans `DebitCirculation.gpkg` en lui empruntant la série d'une station voisine — il n'invente jamais une station là où le MTQ n'en a posé aucune. Les arcs en échec, eux, se jouent une étape plus loin : après routage et filtrage géométrique, aucun segment RTSS porteur d'une mesure ne se retrouve associé à l'arc, et m1 à m4 n'ont alors rien à agréger. Ces arcs sont donc traités en aval, par la [complétion géographique des échecs](methodes.md#complétion-géographique-des-échecs), qui leur emprunte le DJMA de l'arc valide le plus proche.

Concrètement, 19 arcs n'ont aucun segment propre parce qu'ils empruntent un tracé purement intraurbain (surtout île de Montréal) que ne croise aucune station de comptage — le MTQ compte le réseau provincial, pas les rues municipales. Ils reçoivent une valeur empruntée, toujours signalée comme telle dans les données. Dans les 19 cas, l'arc donneur partage directement un nœud (une ville) avec l'arc complété (distance 0 km) : c'est la valeur du corridor adjacent, jamais une estimation fondée sur une proximité lointaine. Les 3 derniers arcs sortent du territoire québécois (liaisons interprovinciales), n'ont aucun segment RTSS/DJMA à associer et restent sans valeur.

![Zoom — Grand Montréal](../figures/carte_montreal_resultats.png)

13 des 19 échecs intraurbains se concentrent dans un rayon de 55 km autour de Montréal (île, couronnes nord et sud) — trop denses pour rester lisibles à l'échelle du Québec ci-dessus. Ce zoom les isole individuellement : chacun correspond à une paire de villes limitrophes (ex. Pointe-Claire–Dollard-des-Ormeaux, Hampstead–Côte-Saint-Luc) où le tracé le plus court ne croise jamais le réseau provincial compté par le MTQ.

| Indicateur | Valeur |
|---|---|
| Arcs avec mesure MTQ directe | 285 / 307 (92,8 %) |
| Segments DJMA mobilisés | 3 306 (médiane 8 par arc) |
| Longueur de tracé médiane | 25,8 km (couverte à 24,9 km par le RTSS québécois) |
| Échecs de routage — intraurbain, aucune station MTQ | 19 — complétés par emprunt au plus proche voisin |
| Échecs de routage — hors territoire québécois | 3 — sans valeur |
| **Couverture globale** | **304 / 307 (99 %)** |

---

[← Méthodes](methodes.md) · [README](../README.md)
