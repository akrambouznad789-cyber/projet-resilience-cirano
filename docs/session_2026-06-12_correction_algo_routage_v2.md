# Session 2026-06-12 — Diagnostic visuel QGIS + Correction algo routage v2

## Objectifs de la session
Valider visuellement le résultat du pipeline v1 dans QGIS, diagnostiquer les problèmes
d'affectation des segments DJMA aux arcs, et produire un algorithme v2 corrigé.

---

## Setup QGIS sur Ubuntu/WSL2

- QGIS installé sur Ubuntu 26.04 via `sudo apt-get install -y qgis qgis-plugin-grass`
- WSLg actif (`$DISPLAY=:0`) → interface graphique directe sans configuration supplémentaire
- CRS des couches `arcs` et `noeuds` (reseau_arcs.gpkg) : **EPSG:32198** (NAD83 / Quebec Lambert)
  → à assigner manuellement dans QGIS (Properties → Source → Assigned CRS) car absent du fichier source
- Fond de carte OSM : Browser QGIS → XYZ Tiles → OpenStreetMap (double-clic)
- Projet QGIS sauvegardé dans : `qgis/validation_v1.qgz`

---

## Problèmes identifiés dans l'algo v1

Observation visuelle sur l'arc Saint-Hyacinthe → Granby (exemple représentatif) :

| Élément | Couleur QGIS | Observation |
|---|---|---|
| Lien noeud-à-noeud | Noir | Ligne droite abstraite A→B |
| Tracé OSRM | Bleu | Très précis, suit la route réelle |
| Segments retenus v1 | Rouge (trajets_segments) | Seulement 2 segments sur ~15 disponibles |
| Données disponibles | Rouge vif (circulation_routier) | Comptages disponibles sur ~95% du trajet |

**Bug 1 — Sous-capture (critique)** : Le clip rectangulaire orienté A→B éliminait les
segments sur les portions de route qui s'écartent de l'axe direct. Des sections entières
avec comptages disponibles étaient ignorées → biais fort sur djma_1.

**Bug 2 — Faux positif** : Un segment près de Granby était retenu alors qu'il ne suit
pas le tracé OSRM (route adjacente entrant dans le rectangle par accident géométrique).

---

## Méthode v2 — Trois filtres séquentiels

### Filtre 1 — Distance au tracé OSRM (remplace le corridor rectangulaire)
- Zone de candidats : buffer 1500m autour du tracé OSRM (suit la forme réelle)
- Critère de rétention : `distance(centroïde_segment, tracé_osrm) < 400m`
- Rationale : les référentiels MTQ et OSRM peuvent diverger de plusieurs centaines
  de mètres hors zones urbaines ; mesurer la distance à la ligne plutôt qu'au polygone
  rectangulaire évite les exclusions arbitraires.

### Filtre 2 — Alignement directionnel (élimine les faux positifs)
- Calcul : angle du segment DJMA vs angle local du tracé OSRM au point le plus proche
- Seuil : différence angulaire < 45°
- Rationale : un segment géographiquement proche mais d'orientation divergente est sur
  une autre route (parallèle adjacente ou perpendiculaire). Ce filtre corrige le Bug 2.

### Filtre 3 — Proximité aux noeuds (conservé de v1)
- Exclut les segments dont le centroïde est plus proche d'un noeud tiers que de A ou B
- Désactivé si dist(A,B) < 2 × 2000m (arcs courts, évite de tout exclure)

---

## Fichiers produits

| Fichier | Description |
|---|---|
| `scripts/algo_graphe_reseau_v2.py` | Algorithme de routage v2 |
| `data/processed/graphe_routier_v2_sample.gpkg` | Résultat v2 (à produire au prochain run) |

**Paramètres v2 :**
```
BUFFER_RECHERCHE_M = 1500
DIST_MAX_TRACE_M   = 400
ANGLE_MAX_DEG      = 45
BUFFER_EXCLUSION_M = 2000
SAMPLE_N_ARCS      = 30
```

---

## Prochaine session

1. **Lancer** `python3 scripts/algo_graphe_reseau_v2.py` (sample 30 arcs)
2. **Charger** `graphe_routier_v2_sample.gpkg` dans QGIS et comparer visuellement avec v1
   → vérifier que le nombre de segments retenus par arc augmente significativement
   → vérifier absence de faux positifs sur l'arc Granby
3. **Ajuster** les seuils si nécessaire (DIST_MAX_TRACE_M, ANGLE_MAX_DEG)
4. **Lancer** `python3 scripts/calcul_djma_v1.py` sur le résultat v2 (le script DJMA est inchangé,
   pointer OUTPUT_FILE vers graphe_routier_v2_sample.gpkg)
5. Comparer les distributions djma_1 entre v1 et v2

## Pipeline à ce stade

```
python3 scripts/algo_graphe_reseau_v2.py   →  graphe_routier_v2_sample.gpkg
python3 scripts/calcul_djma_v1.py          →  graphe_routier_v2_djma.gpkg   (adapter le chemin)
```
