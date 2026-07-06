# Session 2026-06-22 — Complétion des débits & corrections algo routage v3

## Travaux réalisés

### 1. Complétion des données de débit (Phase 1 pipeline)

**Problème :** 3 247 segments sans aucune valeur DJMA, %cam très lacunaire (132 complets sur 10 ans).

**Scripts créés :**

`scripts/completer_debits_v1.py` → `data/processed/debits_completes_v1.gpkg`
- Phase 1 : interpolation linéaire (trous internes) + régression linéaire (extrémités)
- MICE (IterativeImputer + RandomForestRegressor) : segments avec DJMA mais 0 %cam
- Phase 2 KNN+IDW : segments sans aucune valeur DJMA (k=5 voisins, priorité même index_agreg)

`scripts/completer_debits_v2.py` → `data/processed/debits_completes_v2.gpkg`
- Identique à v1 + reconstruction des champs texte `annee_en_cours` à `annee10`
- Permet validation visuelle directe dans QGIS (DJMA et %cam injectés dans les chaînes)
- DJME, DJMH et 30e h conservés de la source originale

**Résultats :**
| Méthode DJMA | Segments | Méthode %cam | Segments |
|---|---|---|---|
| Complet (déjà OK) | 2 716 | Complet | 132 |
| Interpolation | 736 | Interpolation | 622 |
| Extrapolation | 1 124 | Extrapolation | 2 912 |
| KNN géographique | 3 247 | MICE (RandomForest) | 910 |
| | | KNN géographique | 3 247 |

**0 valeur manquante** sur 7 823 segments après complétion.

---

### 2. Corrections algo routage v3 (`scripts/algo_graphe_reseau_v3.py`)

**Correction 1 — Filtre 1 : distance segment complet → tracé (pas du centroïde)**
```python
# AVANT (sous-capture : segments longs décalés rejetés)
distances = segs.geometry.centroid.distance(trace)
# APRÈS (point le plus proche de toute la géométrie)
distances = segs.geometry.distance(trace)
```

**Correction 2 — Filtre 4 (nouveau) : exclusion zone intraurbaine A et B**
```python
BUFFER_NOEUDS_AB_M = 3000  # segments à < 3km du centroïde A ou B → exclus
# Exception : si dist(A,B) < 2 × 3000m → pas d'exclusion (arc court)
```
Règle le problème de sur-capture des flux intraurbains près des villes d'origine/destination.

**Correction 3 — Source données**
- `PATH_DJMA` → `debits_completes_v2.gpkg` (layer `debits_completes_v2`)
- `charger_djma()` simplifiée : lit directement `val_djma_annee_1` (toujours renseigné)

**Résultats sample 10 arcs RMR :**
- 10/10 arcs OK, 0 échec
- Qualité DJMA : 100% sur tous les arcs
- 28.3% du tracé exclu (clipping RMR + filtre AB = trafic local éliminé)
- 227 segments DJMA exportés

---

## Pipeline actif (état 2026-06-22)

```
data/raw/DebitCirculation.gpkg
       ↓ completer_debits_v2.py
data/processed/debits_completes_v2.gpkg          ✅ COMPLET
       ↓ algo_graphe_reseau_v3.py (SAMPLE_N_ARCS=10)
data/processed/graphe_routier_v3_sample.gpkg     ⚠️ 10/74 arcs seulement
       ↓ calcul_djma_m1_v3.py (à créer)
data/processed/graphe_routier_v3_djma_m1.gpkg    ❌ pas encore
```

---

## Prochaine session — Point de départ

**→ Valider les 10 arcs sample de `graphe_routier_v3_sample.gpkg` dans QGIS**

Couches à charger dans QGIS :
- `graphe_routier_v3_sample.gpkg` → arcs_enrichis_v3, trajets_segments_v3, trace_osrm_v3, trace_interurbain_v3
- `debits_completes_v2.gpkg` → debits_completes_v2 (référence)
- Garder : noeuds, circulation_routier (données brutes)

Questions à valider :
1. Les segments `trajets_segments_v3` sont-ils bien sur les routes interurbaines ?
2. Pas de sur-capture intraurbaine près des villes A et B ?
3. Pas de sous-capture évidente (segments manquants sur la route) ?

Si validation OK → `SAMPLE_N_ARCS = None` → relancer pour les 74 arcs complets → créer `calcul_djma_m1_v3.py`.
