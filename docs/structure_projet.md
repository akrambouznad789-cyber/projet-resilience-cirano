# Structure du projet — Référence complète
**Projet :** Résilience du réseau routier québécois (CIRANO II)  
**Chercheur :** Akram Bouznad — Polytechnique Montréal / CIRANO / MTQ  
**Dernière mise à jour :** 2026-06-12

---

## 1. Vue d'ensemble

Ce projet construit un **graphe routier simplifié ville-à-ville** pour le Québec, enrichi de données de débit de circulation (DJMA). L'objectif final est d'évaluer la résilience du réseau face à des perturbations.

Le pipeline est structuré en **deux étapes indépendantes** :

```
Étape A — Routage          : algo_graphe_reseau_v2.py
                                  ↓
           graphe_routier_v2_sample.gpkg
                                  ↓
Étape B — Calcul DJMA      : calcul_djma_m{1..4}.py
                                  ↓
           graphe_routier_v2_djma_m{1..4}.gpkg
```

---

## 2. Convention de nommage (établie le 2026-06-12)

Deux axes de versionnement **indépendants** :

| Axe | Notation | Signification |
|---|---|---|
| **Routage** — comment on associe les segments DJMA aux arcs | `v{N}` | `v1` = buggy (retraité), `v2` = actif |
| **Méthode DJMA** — comment on agrège le débit par arc | `m{N}` | `m1` à `m4` |

Les fichiers processés portent les deux indices : `graphe_routier_v2_djma_m1.gpkg` = routage v2 + méthode m1.

---

## 3. Données brutes (`data/raw/`) — lecture seule

| Fichier | Couche | Contenu | CRS |
|---|---|---|---|
| `reseau_arcs.gpkg` | `arcs`, `noeuds` | Graphe ville-à-ville simplifié MTQ (~1600 arcs) | EPSG:32198 |
| `ReseauRoutier_RTSS.gpkg` | `bgr_v_sous_route_res_sup_act` | Référentiel routier MTQ avec classification (`cod_clasf_`, `des_clasf_`) | EPSG:32198 |
| `DebitCirculation.gpkg` | `circulation_routier` | Comptages de débit DJMA par section de trafic (DJMA, DJME, DJMH, % camion, annuels) | EPSG:32198 |

**Champ clé dans ReseauRoutier_RTSS — `des_clasf_` :**

| Valeur | Code | Signification |
|---|---|---|
| Autoroute | 10 | Voies rapides à accès limité |
| Nationale | 20 | Routes nationales numérotées |
| Régionale | 30 | Routes régionales |
| Collectrice | 40 | Routes collectrices |
| Accès aux ressources | 60/70 | Desservant zones éloignées |
| Sans classe / Local | 00/51/52/53 | Voies non classifiées |

---

## 4. Scripts (`scripts/`)

### 4.1 `algo_graphe_reseau_v1.py` — Routage v1 (ARCHIVÉ, ne pas utiliser)

**Statut :** buggy, conservé uniquement comme référence.  
**Problèmes identifiés visuellement dans QGIS (2026-06-12) :**
- **Sous-capture** : corridor rectangulaire orienté A→B excluait les segments sur les portions de route qui s'écartent de l'axe direct (virages, contournements). Exemple : arc Saint-Hyacinthe→Granby, seulement 2 segments capturés sur ~15 disponibles.
- **Faux positifs** : des routes adjacentes entraient dans le rectangle par accident géométrique.

**Sortie produite :** `graphe_routier_v1_sample.gpkg`

---

### 4.2 `algo_graphe_reseau_v2.py` — Routage v2 (ACTIF, figé)

**Statut :** validé visuellement dans QGIS. **Ne pas modifier.**  
**Entrées :** `reseau_arcs.gpkg` + `ReseauRoutier_RTSS.gpkg` + `DebitCirculation.gpkg`  
**Sortie :** `graphe_routier_v2_sample.gpkg` (30 arcs en mode sample)

**Méthode — 3 filtres séquentiels :**

1. **Filtre distance au tracé OSRM** (remplace le corridor rectangulaire)  
   Buffer 1500 m autour du tracé OSRM comme zone de candidats.  
   Seuil de rétention : distance centroïde → tracé < 400 m.  
   *Rationale :* les référentiels MTQ et OSRM divergent souvent de centaines de mètres hors zones urbaines ; le filtre suit la forme réelle de la route.

2. **Filtre directionnel** (élimine les faux positifs)  
   Comparaison de l'angle du segment DJMA vs l'angle local du tracé OSRM au point le plus proche.  
   Seuil : différence angulaire < 45°.  
   *Rationale :* un segment géographiquement proche mais d'orientation divergente est sur une autre route.  
   *Bug corrigé en session (2026-06-12) :* `angle_linestring()` échouait sur les `MultiLineString` — la sous-géométrie la plus longue est maintenant utilisée.

3. **Filtre proximité aux noeuds** (conservé de v1)  
   Exclut les segments plus proches d'un noeud tiers que de A ou B.  
   Désactivé pour les arcs courts (dist A-B < 2 × 2000 m).

**Couches produites dans `graphe_routier_v2_sample.gpkg` :**

| Couche | Contenu |
|---|---|
| `arcs_enrichis_v2` | Arcs avec métadonnées + champs encodés (DJMA, types RTSS, distances) |
| `trajets_segments_v2` | Un enregistrement par segment DJMA retenu (avec géométrie réelle) |
| `trace_osrm_v2` | Tracé OSRM brut par arc (pour validation visuelle) |

**Champs clés de `arcs_enrichis_v2` :**

| Champ | Format | Contenu |
|---|---|---|
| `ids_segs_djma_val` | `val@année\|NA\|val@année\|...` | Valeur DJMA de chaque segment RTSS associé (NA si absent) |
| `ids_segs_djma_val_cam` | même format | % camion de chaque segment |
| `ids_segs_rtss_type` | `Autoroute\|Nationale\|...` | Type d'axe MTQ de chaque segment RTSS (aligné avec `ids_segs_djma_val`) |
| `ids_segs_rtss_dist` | `615.3\|829.7\|...` | Distance centroïde→tracé OSRM de chaque segment (mètres) |
| `ids_segs_djma` | `29121\|23067\|...` | IDs `ide_sectn_trafc` des segments retenus |

> **Note importante :** `ids_segs_rtss_type` et `ids_segs_djma_val` sont **positionnellement alignés** : la position N de l'un correspond au même segment que la position N de l'autre. Les NA dans `ids_segs_djma_val` correspondent à des segments RTSS sans comptage disponible.

---

### 4.3 `calcul_djma_v1.py` — Calcul DJMA v1 (LEGACY, ne plus utiliser)

**Pourquoi ce fichier existe :**  
Créé lors de la session du 2026-06-11, **avant** l'établissement de la convention de nommage. À l'époque, « v1 » désignait à la fois le premier script de calcul DJMA ET le fait qu'il travaillait sur la sortie du routage v1 (`graphe_routier_v1_sample.gpkg`). Ce double sens rendait le nommage ambigu.

**Il est supersédé par `calcul_djma_m1.py`** qui :
- Travaille sur `graphe_routier_v2_sample.gpkg` (routage corrigé)
- Suit la nouvelle convention (`m{N}` pour la méthode)

**Conserver** ce fichier comme trace de l'historique du projet, mais ne pas l'utiliser pour de nouveaux calculs.

---

### 4.4 `calcul_djma_m1.py` — Méthode m1 : Moyenne simple

**Entrée :** `graphe_routier_v2_sample.gpkg` → couche `arcs_enrichis_v2`  
**Sortie :** `graphe_routier_v2_djma_m1.gpkg` → couche `arcs_enrichis_v2_djma_m1`

**Formule :**
```
djma_m1 = mean(djma_val)  pour tous les segments non-NA
```

**Champs produits :** `djma_m1`, `pct_cam_m1`, `djma_cam_m1`, `n_segs_m1`

**Hypothèses :**
- Chaque segment contribue avec le **même poids**, indépendamment de sa longueur ou de son importance routière.
- Référence de base : si m1 ≈ m2 ≈ m3 ≈ m4, cela valide la robustesse du résultat.

**Limite principale :** un segment de 50 m pèse autant qu'un segment de 5 km.

---

### 4.5 `calcul_djma_m2.py` — Méthode m2 : Pondération par longueur

**Entrée :** `graphe_routier_v2_sample.gpkg` → couches `arcs_enrichis_v2` + `trajets_segments_v2`  
**Sortie :** `graphe_routier_v2_djma_m2.gpkg` → couche `arcs_enrichis_v2_djma_m2`

**Formule :**
```
poids_i      = longueur géométrique du segment i  (mètres, CRS EPSG:32198)
djma_m2      = Σ(djma_val_i × poids_i) / Σ(poids_i)
```

**Champs produits :** `djma_m2`, `pct_cam_m2`, `djma_cam_m2`, `n_segs_m2`, `longueur_segs_m`

**Hypothèses :**
- La longueur géométrique du segment dans `trajets_segments_v2` est un proxy valide du kilométrage qu'il représente sur l'arc.
- Les sections longues (ex. tronçon autoroutier de 10 km) sont plus représentatives du débit de l'arc que les bretelles courtes.

**Attente :** diverge de m1 surtout sur les arcs mixtes (portion autoroute + bretelles courtes).

---

### 4.6 `calcul_djma_m3.py` — Méthode m3 : Pondération par type d'axe

**Entrée :** `graphe_routier_v2_sample.gpkg` → couche `arcs_enrichis_v2`  
**Sortie :** `graphe_routier_v2_djma_m3.gpkg` → couche `arcs_enrichis_v2_djma_m3`

**Formule :**
```
poids_i      = POIDS_TYPE[ids_segs_rtss_type_i]
djma_m3      = Σ(djma_val_i × poids_i) / Σ(poids_i)
```

**Table des poids :**

| Type d'axe | Poids | Rationale |
|---|---|---|
| Autoroute | 4 | Comptages les plus fiables, trafic homogène sur de longues distances |
| Nationale | 3 | Routes numérotées à fort débit, comptages réguliers |
| Régionale | 2 | Débit intermédiaire, couverture de comptage bonne |
| Collectrice | 2 | Idem régionale |
| Accès aux ressources | 1 | Débit faible, comptages parfois anciens |
| Accès aux ressources et aux localités isolées | 1 | Idem |
| Sans classe / Local 1/2/3 | 1 | Données rares ou peu fiables |

**Implémentation :** les types sont lus depuis `ids_segs_rtss_type` (déjà encodé dans `arcs_enrichis_v2`), parsé en parallèle avec `ids_segs_djma_val` — aucun join spatial nécessaire.

**Attente :** sur un arc mixte autoroute/bretelles, m3 sera proche de la valeur de l'autoroute.

---

### 4.7 `calcul_djma_m4.py` — Méthode m4 : Pondération composite longueur × type

**Entrée :** `graphe_routier_v2_sample.gpkg` → couches `arcs_enrichis_v2` + `trajets_segments_v2`  
**Sortie :** `graphe_routier_v2_djma_m4.gpkg` → couche `arcs_enrichis_v2_djma_m4`

**Formule :**
```
poids_composite_i = longueur_m_i × poids_type_i
djma_m4           = Σ(djma_val_i × poids_composite_i) / Σ(poids_composite_i)
```

**Implémentation :** le script charge `trajets_segments_v2` pour les longueurs géométriques, puis reconstruit la table `{(ID_ARC, ide_sectn_trafc) → type_axe}` depuis `ids_segs_rtss_type` encodé dans `arcs_enrichis_v2`, sans join spatial sur le RTSS brut.

**Attente :** méthode la plus physiquement correcte — un long tronçon autoroutier domine largement. Cas limite intéressant : si m4 ≫ m1, l'arc a une majorité de bretelles courtes + peu de long tronçon autoroutier.

---

## 5. Fichiers processés (`data/processed/`)

| Fichier | Statut | Couche(s) | Notes |
|---|---|---|---|
| `graphe_routier_v1_sample.gpkg` | **Archivé** | `arcs_enrichis`, `trajets_segments`, `trace_osrm` | Sortie du routage buggy v1 |
| `graphe_routier_v1_djma.gpkg` | **Archivé** | `arcs_enrichis_djma1` | DJMA calculé sur routage v1 — valeurs biaisées (sous-capture) |
| `graphe_routier_v2_sample.gpkg` | **ACTIF — base de tous les calculs DJMA** | `arcs_enrichis_v2`, `trajets_segments_v2`, `trace_osrm_v2` | 30 arcs, routage v2 validé QGIS |
| `graphe_routier_v2_djma_m1.gpkg` | Produit | `arcs_enrichis_v2_djma_m1` | Moyenne simple — 29/30 arcs avec DJMA |
| `graphe_routier_v2_djma_m2.gpkg` | À produire | `arcs_enrichis_v2_djma_m2` | `python3 scripts/calcul_djma_m2.py` |
| `graphe_routier_v2_djma_m3.gpkg` | À produire | `arcs_enrichis_v2_djma_m3` | `python3 scripts/calcul_djma_m3.py` |
| `graphe_routier_v2_djma_m4.gpkg` | À produire | `arcs_enrichis_v2_djma_m4` | `python3 scripts/calcul_djma_m4.py` |

---

## 6. Projet QGIS (`qgis/`)

| Fichier | Contenu |
|---|---|
| `reseau-routier-graphe.qgz` | Projet de validation — contient v1 et v2, fond OSM, groupes de couches organisés |

**Pour ouvrir :**
```bash
qgis ~/projects/projet-resilience-cirano/qgis/reseau-routier-graphe.qgz &
```

**CRS à assigner manuellement** pour `arcs` et `noeuds` de `reseau_arcs.gpkg` : **EPSG:32198** (NAD83 / Quebec Lambert) — absent du fichier source.

---

## 7. Pipeline actif

```bash
# Étape A — Routage (déjà exécuté, ne pas relancer sauf changement d'algo)
python3 scripts/algo_graphe_reseau_v2.py
# → data/processed/graphe_routier_v2_sample.gpkg

# Étape B — Calcul DJMA (une commande par méthode, indépendantes)
python3 scripts/calcul_djma_m1.py   # → graphe_routier_v2_djma_m1.gpkg  ✓ produit
python3 scripts/calcul_djma_m2.py   # → graphe_routier_v2_djma_m2.gpkg
python3 scripts/calcul_djma_m3.py   # → graphe_routier_v2_djma_m3.gpkg
python3 scripts/calcul_djma_m4.py   # → graphe_routier_v2_djma_m4.gpkg
```

---

## 8. Prochaines étapes identifiées

1. **Lancer m2, m3, m4** et comparer les distributions DJMA → voir la convergence entre méthodes.
2. **Identifier les arcs divergents** (m1 ≠ m4 significativement) → diagnostiquer les causes (arcs mixtes, données manquantes, géométries complexes).
3. **Passage à l'échelle Québec** : retirer `SAMPLE_N_ARCS = 30` dans `algo_graphe_reseau_v2.py` → traitement de tous les arcs.
4. **Méthode djma_2 pour le RTSS** : pondération par longueur de sous-route (champ `val_longr_` du RTSS brut) — distincte de m2 qui utilise la longueur géométrique des segments de comptage.
