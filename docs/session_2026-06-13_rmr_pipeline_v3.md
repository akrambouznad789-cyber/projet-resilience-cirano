# Session 2026-06-13 — Graphe RMR & Pipeline v3

## Contexte de départ
Suite de la session 2026-06-12 (convention nommage + méthodes DJMA m1–m4).
Les fichiers `graphe_routier_v2_djma_m2/m3/m4.gpkg` étaient déjà produits au démarrage.

---

## Ce qui a été fait

### 1. Comparaison des méthodes DJMA m1–m4

**Script créé :** `scripts/comparer_methodes_djma.py`

**Résultats sur 30 arcs :**

| | m1 | m2 | m3 | m4 |
|---|---|---|---|---|
| Médiane | 24 380 | 26 902 | 26 582 | 28 544 |
| Moyenne | 35 132 | 35 809 | 35 347 | 35 989 |

**Corrélation Pearson ≥ 0.977 entre toutes les méthodes → convergence excellente.**

**7 arcs divergents (écart relatif > 30 %) :**
- Arc 7 : 64 % d'écart (m1=8 715 vs m4=16 830)
- Arc 8 : 50 %, Arc 15 : 47 %, Arc 6 : 45 %, Arc 11 : 41 %, Arc 17 : 37 %, Arc 10 : 34 %

Pattern : arcs avec segments longs à fort DJMA → m2/m4 (pondérés longueur) > m1/m3.

**Validation QGIS :** effectuée, arcs jugés fiables.

---

### 2. Architecture visuelle du pipeline

**Fichier créé :** `docs/architecture_pipeline.md`  
Diagramme Mermaid — s'ouvre avec `Ctrl+Shift+V` dans VSCode.  
Couvre les données brutes, routage v2, DJMA m1-m4, et les étapes futures.

---

### 3. Intégration des zones RMR (Statistique Canada 2021)

**Source :** `lrmr000b21a_f.zip` (téléchargé depuis données publiques StatCan)  
**Extrait dans :** `data/raw/rmr/lrmr000b21a_f.shp`  
**CRS source :** EPSG:3347 → reprojeter en EPSG:32198

**32 zones RMR/AR au Québec (PRIDU='24')**, dont :
- 6 RMR (RMRGENRE=B) : Montréal, Québec, Saguenay, Sherbrooke, Trois-Rivières, Ottawa-Gatineau (part QC)
- 26 AR (RMRGENRE=D/K)
- 3 zones partielles conservées : Campbellton, Hawkesbury, Ottawa-Gatineau

**Champs clés :** `RMRIDU` (ID numérique), `RMRNOM`, `RMRGENRE`, `SUPTERRE` (km²), `PRIDU`

---

### 4. Construction du réseau RMR

**Script créé :** `scripts/construire_reseau_rmr.py`

**Logique Option B — connexions directes :**
Deux RMR sont connectées si le trajet OSRM entre leurs centroïdes ne traverse pas une troisième RMR (intersection > 200m).

**Résultats (496 paires testées, 0 échec OSRM) :**
- **74 arcs directs** retenus sur 496 paires
- 422 filtrés (via tiers), 0 échecs
- Distance médiane : 118 km | min : 27 km | max : 752 km
- Arcs RMR↔RMR : 13 | RMR↔AR : 33 | AR↔AR : 28

**Fichier produit :** `data/raw/reseau_rmr.gpkg`  
3 couches :
- `arcs` : 74 arcs (ID_ARC, ID_A, VILLE_A, ID_B, VILLE_B, DIST_KM, SOURCE, TYPE_A, TYPE_B, geometry=tracé OSRM)
- `noeuds` : 32 centroïdes (ID=RMR{RMRIDU}, NOM, TYPE, NB_ARCS, CAS, DETAIL)
- `rmr_zones` : 32 polygones RMR (RMRIDU, NOM, RMRGENRE, SUPTERRE)

**Structure compatible avec `reseau_arcs.gpkg`** → le code aval fonctionne sans modification.

**Exemples de détections correctes :**
- Montréal → Québec : ↷ via Trois-Rivières ✓
- Montréal → Sherbrooke : ↷ via Granby ✓
- Québec → Trois-Rivières : ✓ direct
- Québec → Saguenay : ✓ direct

---

### 5. Algorithme de routage v3

**Script créé :** `scripts/algo_graphe_reseau_v3.py`

**Différence principale vs v2 : CLIPPING RMR**
- Après récupération du tracé OSRM, on coupe la portion à l'intérieur des polygones RMR A et B
- `trace_interurbain = trace_lambert.difference(union(zone_a, zone_b))`
- Tous les filtres RTSS/DJMA (distance, direction, proximité nœud) s'appliquent sur ce corridor interurbain
- Résultat : DJMA exempt de trafic local intra-urbain

**Nouveauté :** champ `longueur_interurbain_km` (longueur du corridor hors RMR)

**Aucun appel OSRM nécessaire** : les tracés sont déjà stockés dans `reseau_rmr.gpkg`.

**Test sur 10 arcs (SAMPLE_N_ARCS=10) — 10/10 OK :**
- Réduction moyenne : **28.3% du tracé exclu** (portion intra-RMR)
- Qualité DJMA médiane : 54% (normal pour l'Est-du-Québec, peu de stations)
- 160 segments DJMA exportés

**Fichier produit :** `data/processed/graphe_routier_v3_sample.gpkg`  
4 couches :
- `arcs_enrichis_v3`
- `trajets_segments_v3`
- `trace_osrm_v3` (tracé complet A→B)
- `trace_interurbain_v3` (corridor clipé — pour validation QGIS)

---

### 6. Règle de versionnement établie

**Ne jamais modifier un script existant.** Toujours créer un nouveau fichier avec un nom incrémenté :
- `algo_graphe_reseau_v2.py` → modifications → `algo_graphe_reseau_v3.py`
- Ancienne version conservée telle quelle

---

## État du pipeline à la fin de session

```
# FIGÉS — ne plus modifier
scripts/algo_graphe_reseau_v2.py      → data/processed/graphe_routier_v2_sample.gpkg    ✓
scripts/calcul_djma_m1.py             → data/processed/graphe_routier_v2_djma_m1.gpkg   ✓
scripts/calcul_djma_m2.py             → data/processed/graphe_routier_v2_djma_m2.gpkg   ✓
scripts/calcul_djma_m3.py             → data/processed/graphe_routier_v2_djma_m3.gpkg   ✓
scripts/calcul_djma_m4.py             → data/processed/graphe_routier_v2_djma_m4.gpkg   ✓

# RÉSEAU RMR
scripts/construire_reseau_rmr.py      → data/raw/reseau_rmr.gpkg                        ✓ (74 arcs, 32 nœuds)

# V3 — EN COURS DE VALIDATION
scripts/algo_graphe_reseau_v3.py      → data/processed/graphe_routier_v3_sample.gpkg    ✓ (10 arcs test)
  SAMPLE_N_ARCS = 10  ← à passer à None pour les 74 arcs complets
```

---

## Fichiers non commités (branche feature/algo-routage-v2)

```
scripts/comparer_methodes_djma.py
scripts/construire_reseau_rmr.py
scripts/algo_graphe_reseau_v3.py
docs/architecture_pipeline.md
docs/session_2026-06-13_rmr_pipeline_v3.md
data/raw/rmr/                         (shapefile StatCan)
data/raw/reseau_rmr.gpkg
data/processed/graphe_routier_v3_sample.gpkg
```

---

## Prochaine session — Plan

### Étape immédiate : Validation QGIS v3
```bash
qgis data/processed/graphe_routier_v3_sample.gpkg data/raw/reseau_rmr.gpkg &
```
Charger et vérifier visuellement :
1. `rmr_zones` — polygones RMR bien positionnés
2. `noeuds` — centroïdes dans les bonnes zones
3. `trace_osrm_v3` vs `trace_interurbain_v3` — clipping cohérent
4. `trajets_segments_v3` — segments DJMA bien sur le corridor interurbain

Si OK → `SAMPLE_N_ARCS = None` dans `algo_graphe_reseau_v3.py` et relancer pour les 74 arcs.

### Étape suivante : Imputation ML des données manquantes

**Contexte :** qualité DJMA médiane = 54% sur les arcs testés (insuffisant pour l'analyse de résilience).

**Plan discuté :**

| Type de manque | Approche |
|---|---|
| Segment avec historique partiel (années récentes manquantes) | Régression linéaire de tendance par segment + extrapolation |
| Segment sans aucun comptage | KNN spatial (voisins de même type de route) |

**Features pour Random Forest (phase avancée) :**
- Type de route (Autoroute, Nationale, Régionale…)
- Longueur du segment
- Coordonnées centroïde (X, Y)
- Année
- DJMA des années précédentes du même segment
- DJMA moyen des voisins à <500m, <1km, <5km

**Cible :** opérer au niveau des segments RTSS individuels (pas des arcs agrégés).

### Phase finale : Analyse de résilience
- Passage à l'échelle Québec (`SAMPLE_N_ARCS = None`)
- Indicateurs OD inter-RMR
- Simulation de perturbations (fermeture d'arcs)
