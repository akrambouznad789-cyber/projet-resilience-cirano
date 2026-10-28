[← Résultats du Volet 1](resultats.md) · **Volet 2** · [README](../README.md)

---

## Volet 2 — La valeur de ce qui circule sur chaque liaison

> **Statut : premier jet, octobre 2026.** Les étapes de données (enquête, filtre, localisation, routage) sont réelles. Les prix par tonne et l'actualisation en dollars de 2026 sont encore provisoires : les montants affichés servent à montrer la méthode et ne doivent pas être cités.

**[Ouvrir la carte interactive →](https://akrambouznad789-cyber.github.io/projet-resilience-cirano/docs/volet2/carte_interactive.html)**

### L'idée

Le Volet 1 sait combien de camions passent chaque jour sur une liaison. Il ne sait pas ce qu'ils transportent. L'**Enquête nationale en bordure de route** (ENR 2006-2007, MTQ et Transports Canada) apporte cette pièce manquante : des enquêteurs ont arrêté des camions au bord de la route pour noter d'où ils venaient, où ils allaient, ce qu'ils transportaient et combien pesait leur chargement.

L'enquête ne sert pas à compter les camions : ses chiffres datent de 2006-2007. Elle sert à savoir ce qu'il y a dedans, en moyenne, sur chaque liaison. Le nombre de camions vient des comptages récents du Volet 1.

```
valeur annuelle d'une liaison = camions par jour (Volet 1) × $ moyen par camion (ENR) × 365
```

### Les étapes

| # | Étape | Ce qu'on fait | En chiffres |
|---|---|---|---|
| 0 | Réseau du Volet 1 | Point de départ : les liaisons et leurs camions par jour. | 307 liaisons |
| 1 | Enquête brute | Toutes les fiches de l'enquête, partout au Canada. Aucune ne contient de montant en dollars. | 114 734 camions |
| 2 | Cibler le Québec | On garde les camions qui chargent ou livrent au Québec, où qu'ils aient été arrêtés, et ceux dont le trajet traverse le Québec. | 37 603 camions, dont 9 057 vides |
| 3 | Postes d'enquête | Les lieux où les camions ont été arrêtés au Québec. | 49 postes |
| 4 | Localiser | L'enquête ne donne qu'un code de municipalité, celui de 2001. Chaque lieu est rattaché au nœud le plus proche du réseau ; hors Québec, à un point frontière. | 100 % des lieux canadiens au code exact |
| 5 | Faire rouler | Chaque camion suit le plus court chemin sur le réseau. Il compte sur toutes les liaisons de son trajet, pas seulement là où on l'a arrêté. | 36 879 camions routés |
| 6 | Donner un prix | Chaque groupe de marchandises reçoit un prix par tonne (Statistique Canada, Cadre de données sur le transport de marchandises). | provisoire : 3 000 $/t |
| 7 | Actualiser | Les prix sont ramenés en dollars de 2026 avec l'indice des prix des produits industriels. | provisoire |
| 8 | Valeur par liaison | $ moyen par camion × camions par jour × 365, avec une fourchette selon la méthode de calcul du trafic. | provisoire |

Les étapes 6 à 8 sont calculées directement dans la page : on peut y modifier les prix et le facteur d'actualisation, et la carte se recalcule. Un onglet « Logique » affiche le code réel de chaque étape.

### Ce que la carte révèle déjà

- **Le routage est à affiner.** Pour seulement 51 % des camions arrêtés à un poste d'enquête, le chemin calculé passe bien par la liaison de ce poste. Aux postes frontaliers comme Lacolle, le plus court chemin sort souvent par une autre frontière voisine. L'enquête indique les postes réellement franchis par chaque camion : s'en servir pour guider le routage est la prochaine amélioration.
- **Le niveau de détail des lieux compte.** L'enquête code ses lieux selon les municipalités de 2001, avant les fusions de 2002. Avec les limites municipales de la même année, chaque lieu canadien retrouve sa municipalité. Si on regroupait plutôt les lieux par division de recensement (en général une MRC), 47 % des ramassages et livraisons au Québec changeraient de nœud. La carte permet de comparer les deux niveaux.
- **Plus d'un camion sur trois roule vide** (37 % une fois pondérés). Ces camions restent dans la moyenne à 0 $, parce que les comptages du Volet 1 les incluent aussi.

### Ce qui reste à faire

- Remplacer les 12 groupes de marchandises de travail par ceux de Statistique Canada, et charger les vrais prix par tonne (tableau 23-10-0142-01).
- Charger l'indice des prix des produits industriels (tableau 18-10-0266-01) pour l'actualisation.
- Guider le routage par les postes franchis.
- Donner une fourchette basse, centrale et haute pour chaque liaison.
- Valider le résultat par une seconde méthode, indépendante de l'enquête : répartir sur le réseau les échanges en dollars publiés par Statistique Canada entre grandes régions.

### Données et confidentialité

Les microdonnées de l'ENR sont utilisées sous entente et ne sont jamais versées dans ce dépôt. Le script les lit dans un dossier externe (variable `ENR_DIR`). La carte publique n'en montre que des chiffres globaux et des résultats agrégés par liaison et par poste. Les lignes origine-destination et les lieux ne sont visibles que dans la version de travail, qui reste locale.

### Reproduire

```bash
ENR_DIR=/chemin/vers/les/fichiers/ENR python scripts/visualisation_volet2.py --publique
```

Le script [`visualisation_volet2.py`](../scripts/visualisation_volet2.py) prépare toutes les couches de la carte (environ 20 secondes) et les injecte dans le gabarit [`scripts/gabarits/visualisation_volet2.html`](../scripts/gabarits/visualisation_volet2.html). Avec `--publique`, il écrit aussi [`docs/volet2/carte_interactive.html`](volet2/carte_interactive.html), sans les données détaillées.

---

[← Résultats du Volet 1](resultats.md) · [README](../README.md)
