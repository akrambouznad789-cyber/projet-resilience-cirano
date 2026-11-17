"""Tables d'apprentissage sur des données synthétiques : statut de chaque valeur, cibles, blocs, graphe.

Cinq sections, cinq années (2021 à 2025, créneau 1 = 2025). Dans le fichier « complété », toute valeur
non publiée porte une sentinelle (9999 pour le DJMA, 99,9 pour le % de camions) : si une sentinelle
apparaît dans une cible, une valeur complétée a servi de cible.

    section  DJMA publié            % camions publié     méthodes du fichier complété
    1        les 5 années           les 5 années         complet / complet
    2        2025 et 2023           2023 seulement       extrapolation / extrapolation
    3        2025 et 2024           aucun                extrapolation / mice (forêt aléatoire)
    4        aucun                  2025 seulement       geo_knn / geo_knn : le % publié est écrasé
    5        aucun                  aucun                geo_knn / geo_knn
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import yaml
from shapely.geometry import LineString, Point

from jumeau_cirano.config import DIR_CONFIGS, RACINE
from jumeau_cirano.donnees import tables as t
from jumeau_cirano.donnees.blocs import bloc_50km, bloc_de
from jumeau_cirano.donnees.commande import executer, lire_brut, lire_complete
from jumeau_cirano.reseau.sections import paires_section_lien

ANNEES = [2025, 2024, 2023, 2022, 2021]
DJMA = {1: [500, 510, 520, 530, 540], 2: [100, "", 80, "", ""], 3: [300, 310, "", "", ""],
        4: ["", "", "", "", ""], 5: ["", "", "", "", ""]}
CAM = {1: [10.0, 11.0, 12.0, 13.0, 14.0], 2: ["", "", 8.0, "", ""], 3: ["", "", "", "", ""],
       4: [12.5, "", "", "", ""], 5: ["", "", "", "", ""]}
METHODES = {1: ("complet", "complet"), 2: ("extrapolation", "extrapolation"), 3: ("extrapolation", "mice"),
            4: ("geo_knn", "geo_knn"), 5: ("geo_knn", "geo_knn")}
SENTINELLE = {"djma": 9999.0, "cam": 99.9}
CRS = "EPSG:32198"
# Sections le long de y = 0 (route 00020), sauf la 3 (route 00040) ; blocs « 0_0 », « 1_0 » et « -1_2 »
GEOMETRIES = {1: LineString([(0, 0), (2000, 0)]), 2: LineString([(1500, 0), (2000, 0)]),
              3: LineString([(60000, 5000), (61000, 5000)]), 4: LineString([(-10000, 120000), (-9000, 120000)]),
              5: LineString([(3000, 9000), (4000, 9000)])}
ROUTES = {1: "00020", 2: "00020", 3: "00040", 4: "00020", 5: "00099"}


def brut() -> pd.DataFrame:
    """Fichier du MTQ : valeurs en texte, chaîne vide = non publié."""
    lignes = []
    for s in DJMA:
        ligne = {"ide_sectn_trafc": s}
        for i, annee in enumerate(ANNEES, start=1):
            for mesure, serie in (("djma", DJMA), ("cam", CAM), ("djme", DJMA), ("djmh", DJMA)):
                ligne[f"{mesure}_annee_{i}"] = float(annee)
                ligne[f"val_{mesure}_annee_{i}"] = "" if serie[s][i - 1] == "" else str(float(serie[s][i - 1]))
        lignes.append(ligne)
    return pd.DataFrame(lignes)


def complete() -> pd.DataFrame:
    """Fichier complété : valeurs publiées conservées (sauf le % de camions de la section 4), sentinelles ailleurs."""
    lignes = []
    for s in DJMA:
        ligne = {"ide_sectn_trafc": s, "methode_djma": METHODES[s][0], "methode_cam": METHODES[s][1]}
        for i in range(1, 6):
            for mesure, serie in (("djma", DJMA), ("cam", CAM)):
                v = serie[s][i - 1]
                ligne[f"val_{mesure}_annee_{i}"] = SENTINELLE[mesure] if v == "" else float(v)
        lignes.append(ligne)
    table = pd.DataFrame(lignes)
    table.loc[table["ide_sectn_trafc"] == 4, "val_cam_annee_1"] = 30.0      # KNN : écrase le 12,5 publié
    return table


def sections() -> gpd.GeoDataFrame:
    """Sections projetées, comme les rend donnees.observations.charger_sections (classe vide pour la 5)."""
    return gpd.GeoDataFrame({"ide_sectn_trafc": [str(s) for s in GEOMETRIES], "num_route": list(ROUTES.values()),
                             "classe": ["10", "10", "30", "20", None]},
                            geometry=list(GEOMETRIES.values()), crs=CRS)


def rtss() -> gpd.GeoDataFrame:
    """Trois lignes RTSS : la collectrice 00099 à 20 m de la section 5 ; l'autoroute 00020, qui croise la
    section 5 (plus proche qu'elle, mais d'un autre numéro), puis passe sous les sections 1 et 2."""
    lignes = [LineString([(3000, 9020), (4000, 9020)]), LineString([(3500, 8000), (3500, 10000)]),
              LineString([(0, 0), (2000, 0)])]
    return gpd.GeoDataFrame({"num_route": ["00099", "00020", "00020"], "cod_clasf_": ["40", "10", "10"]},
                            geometry=lignes, crs=CRS)


def liens() -> gpd.GeoDataFrame:
    """Quatre liens : deux sur la route 00020 (sections 1 et 2), un sans section, un tronçon estimé."""
    geoms = [LineString([(0, 0), (1000, 0)]), LineString([(1000, 0), (2000, 0)]),
             LineString([(2000, 0), (2000, 3000)]), LineString([(2000, 0), (2600, 0)])]
    return gpd.GeoDataFrame({"id_lien": [0, 1, 2, 3], "u": [0, 1, 2, 2], "v": [1, 2, 3, 4],
                             "num_route": ["00020", "00020", "00133", "00020"],
                             "cod_clasf_": ["10", "10", "30", "MU"],
                             "cod_cote_c": ["C", "C", "C", ""], "raccord": [False, False, False, True],
                             "longueur_m": [1000.0, 1000.0, 3000.0, 600.0]}, geometry=geoms, crs=CRS)


@pytest.fixture
def table() -> pd.DataFrame:
    return t.table_sections_annees(brut(), complete(), t.attributs_sections(sections(), rtss()))


def cellule(table: pd.DataFrame, section: int, annee: int) -> pd.Series:
    return table[(table["ide_sectn_trafc"] == section) & (table["annee"] == annee)].iloc[0]


def test_bloc_reprend_la_definition_de_la_validation_spatiale():
    lignes = gpd.GeoSeries(list(GEOMETRIES.values()), crs=CRS).to_crs("EPSG:4326")      # en degrés, comme le MTQ
    centre = lignes.to_crs("EPSG:32198").geometry.centroid                              # la formule du script
    attendu = (np.floor(centre.x / (50 * 1000)).astype(int).astype(str) + "_"
               + np.floor(centre.y / (50 * 1000)).astype(int).astype(str)).to_numpy()
    assert list(bloc_50km(lignes)) == list(attendu) == ["0_0", "0_0", "1_0", "-1_2", "0_0"]
    assert list(bloc_de(np.array([-1.0, 49999.9, 50000.0]), np.array([0.0, -0.1, 99999.0]))) == ["-1_0", "0_-1", "1_1"]


def test_methode_de_chaque_valeur(table):
    assert [cellule(table, 1, a)["djma_methode"] for a in ANNEES] == ["mesure"] * 5
    assert [cellule(table, 2, a)["djma_methode"] for a in ANNEES] == [
        "mesure", "interpolation", "mesure", "extrapolation", "extrapolation"]
    assert [cellule(table, 2, a)["pct_camions_methode"] for a in ANNEES] == [
        "recopie", "recopie", "mesure", "recopie", "recopie"]             # une seule année publiée
    assert set(table.loc[table["ide_sectn_trafc"] == 3, "pct_camions_methode"]) == {"foret_aleatoire"}
    assert set(table.loc[table["ide_sectn_trafc"] == 5, "djma_methode"]) == {"knn_idw"}
    assert set(table["djma_methode"]) | set(table["pct_camions_methode"]) <= set(t.METHODES)
    assert cellule(table, 2, 2025)["djma_n_mesures"] == 2 and cellule(table, 2, 2025)["pct_camions_n_mesures"] == 1


def test_une_valeur_completee_ne_peut_pas_servir_de_cible(table):
    for nom, mesure in (("djma", "djma"), ("pct_camions", "cam")):
        cible = table[f"{nom}_cible"]
        assert (cible.isna() == ~table[f"{nom}_mesure"]).all()                 # vide partout où rien n'est publié
        assert SENTINELLE[mesure] not in set(cible.dropna())                    # aucune valeur complétée en cible
        assert (table.loc[~table[f"{nom}_mesure"], f"{nom}_complete"] == SENTINELLE[mesure]).all()
        ex = t.exemples(table, nom)
        assert len(ex) == int(table[f"{nom}_mesure"].sum()) and ex[f"{nom}_cible"].notna().all()
        assert {f"{nom}_complete", f"{nom}_n_mesures", "camions_cible"}.isdisjoint(ex.columns)
    assert len(t.exemples(table, "djma")) == 9 and len(t.exemples(table, "pct_camions")) == 7
    assert {"djme", "djmh"}.isdisjoint(t.exemples(table, "djma").columns)            # cibles sœurs du DJMA
    assert {"djme", "djmh", "djma_cible"} <= set(t.exemples(table, "pct_camions").columns)

    fuite = table.copy()                                                        # cible remplie par le fichier complété
    fuite["djma_cible"] = fuite["djma_complete"]
    with pytest.raises(ValueError, match="non publiée"):
        t.verifier_cibles(fuite)
    with pytest.raises(ValueError, match="non publiée"):
        t.exemples(fuite, "djma")
    drapeau = table.copy()                                                      # drapeau faussé
    drapeau.loc[drapeau["djma_methode"] == "knn_idw", "djma_methode"] = "mesure"
    with pytest.raises(ValueError, match="methode"):
        t.verifier_cibles(drapeau)


def test_valeur_publiee_ecrasee_par_la_completion(table):
    c = cellule(table, 4, 2025)
    assert c["pct_camions_cible"] == 12.5 and c["pct_camions_complete"] == 30.0     # la cible vient du brut
    assert c["pct_camions_mesure"] and c["pct_camions_ecrase"] and c["pct_camions_methode"] == "mesure"
    assert c["djma_methode"] == "knn_idw" and np.isnan(c["djma_cible"])
    assert int(table["pct_camions_ecrase"].sum()) == 1 and not table["djma_ecrase"].any()
    assert 12.5 in set(t.exemples(table, "pct_camions")["pct_camions_cible"])


def test_camions_seulement_si_les_deux_mesures_sont_publiees(table):
    assert int(table["camions_cible"].notna().sum()) == 6               # section 1 (5 ans), section 2 en 2023
    assert cellule(table, 2, 2023)["camions_cible"] == pytest.approx(80 * 8.0 / 100)
    ex = t.exemples(table, "camions")
    assert len(ex) == 6 and {"djma_cible", "pct_camions_cible", "djma_complete", "djme", "djmh",
                             "djma_n_mesures", "pct_camions_n_mesures"}.isdisjoint(ex.columns)
    with pytest.raises(ValueError, match="cible inconnue"):
        t.exemples(table, "djme")


def test_fichiers_incoherents_refuses():
    faux = complete()
    faux.loc[faux["ide_sectn_trafc"] == 2, "methode_djma"] = "geo_knn"              # la section 2 a du DJMA publié
    with pytest.raises(ValueError, match="geo_knn"):
        t.series_longues(brut(), faux)
    trou = complete()
    trou.loc[0, "val_djma_annee_3"] = np.nan
    with pytest.raises(ValueError, match="manquantes"):
        t.series_longues(brut(), trou)
    decale = brut()
    decale.loc[0, "djma_annee_1"] = 2019.0                                          # créneau à deux années
    with pytest.raises(ValueError, match="année unique"):
        t.series_longues(decale, complete())
    soeur = brut()
    soeur["djme_annee_2"] = 2019.0                                                  # DJME décalé sur le DJMA
    with pytest.raises(ValueError, match="djme"):
        t.series_longues(soeur, complete())
    sans_methode = complete()
    sans_methode.loc[sans_methode["ide_sectn_trafc"] == 3, "methode_cam"] = "extrapolation"   # aucun % publié
    with pytest.raises(ValueError, match="ni forêt aléatoire ni KNN"):
        t.series_longues(brut(), sans_methode)


def test_type_de_route_position_et_bloc(table):
    a = table.drop_duplicates("ide_sectn_trafc").set_index("ide_sectn_trafc")
    assert list(a["type_route"]) == ["Autoroute", "Autoroute", "Régionale", "Nationale", "Collectrice"]
    assert list(a["type_route_source"]) == ["code_rtss"] * 4 + ["meme_numero"]     # pas l'autoroute qui la croise
    assert list(a["bloc_50km"]) == ["0_0", "0_0", "1_0", "-1_2", "0_0"]
    assert a.loc[1, ["x", "y", "longueur_m"]].tolist() == [1000.0, 0.0, 2000.0]

    sans_code = t.attributs_sections(sections().assign(classe=None), rtss()).set_index("ide_sectn_trafc")
    assert list(sans_code["type_route"]) == ["Autoroute", "Autoroute", "Inconnu", "Inconnu", "Collectrice"]
    assert list(sans_code["type_route_source"]) == ["meme_numero", "meme_numero", "aucune", "aucune", "meme_numero"]
    assert sans_code["classe_rtss"].isna().sum() == 2              # 3 : route 00040 absente ; 4 : la 00020 est loin


def test_graphe_et_etiquettes_des_aretes(table):
    lg = liens()
    points = gpd.GeoDataFrame({"id_noeud": [0, 1], "degre": [1, 2], "raccord": [False, True]},
                              geometry=[Point(0, 0), Point(1000, 0)], crs=CRS)
    aretes, noeuds = t.table_aretes(lg), t.table_noeuds(points, lg)
    assert list(aretes["estime"]) == [False, False, False, True] and aretes["type_route"].iloc[3] == "Raccord estimé"
    assert list(noeuds["bloc_50km"]) == ["0_0", "0_0"] and list(noeuds["jonction_nouee"]) == [False, True]
    assert list(noeuds["ecart_jonction_m"]) == [0.0, 0.0]                # extrémités communes : aucun écart

    paires = paires_section_lien(sections(), lg[~lg["raccord"]], 30.0)
    inc = t.table_aretes_sections(paires, table.drop_duplicates("ide_sectn_trafc"))
    assert set(zip(inc["id_lien"], inc["ide_sectn_trafc"], strict=True)) == {(0, 1), (1, 1), (1, 2)}
    assert 3 not in set(inc["id_lien"])                                 # un tronçon estimé n'a pas de comptage
    longueur = inc.set_index(["id_lien", "ide_sectn_trafc"])["longueur_commune_m"]
    assert longueur[(1, 2)] == pytest.approx(500, abs=1) and longueur[(1, 1)] == pytest.approx(1030, abs=1)

    e = t.etiquettes_aretes(inc, table).set_index(["id_lien", "annee"])
    assert e.loc[(0, 2025), "djma_cible"] == 500 and e.loc[(0, 2025), "djma_part_mesuree"] == 1.0
    poids = np.array([longueur[(1, 1)], longueur[(1, 2)]])
    assert e.loc[(1, 2025), "djma_cible"] == pytest.approx(np.average([500, 100], weights=poids))
    assert e.loc[(1, 2024), "djma_cible"] == 510                        # la section 2 n'est pas publiée en 2024
    assert e.loc[(1, 2024), "djma_part_mesuree"] == pytest.approx(poids[0] / poids.sum())
    assert e.loc[(1, 2024), "djma_complete"] == pytest.approx(np.average([510, 9999], weights=poids))
    assert e.loc[(1, 2023), "camions_cible"] == pytest.approx(np.average([520 * 0.12, 80 * 0.08], weights=poids))

    appris = t.etiquettes_aretes(inc, table, sections_gardees=[2]).set_index(["id_lien", "annee"])
    assert set(appris.index.get_level_values("id_lien")) == {1}                     # section 1 mise de côté : muette
    assert appris.loc[(1, 2025), "djma_cible"] == 100


def test_ecart_de_jonction_distingue_les_raccords_estimes():
    """Nœud 1 : carrefour en T posé à 0 m, plus une extrémité pendante raccordée à 15 m (lien 2)."""
    lg = gpd.GeoDataFrame({"u": [0, 1, 1], "v": [1, 2, 3]}, crs=CRS, geometry=[
        LineString([(0, 0), (100, 0)]), LineString([(100, 0), (200, 0)]), LineString([(100, 15), (100, 300)])])
    points = gpd.GeoDataFrame({"id_noeud": [0, 1, 2, 3], "degre": [1, 3, 1, 1], "raccord": [False, True, False, False]},
                              geometry=[Point(0, 0), Point(100, 5), Point(200, 0), Point(100, 300)], crs=CRS)
    noeuds = t.table_noeuds(points, lg)
    assert noeuds["ecart_jonction_m"].tolist() == pytest.approx([0.0, 15.0, 0.0, 0.0])
    assert noeuds["jonction_nouee"].tolist() == [False, True, False, False]


def test_blocs_de_la_validation_spatiale_sur_les_donnees_publiques():
    """Sur les fichiers du MTQ suivis par git : les sections de la validation publiée occupent 149 blocs."""
    chemin_brut = RACINE / "data" / "raw" / "DebitCirculation.gpkg"
    chemin_complet = RACINE / "data" / "processed" / "debits_completes.gpkg"
    if not (chemin_brut.exists() and chemin_complet.exists()):
        pytest.skip("données publiques absentes")
    methodes = gpd.read_file(chemin_complet, columns=["methode_djma", "methode_cam"], ignore_geometry=True)
    temporelles = ["complet", "interpolation", "extrapolation"]
    garde = methodes["methode_djma"].isin(temporelles) & methodes["methode_cam"].isin(temporelles)
    geometries = gpd.read_file(chemin_brut, columns=["ide_sectn_trafc"]).geometry
    blocs = bloc_50km(geometries)
    assert int(garde.sum()) == 3666 and len(set(blocs[garde.to_numpy()])) == 149 and len(set(blocs)) == 182


def test_commande_sur_fichiers_synthetiques(tmp_path: Path):
    b, c, s = brut(), complete(), sections()
    codes = ["0002001000000C", "0002001000000C", "0004001000000C", "0002002000000C", "0009902000000C"]
    textes = [f"2025 DJMA:{x[0]} / DJME: / DJMH: / %cam:{y[0]} / 30e h:"
              for x, y in zip(DJMA.values(), CAM.values(), strict=True)]
    geo_brut = gpd.GeoDataFrame(b.assign(rtss_debut=codes, rtss_fin="", annee_en_cours=textes),
                                geometry=list(s.geometry), crs=CRS)
    geo_brut.to_file(tmp_path / "brut.gpkg", driver="GPKG")
    gpd.GeoDataFrame(c, geometry=list(s.geometry), crs=CRS).to_file(tmp_path / "complete.gpkg", driver="GPKG")
    rtss().assign(num_rts=["0009901000000C", "0002003000000C", "0002001000000C"]).to_file(
        tmp_path / "rtss.gpkg", driver="GPKG")                          # les codes des sections 3, 4 et 5 sont absents
    liens().to_file(tmp_path / "reseau.gpkg", layer="liens_rtss", driver="GPKG")
    gpd.GeoDataFrame({"id_noeud": [0, 1, 2, 3, 4], "degre": [1, 2, 3, 1, 1], "raccord": [False] * 5},
                     geometry=[Point(0, 0), Point(1000, 0), Point(2000, 0), Point(2000, 3000), Point(2600, 0)],
                     crs=CRS).to_file(tmp_path / "reseau.gpkg", layer="noeuds_rtss", driver="GPKG")

    reseau = yaml.safe_load((DIR_CONFIGS / "reseau.yaml").read_text(encoding="utf-8"))
    reseau["entrees"] |= {"sections": str(tmp_path / "brut.gpkg"),
                          "sections_completes": str(tmp_path / "complete.gpkg"), "rtss": str(tmp_path / "rtss.gpkg")}
    reseau["sortie"] = str(tmp_path / "reseau.gpkg")
    (tmp_path / "reseau.yaml").write_text(yaml.safe_dump(reseau, allow_unicode=True), encoding="utf-8")
    (tmp_path / "tables.yaml").write_text(yaml.safe_dump({
        "reseau": str(tmp_path / "reseau.yaml"), "dossier_sortie": str(tmp_path / "ml"), "bloc_km": 50,
        "meta_publique": str(tmp_path / "docs" / "tables.json"),
        "blocs_validation_attendus": 1}), encoding="utf-8")                          # sections 1 et 2 : bloc « 0_0 »

    lu = lire_brut(tmp_path / "brut.gpkg")
    assert list(lu.columns[:3]) == ["ide_sectn_trafc", "djma_annee_1", "val_djma_annee_1"]
    assert "methode_cam" in lire_complete(tmp_path / "complete.gpkg").columns
    executer(argparse.Namespace(config=str(tmp_path / "tables.yaml"), echantillon=False))

    meta = json.loads((tmp_path / "ml" / "meta.json").read_text(encoding="utf-8"))
    assert json.loads((tmp_path / "docs" / "tables.json").read_text(encoding="utf-8")) == meta     # copie publique
    assert meta["controles"]["djma"]["cibles"] == 9 and meta["controles"]["pct_camions"]["cibles"] == 7
    assert meta["controles"]["pct_camions"]["valeurs_ecrasees_par_la_completion"] == 1
    assert meta["controles"]["blocs_validation"] == 1 and len(meta["entrees"]) == 4
    assert meta["controles"]["type_route_source"] == {"code_rtss": 2, "aucune": 2, "meme_numero": 1}
    assert meta["controles"]["sections_sur_le_graphe"] == 2 and set(meta["code"]) == {"jumeau_cirano", "commit"}
    assert meta["controles"]["aretes_etiquetees_par_annee"]["camions"] == {str(a): 2 for a in ANNEES}
    assert meta["controles"]["noeuds_jonction_estimee"] == 0 and meta["controles"]["seuil_jonction_estimee_m"] == 2.0
    c = meta["controles"]
    assert c["ecart_jonction_m"]["jonctions_estimees"]["mediane"] is None             # aucune : pas de NaN
    assert {cible: e["lignes"] for cible, e in c["exemples"].items()} == {"djma": 9, "pct_camions": 7, "camions": 6}
    assert c["exemples"]["djma"]["lignes_ou_djme_et_djmh_sont_publies"] == 9
    assert c["paires_lien_section"] == 3 and c["paires_lien_section_de_blocs_differents"] == 0
    ecrite = pd.read_parquet(tmp_path / "ml" / "sections_annees.parquet")
    assert len(ecrite) == 25 and list(ecrite.columns) == meta["tables"]["sections_annees"]["colonnes"]
    t.verifier_cibles(ecrite)
    assert {p.name for p in (tmp_path / "ml").iterdir()} == {
        "sections_annees.parquet", "noeuds.parquet", "aretes.parquet", "aretes_sections.parquet",
        "aretes_annees.parquet", "meta.json"}

    (tmp_path / "tables.yaml").write_text(yaml.safe_dump({
        "reseau": str(tmp_path / "reseau.yaml"), "dossier_sortie": str(tmp_path / "ml"), "bloc_km": 50,
        "blocs_validation_attendus": 149}), encoding="utf-8")
    with pytest.raises(ValueError, match="blocs"):                                   # le contrôle des blocs arrête tout
        executer(argparse.Namespace(config=str(tmp_path / "tables.yaml"), echantillon=False))
