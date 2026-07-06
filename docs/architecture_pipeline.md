# Architecture du pipeline — Résilience réseau routier québécois
**Projet CIRANO II** | Polytechnique Montréal / CIRANO / MTQ  
**Chercheur :** Akram Bouznad | **Mise à jour :** 2026-06-13

---

## Vue d'ensemble

```mermaid
flowchart TD

    %% ── SOURCES DE DONNÉES BRUTES ─────────────────────────────────
    subgraph RAW["📦 Données brutes MTQ  ·  data/raw/  ·  LECTURE SEULE"]
        direction LR
        A1["reseau_arcs.gpkg\n─────────────────\nGraphe ville-à-ville\n~1 600 arcs · 2 couches\n(arcs, noeuds)\nCRS : EPSG:32198"]
        A2["ReseauRoutier_RTSS.gpkg\n─────────────────\nRéférentiel routier MTQ\nClassification (Autoroute,\nNationale, Régionale…)\nCRS : EPSG:32198"]
        A3["DebitCirculation.gpkg\n─────────────────\nComptages de circulation\nDJMA par section · annuels\n% camion · DJME · DJMH\nCRS : EPSG:32198"]
    end

    %% ── ÉTAPE A : ROUTAGE ─────────────────────────────────────────
    subgraph ROUTAGE["🔀 Étape A — Routage  ·  algo_graphe_reseau_v2.py  ·  v2 FIGÉ"]
        direction TB
        B1["Filtre 1 — Distance au tracé OSRM\nBuffer 1 500 m · seuil 400 m\n→ candidats dans le corridor de la route"]
        B2["Filtre 2 — Directionnel\nDifférence angulaire < 45°\n→ élimine les routes adjacentes"]
        B3["Filtre 3 — Proximité aux nœuds\nExclut les segments plus proches\nd'un nœud tiers que de A ou B"]
        B1 --> B2 --> B3
    end

    %% ── SORTIE ROUTAGE ────────────────────────────────────────────
    subgraph GPKG_V2["📄 graphe_routier_v2_sample.gpkg"]
        direction LR
        C1["arcs_enrichis_v2\n30 arcs enrichis\n(DJMA encodés · types RTSS · distances)"]
        C2["trajets_segments_v2\n1 ligne / segment retenu\n(géométrie réelle · djma_val · cam_val)"]
        C3["trace_osrm_v2\nTracé OSRM brut\n(validation visuelle)"]
    end

    %% ── ÉTAPE B : CALCUL DJMA ─────────────────────────────────────
    subgraph DJMA["📊 Étape B — Calcul DJMA  ·  4 méthodes indépendantes"]
        direction LR
        D1["m1 — Moyenne simple\nmean(djma_val)"]
        D2["m2 — Pondération longueur\nΣ(djma × long) / Σlong"]
        D3["m3 — Pondération type MTQ\nΣ(djma × poids_type) / Σpoids\nAutoroute=4 · Nationale=3…"]
        D4["m4 — Composite longueur × type\nΣ(djma × long × poids) / Σ(long × poids)"]
    end

    %% ── SORTIES DJMA ──────────────────────────────────────────────
    subgraph OUT_DJMA["📄 Sorties  ·  data/processed/"]
        direction LR
        E1["graphe_routier_v2_djma_m1.gpkg ✓"]
        E2["graphe_routier_v2_djma_m2.gpkg ✓"]
        E3["graphe_routier_v2_djma_m3.gpkg ✓"]
        E4["graphe_routier_v2_djma_m4.gpkg ✓"]
    end

    %% ── VALIDATION ────────────────────────────────────────────────
    subgraph VALID["✅ Validation  ·  Sample 30 arcs"]
        direction LR
        F1["comparer_methodes_djma.py\nStats · corrélations · arcs divergents"]
        F2["QGIS\nVisualisation géographique\nsymbologie par DJMA"]
    end

    %% ── FUTURES ÉTAPES ────────────────────────────────────────────
    subgraph FUTURE["🚀 Prochaines étapes"]
        direction TB
        G1["Restructuration graphe\nNœuds = Zones RMR\nArcs = liaisons inter-RMR"]
        G2["Imputation ML\nComplétion données manquantes\n(temporel + spatial)"]
        G3["Passage à l'échelle Québec\nSAMPLE_N_ARCS = None\n~1 600 arcs · réseau complet"]
        G4["Analyse de résilience\nPerturbations · connectivité\nIndicateurs OD"]
        G1 --> G3
        G2 --> G3
        G3 --> G4
    end

    %% ── FLUX PRINCIPAL ────────────────────────────────────────────
    A1 & A2 & A3 --> ROUTAGE
    ROUTAGE --> GPKG_V2
    C1 & C2 --> DJMA
    D1 --> E1
    D2 --> E2
    D3 --> E3
    D4 --> E4
    E1 & E2 & E3 & E4 --> VALID
    VALID --> FUTURE

    %% ── STYLES ────────────────────────────────────────────────────
    style RAW        fill:#f0f4ff,stroke:#4a6cf7,color:#000
    style ROUTAGE    fill:#fff8e6,stroke:#f5a623,color:#000
    style GPKG_V2    fill:#e8f5e9,stroke:#43a047,color:#000
    style DJMA       fill:#fce4ec,stroke:#e91e63,color:#000
    style OUT_DJMA   fill:#e8f5e9,stroke:#43a047,color:#000
    style VALID      fill:#e8f5e9,stroke:#2e7d32,color:#000
    style FUTURE     fill:#ede7f6,stroke:#6a1b9a,color:#000
```

---

## Légende des étapes

| Étape | Script | Statut | Sortie |
|---|---|---|---|
| **A — Routage v2** | `algo_graphe_reseau_v2.py` | ✅ Figé, validé QGIS | `graphe_routier_v2_sample.gpkg` |
| **B1 — DJMA m1** | `calcul_djma_m1.py` | ✅ Produit | `graphe_routier_v2_djma_m1.gpkg` |
| **B2 — DJMA m2** | `calcul_djma_m2.py` | ✅ Produit | `graphe_routier_v2_djma_m2.gpkg` |
| **B3 — DJMA m3** | `calcul_djma_m3.py` | ✅ Produit | `graphe_routier_v2_djma_m3.gpkg` |
| **B4 — DJMA m4** | `calcul_djma_m4.py` | ✅ Produit | `graphe_routier_v2_djma_m4.gpkg` |
| **Validation** | `comparer_methodes_djma.py` + QGIS | ✅ Fait (30 arcs, corrél. > 0.97) | — |
| **Nœuds RMR** | À définir | 🔜 Prochaine session | `reseau_arcs_rmr.gpkg` |
| **Imputation ML** | À développer | 🔜 Prochaine session | `djma_complet_ml.gpkg` |
| **Échelle Québec** | `algo_graphe_reseau_v2.py` (`SAMPLE_N_ARCS=None`) | 🔜 Après validation RMR | `graphe_routier_v2_full.gpkg` |
| **Résilience** | À développer | 🔮 Phase finale | Indicateurs OD · rapports |

---

## Convention de nommage des fichiers

```
graphe_routier_v{routage}_djma_m{méthode}.gpkg
                  │                  │
                  └── v2 : figé      └── m1 à m4 : méthodes d'agrégation
```

**Axes indépendants** — changer la méthode DJMA ne touche pas au routage, et vice-versa.
