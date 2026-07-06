# Session 2026-06-12 (partie 2) — Convention de nommage & méthodes DJMA m1–m4

## Contexte de départ
Suite directe de la session du matin (correction algo routage v2).  
`graphe_routier_v2_sample.gpkg` était produit et validé visuellement dans QGIS.  
Objectif de cette partie : structurer le calcul du DJMA par méthodes distinctes.

---

## Correction de bug appliquée

**Fichier :** `scripts/algo_graphe_reseau_v2.py` — fonction `angle_linestring()` (ligne ~171)

**Bug :** la fonction appelait `.coords` sur des géométries `MultiLineString`, ce qui levait un `NotImplementedError`. Certains segments DJMA de la couche `circulation_routier` ont une géométrie multi-parties.

**Fix :** si la géométrie est un `MultiLineString`, on extrait la sous-géométrie la plus longue avant d'appeler `.coords`.

```python
if geom.geom_type == "MultiLineString":
    geom = max(geom.geoms, key=lambda g: g.length)
```

---

## Convention de nommage établie

Deux axes **indépendants** dorénavant :

| Axe | Notation | Valeurs |
|---|---|---|
| Routage (association segments → arcs) | `v{N}` | `v2` — figé, ne plus modifier |
| Méthode d'agrégation DJMA | `m{N}` | `m1` à `m4` (et futures) |

**Format des fichiers de sortie :** `graphe_routier_v{routage}_djma_m{méthode}.gpkg`

**Pourquoi `calcul_djma_v1.py` existe encore :**  
Ce script a été créé lors de la session du 2026-06-11, avant la convention de nommage. Son « v1 » désignait à la fois le premier script de calcul ET le fait qu'il travaillait sur le routage v1. Il est conservé comme référence historique mais **ne plus utiliser** — supersédé par `calcul_djma_m1.py`.

---

## Scripts créés

### `scripts/calcul_djma_m1.py` — Moyenne simple (routage v2)
- **Entrée :** `graphe_routier_v2_sample.gpkg` → `arcs_enrichis_v2`
- **Méthode :** `mean(djma_val)` sur tous les segments non-NA
- **Sortie :** `graphe_routier_v2_djma_m1.gpkg` → `arcs_enrichis_v2_djma_m1`
- **Résultat produit :** 29/30 arcs avec djma_m1, médiane 24 380 véh./jour

### `scripts/calcul_djma_m2.py` — Pondération par longueur
- **Entrée :** `graphe_routier_v2_sample.gpkg` → `arcs_enrichis_v2` + `trajets_segments_v2`
- **Méthode :** `Σ(djma_val × longueur_m) / Σ(longueur_m)`  
  La longueur est calculée depuis `geometry.length` de `trajets_segments_v2` (EPSG:32198 → mètres)
- **Sortie :** `graphe_routier_v2_djma_m2.gpkg` → `arcs_enrichis_v2_djma_m2`
- **Statut :** script écrit, **à exécuter**

### `scripts/calcul_djma_m3.py` — Pondération par type d'axe (hiérarchie MTQ)
- **Entrée :** `graphe_routier_v2_sample.gpkg` → `arcs_enrichis_v2`
- **Méthode :** `Σ(djma_val × poids_type) / Σ(poids_type)`  
  Types lus depuis `ids_segs_rtss_type` (encodé dans `arcs_enrichis_v2`, aligné avec `ids_segs_djma_val`) — pas de join spatial nécessaire.
- **Poids :** Autoroute=4, Nationale=3, Régionale=2, Collectrice=2, reste=1
- **Sortie :** `graphe_routier_v2_djma_m3.gpkg` → `arcs_enrichis_v2_djma_m3`
- **Statut :** script écrit, **à exécuter**

### `scripts/calcul_djma_m4.py` — Pondération composite longueur × type
- **Entrée :** `graphe_routier_v2_sample.gpkg` → `arcs_enrichis_v2` + `trajets_segments_v2`
- **Méthode :** `Σ(djma_val × longueur_m × poids_type) / Σ(longueur_m × poids_type)`  
  Longueurs depuis géométrie `trajets_segments_v2`. Types via table `{(ID_ARC, ide_sectn_trafc) → type}` construite depuis `ids_segs_rtss_type` de `arcs_enrichis_v2`.
- **Sortie :** `graphe_routier_v2_djma_m4.gpkg` → `arcs_enrichis_v2_djma_m4`
- **Statut :** script écrit, **à exécuter**

---

## Données clés découvertes

- **`ids_segs_rtss_type`** dans `arcs_enrichis_v2` : types d'axe MTQ déjà encodés par arc, alignés positionnellement avec `ids_segs_djma_val`. Exploité dans m3 et m4 sans join spatial.
- **`ids_segs_rtss_dist`** : distances centroïde→tracé OSRM (NON des longueurs de segments).
- **`trajets_segments_v2`** : contient `djma_val` et `cam_val` numériques directs + géométrie réelle → source de longueurs pour m2 et m4.
- **Couche RTSS `des_clasf_`** : 10 classes, les 5 principales = Autoroute, Nationale, Régionale, Collectrice, Sans classe.

---

## État du pipeline à la fin de session

```bash
# Routage — FIGÉ
python3 scripts/algo_graphe_reseau_v2.py   →  graphe_routier_v2_sample.gpkg  ✓

# Méthodes DJMA
python3 scripts/calcul_djma_m1.py          →  graphe_routier_v2_djma_m1.gpkg  ✓ produit
python3 scripts/calcul_djma_m2.py          →  graphe_routier_v2_djma_m2.gpkg  ← À lancer
python3 scripts/calcul_djma_m3.py          →  graphe_routier_v2_djma_m3.gpkg  ← À lancer
python3 scripts/calcul_djma_m4.py          →  graphe_routier_v2_djma_m4.gpkg  ← À lancer
```

**Branche Git active :** `feature/algo-routage-v2`  
**Fichiers non commités :** `calcul_djma_m1.py`, `calcul_djma_m2.py`, `calcul_djma_m3.py`, `calcul_djma_m4.py`, `docs/structure_projet.md`, `docs/session_2026-06-12_methodes_djma_naming.md`

---

## Prochaine session — Plan

1. **Lancer m2, m3, m4** (3 commandes Python, ordre quelconque)
2. **Comparer les distributions** djma_m1 vs m2 vs m3 vs m4 :
   - Si convergence globale → robustesse confirmée
   - Si divergences → identifier les arcs concernés et diagnostiquer (arcs mixtes, type de données manquantes)
3. **Visualiser dans QGIS** : charger les 4 couches `arcs_enrichis_v2_djma_m{1..4}`, comparer la symbologie par DJMA
4. **Décider de la méthode officielle** à utiliser pour le passage à l'échelle Québec
5. **Passage à l'échelle** : `SAMPLE_N_ARCS = None` dans `algo_graphe_reseau_v2.py` → tous les arcs

---

## Référence complète
Pour la structure détaillée du projet et l'explication de chaque script, voir `docs/structure_projet.md`.
