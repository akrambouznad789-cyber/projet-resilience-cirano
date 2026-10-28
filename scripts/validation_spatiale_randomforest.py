"""
validation_spatiale_randomforest.py
===================================
Projet CIRANO — honnêteté de la validation du RandomForest de complétion (% camions).

La validation de generer_figures_resultats.py mélange les sections au hasard
(KFold, shuffle=True). Or le modèle reçoit les coordonnées des sections, et deux
sections voisines se ressemblent : une section de test a presque toujours une
voisine dans l'entraînement, ce qui gonfle le R². Ce script reprend EXACTEMENT
les mêmes données, variables et modèle, et ne change que le découpage :

  1. KFold mélangé (5 plis) : la validation publiée (R² ≈ 0,306)
  2. Blocs spatiaux (5 plis) : carrés de BLOC_KM km, chacun entièrement dans
     l'entraînement ou dans le test (GroupKFold) — la mesure honnête de la
     capacité à prédire une section dans une zone sans voisine connue

Usage : cd scripts && ~/.venvs/portfolio/bin/python validation_spatiale_randomforest.py
Sortie : outputs/validation_spatiale_randomforest.csv (R², RMSE, par découpage et type de route)
"""

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_squared_error, r2_score
from sklearn.model_selection import GroupKFold, KFold, cross_val_predict

from completion_donnees_randomforest import FEATURES_SUPPLEMENTAIRES, ROUTE_DUMMY_COLS, construire_features_supplementaires

RACINE      = Path(__file__).resolve().parent.parent
DEBITS_FILE = RACINE / "data" / "processed" / "debits_completes.gpkg"
SORTIE      = RACINE / "outputs" / "validation_spatiale_randomforest.csv"
ANNEES      = range(1, 11)
DJMA_COLS   = [f"val_djma_annee_{i}" for i in ANNEES]
CAM_COLS    = [f"val_cam_annee_{i}" for i in ANNEES]
BLOC_KM     = 50


def scores(y: np.ndarray, p: np.ndarray, type_route: np.ndarray, decoupage: str) -> list[dict]:
    """R² et RMSE, global puis par type de route (valeurs annuelles aplaties, comme la figure publiée)."""
    lignes = [{"decoupage": decoupage, "type_route": "tous", "n": len(y),
               "r2": r2_score(y, p), "rmse": mean_squared_error(y, p) ** 0.5}]
    for t in np.unique(type_route):
        m = type_route == t
        lignes.append({"decoupage": decoupage, "type_route": t, "n": int(m.sum()),
                       "r2": r2_score(y[m], p[m]), "rmse": mean_squared_error(y[m], p[m]) ** 0.5})
    return lignes


def main() -> None:
    gdf = gpd.read_file(DEBITS_FILE, layer="debits_completes")
    garde = (gdf["methode_djma"].isin(["complet", "interpolation", "extrapolation"])
             & gdf["methode_cam"].isin(["complet", "interpolation", "extrapolation"]))
    features = construire_features_supplementaires(gdf)
    X = np.hstack([gdf.loc[garde, DJMA_COLS].values, features.loc[garde, FEATURES_SUPPLEMENTAIRES].values])
    Y = gdf.loc[garde, CAM_COLS].values
    type_route = np.repeat(features.loc[garde, ROUTE_DUMMY_COLS].idxmax(axis=1).str.replace("route_", "").values,
                           len(ANNEES))

    centre = gdf.loc[garde].to_crs("EPSG:32198").geometry.centroid     # mètres (le fichier est en degrés)
    blocs = (np.floor(centre.x / (BLOC_KM * 1000)).astype(int).astype(str) + "_"
             + np.floor(centre.y / (BLOC_KM * 1000)).astype(int).astype(str)).to_numpy()
    print(f"{int(garde.sum())} sections, {X.shape[1]} variables ; {len(np.unique(blocs))} blocs de {BLOC_KM} km")

    modele = RandomForestRegressor(n_estimators=50, random_state=42, n_jobs=-1)
    lignes = []
    for nom, cv, groupes in [("KFold mélangé (publié)", KFold(5, shuffle=True, random_state=42), None),
                             (f"blocs spatiaux de {BLOC_KM} km", GroupKFold(5), blocs)]:
        p = cross_val_predict(modele, X, Y, cv=cv, groups=groupes)
        lignes += scores(Y.flatten(), p.flatten(), type_route, nom)
        print(f"  {nom:28s} R² = {lignes[-1 - len(np.unique(type_route))]['r2']:.3f}")

    resultat = pd.DataFrame(lignes)
    SORTIE.parent.mkdir(parents=True, exist_ok=True)
    resultat.to_csv(SORTIE, index=False)
    print(resultat.pivot(index="type_route", columns="decoupage", values="r2").round(3).to_string())


if __name__ == "__main__":
    main()
