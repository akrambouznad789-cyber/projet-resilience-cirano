"""
calcul_djma_m1_c1.py
====================
Projet CIRANO II — Complétion des valeurs DJMA manquantes (méthode c1)

MÉTHODE c1 — Complétion intra-arc par moyenne locale
------------------------------------------------------
Pour chaque arc, les segments NA dans `ids_segs_djma_val` sont remplacés par
la moyenne des segments disponibles de CE MÊME arc (complétion intra-arc).
La logique : les segments NA d'un arc sont sur la même route → leur DJMA est
approximativement celui mesuré sur les autres segments de cet arc.

Fallback global uniquement pour les arcs sans aucun segment valide :
  - arcs sans segments (statut aucun_djma)
  - arcs dont tous les segments ont NA

S'applique uniquement à djma ; pct_cam est laissé pour une session ultérieure.

ENTRÉE
------
  data/processed/graphe_routier_v2_djma_m1.gpkg  (layer : arcs_enrichis_v2_djma_m1)

SORTIE
------
  data/processed/graphe_routier_v2_djma_m1_c1.gpkg  (layer : arcs_enrichis_v2_djma_m1_c1)

CHAMPS PRODUITS (ajoutés aux colonnes m1 existantes)
-----------------------------------------------------
  djma_m1_c1      : djma complété — même valeur que djma_m1 si tous les
                    segments avaient déjà des données (Int64)
  n_segs_m1_c1    : nombre total de segments après complétion (y compris ceux
                    qui avaient NA et ont reçu la moyenne intra-arc)
  djma_cam_m1_c1  : djma_m1_c1 × pct_cam_m1 / 100 (Int64)
  djma_c1_source  : "m1_original"       tous les segments avaient une valeur
                    "c1_intra_arc"       des NA remplacés par la moyenne de l'arc
                    "c1_moyenne_globale" aucun segment valide → moyenne globale injectée
                    "c1_sans_donnee"     aucune valeur nulle part (cas dégénéré)
"""

import os
import geopandas as gpd
import pandas as pd
import numpy as np
import warnings

warnings.filterwarnings("ignore")

# ============================================================
# CONFIGURATION
# ============================================================

INPUT_FILE   = os.path.expanduser("~/projects/projet-resilience-cirano/data/processed/graphe_routier_v2_djma_m1.gpkg")
INPUT_LAYER  = "arcs_enrichis_v2_djma_m1"
OUTPUT_FILE  = os.path.expanduser("~/projects/projet-resilience-cirano/data/processed/graphe_routier_v2_djma_m1_c1.gpkg")
OUTPUT_LAYER = "arcs_enrichis_v2_djma_m1_c1"


# ============================================================
# PARSING
# ============================================================

def parser_tokens(chaine: str | None) -> list[str]:
    """Retourne la liste brute des tokens (y compris 'NA') d'une chaîne pipe-séparée."""
    if not chaine or pd.isna(chaine):
        return []
    return [t.strip() for t in str(chaine).split("|")]


def extraire_valeurs(tokens: list[str]) -> list[float]:
    """Extrait les valeurs numériques des tokens non-NA."""
    valeurs = []
    for token in tokens:
        if not token or token.upper() == "NA":
            continue
        partie = token.split("@")[0]
        try:
            valeurs.append(float(partie))
        except ValueError:
            continue
    return valeurs


# ============================================================
# COMPLÉTION c1 PAR ARC
# ============================================================

def completer_arc_c1(row: pd.Series, moyenne_globale: float | None) -> tuple:
    """
    Applique la complétion c1 à un arc.

    Retourne (djma_m1_c1, n_segs_m1_c1, source).

    Priorité :
      1. Tous segments ok        → m1_original (pas de changement)
      2. Segments mixtes         → c1_intra_arc (NA remplacés par moyenne de l'arc)
      3. Aucun segment valide    → c1_moyenne_globale (fallback global)
      4. Pas de moyenne globale  → c1_sans_donnee
    """
    tokens = parser_tokens(row.get("ids_segs_djma_val"))
    valeurs_ok = extraire_valeurs(tokens)
    n_total  = len(tokens)
    n_ok     = len(valeurs_ok)
    n_na     = sum(1 for t in tokens if not t or t.upper() == "NA")

    if n_ok == 0:
        # Aucune valeur dans cet arc → fallback global
        if moyenne_globale is not None:
            return round(moyenne_globale), n_total if n_total > 0 else 1, "c1_moyenne_globale"
        return None, 0, "c1_sans_donnee"

    moyenne_arc = float(np.mean(valeurs_ok))

    if n_na == 0:
        # Tous les segments avaient déjà une valeur
        return round(moyenne_arc), n_total, "m1_original"

    # Segments mixtes : NA remplacés par la moyenne de l'arc
    # La moyenne de [v1, moy, v2, moy, ...] = moyenne_arc (invariant mathématique)
    n_segs_c1 = n_total  # tous les segments ont maintenant une valeur
    return round(moyenne_arc), n_segs_c1, "c1_intra_arc"


# ============================================================
# PIPELINE PRINCIPAL
# ============================================================

def main() -> None:
    print("=" * 65)
    print("CALCUL DJMA — méthode m1 + complétion c1 (intra-arc)")
    print("=" * 65)

    print(f"\n[1/3] Chargement {INPUT_LAYER}...")
    gdf = gpd.read_file(INPUT_FILE, layer=INPUT_LAYER)
    print(f"  {len(gdf)} arcs chargés")

    # Calcul de la moyenne globale (fallback pour arcs sans aucune valeur)
    moyenne_globale = None
    if gdf["djma_m1"].notna().any():
        moyenne_globale = round(float(gdf["djma_m1"].dropna().mean()))

    print(f"  Moyenne globale (fallback) : {moyenne_globale:,} véh/jour" if moyenne_globale else "  Aucune moyenne globale disponible")

    print("\n[2/3] Complétion c1 intra-arc...")
    resultats = gdf.apply(lambda row: completer_arc_c1(row, moyenne_globale), axis=1, result_type="expand")
    resultats.columns = ["djma_m1_c1", "n_segs_m1_c1", "djma_c1_source"]

    gdf = gdf.copy()
    gdf["djma_m1_c1"]    = pd.array(resultats["djma_m1_c1"].tolist(), dtype="Int64")
    gdf["n_segs_m1_c1"]  = resultats["n_segs_m1_c1"].astype(int)
    gdf["djma_c1_source"] = resultats["djma_c1_source"]

    # djma_cam_m1_c1
    djma_float = gdf["djma_m1_c1"].astype(float)
    pct_float  = pd.to_numeric(gdf["pct_cam_m1"], errors="coerce")
    cam_result = (djma_float * pct_float / 100).round()
    gdf["djma_cam_m1_c1"] = cam_result.where(cam_result.notna(), other=pd.NA).astype("Int64")

    # ── Résumé ──────────────────────────────────────────────
    n_original  = (gdf["djma_c1_source"] == "m1_original").sum()
    n_intra     = (gdf["djma_c1_source"] == "c1_intra_arc").sum()
    n_global    = (gdf["djma_c1_source"] == "c1_moyenne_globale").sum()
    n_sans      = (gdf["djma_c1_source"] == "c1_sans_donnee").sum()
    n_null_apres = gdf["djma_m1_c1"].isna().sum()

    segs_avant = gdf["n_segs_m1"].sum()
    segs_apres = gdf["n_segs_m1_c1"].sum()

    print(f"  m1_original         : {n_original:3d} arcs (tous segments avaient une valeur)")
    print(f"  c1_intra_arc        : {n_intra:3d} arcs ({segs_apres - segs_avant} slots NA remplis par moyenne de l'arc)")
    print(f"  c1_moyenne_globale  : {n_global:3d} arcs (fallback {moyenne_globale:,} véh/jour)")
    if n_sans:
        print(f"  c1_sans_donnee      : {n_sans:3d} arcs (cas dégénéré — aucune valeur nulle part)")

    print(f"\n  Segments contributeurs : {segs_avant} → {segs_apres} (+ {segs_apres - segs_avant} récupérés)")
    print(f"  Arcs avec djma_m1_c1 NULL : {n_null_apres}")

    ok = gdf[gdf["djma_m1_c1"].notna()]
    print(f"\n  djma_m1_c1 — médiane : {ok['djma_m1_c1'].median():.0f}  "
          f"min : {ok['djma_m1_c1'].min()}  max : {ok['djma_m1_c1'].max()}")

    print(f"\n[3/3] Export → {OUTPUT_FILE}")
    gdf.to_file(OUTPUT_FILE, layer=OUTPUT_LAYER, driver="GPKG")
    print("  Terminé.")


if __name__ == "__main__":
    main()
