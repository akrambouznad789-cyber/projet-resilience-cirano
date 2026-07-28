[← README](../README.md) · **Données** · [Complétion →](completion.md)

---

## Données

Le projet croise trois couches géographiques distinctes, qui répondent chacune à une question différente sur le même territoire :

| Couche | Fichier | Rôle | Modifiable ? |
|---|---|---|---|
| **Nœuds et liens** | `data/raw/reseau_arcs.gpkg` (couches `noeuds`, `arcs`) | Définit *quelles villes* et *quelles paires de villes* on étudie — le graphe simplifié du projet | Oui — un simple fichier (id, nom, ville A, ville B, distance) : ajouter ou retirer une ville ne touche à rien d'autre dans le pipeline |
| **Réseau routier (RTSS)** | `data/raw/ReseauRoutier_RTSS.gpkg` (MTQ) | La géométrie détaillée des routes du Québec, avec leur classification (autoroute, nationale, régionale, collectrice…) — sert à router chaque lien sur de vraies routes | Non — donnée source du MTQ |
| **Comptage routier** | `data/raw/DebitCirculation.gpkg` (MTQ) | 7 823 stations de comptage réel, chacune avec jusqu'à 10 ans de mesures (DJMA, DJME, DJMH, % camions) | Non — donnée source du MTQ, c'est elle qui porte le problème de données manquantes |

### Réseau graphe — nœuds et liens

307 liens simplifiés relient 207 villes du Québec (municipalités, nœuds intermédiaires, points frontière). C'est le graphe étudié par le projet, indépendant des deux couches suivantes — modifiable en éditant directement `reseau_arcs.gpkg`.

![Réseau graphe — nœuds et liens](../figures/reseau_graphe.png)

| Nœuds | Description |
|---|---|
| `ID` | Identifiant unique du nœud |
| `NOM` | Nom de la ville / municipalité |
| `TYPE` | Municipalité, nœud intermédiaire ou point frontière |
| `NB_ARCS` | Nombre de liens connectés à ce nœud |

| Arcs | Description |
|---|---|
| `ID_ARC` | Identifiant unique du lien |
| `VILLE_A` / `VILLE_B` | Les deux villes reliées par ce lien |
| `DIST_KM` | Distance à vol d'oiseau entre A et B |
| `SOURCE` | Comment la paire a été retenue (BFS, forcé, nœud, frontière) |

### Réseau routier — RTSS

Chaque lien du graphe est ensuite routé sur le réseau routier réel du MTQ — 12 567 segments classifiés par type de route — qui sert de base géométrique au routage (voir [Routage](routage.md)).

![Réseau routier — RTSS, coloré par type de route](../figures/reseau_routier.png)

| Type de route | Segments | % |
|---|---|---|
| Autoroute | 4 222 | 33,6 % |
| Nationale | 2 859 | 22,8 % |
| Régionale | 1 581 | 12,6 % |
| Collectrice | 1 963 | 15,6 % |
| Autre / sans classe | 1 942 | 15,5 % |

### Comptage routier

Enfin, 7 823 stations de comptage MTQ portent les mesures de trafic (DJMA, % camions) qui viennent enrichir le réseau — la table brute a 110 colonnes, peu lisibles telles quelles :

| Variable | Description |
|---|---|
| `ide_sectn_trafc` | Identifiant unique du segment de comptage |
| `des_debut` / `fin_sous_route` | Description textuelle des deux extrémités |
| `djma_annee_i` / `val_djma_annee_i` | Année mesurée / valeur du DJMA (i = 1 à 10) |
| `cam_annee_i` / `val_cam_annee_i` | Année mesurée / valeur du % camions (i = 1 à 10) |

C'est ici que se loge le problème de données manquantes : seuls **34,7 %** des segments ont un DJMA complet sur 10 ans, et seulement **1,7 %** ont un % camions complet.

![Comptage routier — complétude du DJMA](../figures/comptage_routier.png)

![Complétude DJMA vs % camions](../figures/comptage_completude.png)

Un DJMA complet ne garantit presque jamais un % camions complet. C'est ce trou précis que la section suivante comble par apprentissage automatique.

---

[← README](../README.md) · [Complétion →](completion.md)
