"""
observations.py
===============
Sections de comptage du MTQ : ce qui est MESURÉ et ce qui est COMPLÉTÉ.

Le Volet 1 a complété les années manquantes (interpolation, forêt aléatoire,
KNN). Ces valeurs complétées servent à l'affichage ; la calibration du modèle,
elle, ne doit apprendre que des mesures réelles, sinon elle apprendrait nos
propres imputations (fuite). Une section est donc « observée » si son DJMA ET
son % de camions BRUTS de 2025 existent dans le fichier du MTQ.

Pseudo-code
-----------
charger_sections()   fichier brut (géométrie, codes RTSS, chaîne « 2025 DJMA:… / %cam:… »)
                     + valeurs 2025 complétées (debits_completes)
                     + numéro et classe de route (code RTSS → RTSS)
                     → camions_obs = DJMA brut × %cam brut / 100, observee = les deux mesurés
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd

from jumeau_cirano.config import ConfigReseau, chemin_absolu


def lire_mesure(chaine: pd.Series, cle: str) -> pd.Series:
    """Valeur numérique de `cle` dans « 2025 DJMA:1780 / … / %cam:11.7 / … » ; NaN si vide."""
    return pd.to_numeric(chaine.str.extract(rf"{cle}:([\d.]+)", expand=False), errors="coerce")


def charger_sections(cfg: ConfigReseau) -> gpd.GeoDataFrame:
    """Une ligne par section de comptage, projetée : route, classe, valeurs 2025 complétées et mesurées."""
    brut = gpd.read_file(chemin_absolu(cfg.entrees.sections),
                         columns=["ide_sectn_trafc", "rtss_debut", "rtss_fin", "annee_en_cours"])
    brut = brut.to_crs(cfg.crs)
    brut["ide_sectn_trafc"] = brut["ide_sectn_trafc"].astype(str)
    brut["djma_obs"] = lire_mesure(brut["annee_en_cours"], "DJMA")
    brut["pct_cam_obs"] = lire_mesure(brut["annee_en_cours"], "%cam")
    brut["annee"] = pd.to_numeric(brut["annee_en_cours"].str[:4], errors="coerce")

    colonnes = ["ide_sectn_trafc", "val_djma_annee_1", "val_cam_annee_1", "methode_djma", "methode_cam"]
    comp = gpd.read_file(chemin_absolu(cfg.entrees.sections_completes), ignore_geometry=True, columns=colonnes)
    comp["ide_sectn_trafc"] = comp["ide_sectn_trafc"].astype(str)
    comp = comp.rename(columns={"val_djma_annee_1": "djma", "val_cam_annee_1": "pct_cam"})
    s = brut.merge(comp, on="ide_sectn_trafc", how="left")

    rtss = gpd.read_file(chemin_absolu(cfg.entrees.rtss), ignore_geometry=True, columns=["num_rts", "cod_clasf_"])
    classe = rtss.drop_duplicates("num_rts").set_index("num_rts")["cod_clasf_"]
    s["classe"] = (s["rtss_debut"].str.replace("-", "", regex=False).map(classe)
                   .fillna(s["rtss_fin"].str.replace("-", "", regex=False).map(classe)))
    s["num_route"] = s["rtss_debut"].str[:5]
    s["longueur_m"] = s.geometry.length
    s["observee"] = s["djma_obs"].notna() & s["pct_cam_obs"].notna() & (s["annee"] == 2025)
    s["camions_obs"] = np.where(s["observee"], s["djma_obs"] * s["pct_cam_obs"] / 100, np.nan)
    s["camions"] = s["djma"] * s["pct_cam"] / 100
    return s.drop(columns=["annee_en_cours", "rtss_fin"])
