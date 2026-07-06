# Méthodes de calcul du DJMA par arc

Ce fichier documente chaque version du DJMA agrégé calculé à l'échelle des arcs du réseau ville-à-ville.

---

## djma_1 — Moyenne simple des segments sous-jacents

**Date de création :** 2026-06-11
**Script :** `scripts/calcul_djma_v1.py`
**Couche source :** `data/processed/graphe_routier_v1_sample.gpkg` → `arcs_enrichis`
**Couche sortie :** `data/processed/graphe_routier_v1_djma.gpkg` → `arcs_enrichis_djma1`

### Méthode

Pour chaque arc du réseau simplifié ville-à-ville :

1. Lire le champ `ids_segs_djma_val` (format `valeur@année|valeur@année|...`).
2. Retenir uniquement les tokens dont la valeur est numérique (les tokens `NA` sont exclus).
3. Calculer la **moyenne arithmétique simple** des valeurs DJMA retenues → `djma_1` (entier arrondi).
4. Répéter la même opération sur `ids_segs_djma_val_cam` (pourcentage camion) → `pct_cam_1` (1 décimale).
5. Calculer le débit camion estimé : `djma_cam_1 = round(djma_1 × pct_cam_1 / 100)`.
6. Enregistrer `n_segs_djma_1` : nombre de segments ayant contribué au calcul (tokens valides uniquement).

Si aucun segment ne possède de valeur DJMA valide, les champs `djma_1`, `pct_cam_1`, `djma_cam_1` sont `NULL` et `n_segs_djma_1 = 0`.

### Champs produits

| Champ | Type | Description |
|---|---|---|
| `djma_1` | Integer | Débit journalier moyen annuel (véhicules/jour) |
| `pct_cam_1` | Float | Pourcentage de camions moyen (%) |
| `djma_cam_1` | Integer | Débit camion estimé (véhicules/jour) |
| `n_segs_djma_1` | Integer | Nombre de segments contribuant au calcul |
| `statut_djma_pct` | Float | % de segments avec une valeur DJMA (hérité de l'étape d'enrichissement) |

### Hypothèses

- Chaque segment intersectant le corridor de l'arc contribue avec le **même poids**, indépendamment de sa longueur.
- La **valeur retenue par segment** est celle de l'**année la plus récente** disponible dans les colonnes annuelles de `DebitCirculation.gpkg` (colonnes `val_djma_annee_1` à `val_djma_annee_10`, parcourues dans l'ordre).
- Les pourcentages camion (`pct_cam_1`) sont calculés sur les **segments disposant d'une valeur camion**, qui peuvent être un sous-ensemble des segments contribuant à `djma_1`.
- L'arc est supposé représentatif de l'ensemble des segments qu'il couvre : pas de correction géographique ni de pondération par densité de trafic.

### Limites connues

- **Absence de pondération par longueur** : un segment court pèse autant qu'un long. Une méthode `djma_2` pondérée est prévue.
- **Hétérogénéité temporelle** : les années des valeurs retenues peuvent varier d'un segment à l'autre au sein d'un même arc.
- **Couverture partielle** : si `statut_djma_pct < 100`, une partie des segments ne dispose d'aucune donnée de comptage ; la moyenne ne porte que sur les segments disponibles.
- **Corridors larges** : le buffer de 500 m autour du tracé OSRM peut capturer des segments adjacents non pertinents (routes parallèles). Ce biais n'est pas corrigé dans `djma_1`.

---

## djma_2 — (à venir)

Pondération des valeurs DJMA par la longueur de chaque segment intersectant le corridor.

---
