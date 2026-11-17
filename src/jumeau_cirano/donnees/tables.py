"""
tables.py
=========
Tables d'apprentissage tirées des données publiques du MTQ : sections × années,
nœuds et arêtes du graphe RTSS. Aucune donnée de l'ENR.

Règle de construction : une CIBLE est toujours une valeur publiée par le MTQ,
lue dans le fichier brut. Le fichier complété par le Module 1 n'est pas un
sur-ensemble du brut (des % de camions publiés y sont remplacés par la valeur
des voisins), et la plupart de ses cases viennent d'une complétion : un modèle
qui les prend pour cibles apprend la méthode de complétion, pas le trafic.

Colonnes d'une mesure (`djma`, `pct_camions`) dans sections_annees :
    <mesure>_cible     valeur publiée par le MTQ ; vide si l'année n'est pas publiée
    <mesure>_complete  valeur du fichier complété (jamais vide) : à n'utiliser qu'en variable, avec sa méthode
    <mesure>_mesure    vrai si la valeur est publiée
    <mesure>_methode   mesure, interpolation, extrapolation, recopie, foret_aleatoire ou knn_idw
    <mesure>_ecrase    vrai si la valeur publiée a été remplacée dans le fichier complété

Pseudo-code
-----------
series_longues()        brut + complété → une ligne par section et par année
attributs_sections()    type de route (code RTSS de la section, sinon lignes RTSS de même numéro à 30 m),
                        position, longueur, bloc de 50 km
verifier_cibles()       refuse une table dont une cible n'est pas une valeur publiée
exemples()              lignes dont la cible est publiée, sans les colonnes qui la répètent
table_noeuds(), table_aretes(), table_aretes_sections()    graphe RTSS et comptages qui longent chaque lien
etiquettes_aretes()     lien × année : moyenne des sections du lien, pondérée par la longueur commune
"""

from __future__ import annotations

from collections.abc import Iterable

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

from jumeau_cirano.donnees.blocs import BLOC_KM, CRS_BLOCS, bloc_de, centroides
from jumeau_cirano.reseau.sections import paires_section_lien

CIBLES = {"djma": "djma", "cam": "pct_camions"}          # mesure du fichier du MTQ → nom dans les tables
SOEURS_DJMA = ("djme", "djmh")                            # même estimation du MTQ que le DJMA, publiées seulement
METHODES = ["mesure", "interpolation", "extrapolation", "recopie", "foret_aleatoire", "knn_idw"]
TYPES_ROUTE = {"10": "Autoroute", "20": "Nationale", "30": "Régionale", "40": "Collectrice", "MU": "Raccord estimé"}
AUTRE = "Autre"                                           # classe RTSS connue, hors des quatre grandes
INCONNU = "Inconnu"                                       # aucune classe RTSS : route absente du RTSS
TAMPON_M = 30.0                                           # règle de rattachement des sections (configs/reseau.yaml)


# ---------------------------------------------------------------------------
# Sections × années
# ---------------------------------------------------------------------------

def creneaux(brut: pd.DataFrame, mesure: str) -> list[tuple[int, int]]:
    """(créneau, année) des colonnes `val_<mesure>_annee_<i>` ; l'année d'un créneau est la même partout."""
    resultat = []
    i = 1
    while f"val_{mesure}_annee_{i}" in brut.columns:
        annees = pd.to_numeric(brut[f"{mesure}_annee_{i}"], errors="coerce").dropna().unique()
        if len(annees) != 1:
            raise ValueError(f"le créneau {i} de « {mesure} » ne porte pas une année unique : {sorted(annees)[:5]}")
        resultat.append((i, int(annees[0])))
        i += 1
    if not resultat:
        raise ValueError(f"aucune colonne val_{mesure}_annee_<i> dans le fichier brut")
    return resultat


def valeurs(table: pd.DataFrame, colonnes: list[str]) -> np.ndarray:
    """Colonnes de valeurs en nombres (sections × années) ; le texte vide du fichier du MTQ devient NaN."""
    return table[colonnes].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)


def methodes(present: np.ndarray, annees: np.ndarray, knn: np.ndarray, foret: np.ndarray | None) -> np.ndarray:
    """Méthode de chaque valeur (sections × années), d'après les années publiées de la section.

    `present` : la valeur est publiée. `knn` : sections complétées par KNN + IDW (aucun DJMA publié).
    `foret` : sections dont le % de camions vient de la forêt aléatoire (aucun % de camions publié).
    """
    premiere = np.where(present, annees, np.inf).min(axis=1)
    derniere = np.where(present, annees, -np.inf).max(axis=1)
    entre = (annees[None, :] > premiere[:, None]) & (annees[None, :] < derniere[:, None])
    m = np.where(entre, "interpolation", "extrapolation").astype(object)
    m[present.sum(axis=1) == 1, :] = "recopie"            # une seule année publiée : recopiée, sans pente
    if foret is not None:
        m[foret, :] = "foret_aleatoire"
    m[knn, :] = "knn_idw"
    m[present] = "mesure"
    return m


def series_longues(brut: pd.DataFrame, complete: pd.DataFrame) -> pd.DataFrame:
    """Une ligne par section et par année : valeurs publiées, valeurs complétées, méthode de chaque valeur.

    `brut` : fichier du MTQ (ide_sectn_trafc, <mesure>_annee_<i>, val_<mesure>_annee_<i>).
    `complete` : fichier du Module 1 (mêmes valeurs complétées, methode_djma, methode_cam).
    """
    if not brut["ide_sectn_trafc"].is_unique or not complete["ide_sectn_trafc"].is_unique:
        raise ValueError("identifiant de section en double")
    c = complete.set_index("ide_sectn_trafc").reindex(brut["ide_sectn_trafc"])
    if c["methode_djma"].isna().any():
        raise ValueError("des sections du fichier brut manquent dans le fichier complété")
    paires = creneaux(brut, "djma")
    annees = np.array([a for _, a in paires])
    n, k = len(brut), len(annees)
    knn = (c["methode_djma"] == "geo_knn").to_numpy()
    foret = (c["methode_cam"] == "mice").to_numpy()

    table = {"ide_sectn_trafc": np.repeat(brut["ide_sectn_trafc"].to_numpy(), k), "annee": np.tile(annees, n)}
    for mesure, nom in CIBLES.items():
        if [a for _, a in creneaux(brut, mesure)] != list(annees):
            raise ValueError(f"les années de « {mesure} » ne suivent pas celles du DJMA")
        colonnes = [f"val_{mesure}_annee_{i}" for i, _ in paires]
        publie, complet = valeurs(brut, colonnes), c[colonnes].to_numpy(dtype=float)
        present = ~np.isnan(publie)
        if mesure == "djma" and (present.any(axis=1) == knn).any():
            raise ValueError("methode_djma = geo_knn ne coïncide pas avec les sections sans DJMA publié")
        if mesure == "cam" and (foret & (present.any(axis=1) | knn)).any():
            raise ValueError("methode_cam = mice sur une section qui a un % de camions publié, ou sans DJMA")
        if mesure == "cam" and (~present.any(axis=1) & ~foret & ~knn).any():
            raise ValueError("% de camions : section sans aucune valeur publiée, ni forêt aléatoire ni KNN")
        if np.isnan(complet).any():
            raise ValueError(f"le fichier complété a encore des valeurs manquantes pour « {mesure} »")
        table[f"{nom}_cible"] = publie.ravel()
        table[f"{nom}_complete"] = complet.ravel()
        table[f"{nom}_mesure"] = present.ravel()
        table[f"{nom}_methode"] = methodes(present, annees, knn, foret if mesure == "cam" else None).ravel()
        table[f"{nom}_ecrase"] = (present & (np.abs(publie - complet) > 1e-9)).ravel()
        table[f"{nom}_n_mesures"] = np.repeat(present.sum(axis=1), k)
    for mesure in SOEURS_DJMA:                            # publiés seulement : le Module 1 ne les complète pas
        if f"val_{mesure}_annee_1" in brut.columns:
            if [a for _, a in creneaux(brut, mesure)] != list(annees):
                raise ValueError(f"les années de « {mesure} » ne suivent pas celles du DJMA")
            table[mesure] = valeurs(brut, [f"val_{mesure}_annee_{i}" for i, _ in paires]).ravel()
    resultat = pd.DataFrame(table)
    resultat["camions_cible"] = resultat["djma_cible"] * resultat["pct_camions_cible"] / 100
    return resultat.sort_values(["ide_sectn_trafc", "annee"], ignore_index=True)


def attributs_sections(sections: gpd.GeoDataFrame, rtss: gpd.GeoDataFrame | None = None,
                       bloc_km: float = BLOC_KM, tampon_m: float = TAMPON_M) -> pd.DataFrame:
    """Attributs fixes d'une section : type de route, position, longueur, bloc de 50 km.

    `sections` : ide_sectn_trafc, num_route, classe (code RTSS de la section, vide si introuvable), geometry.
    `rtss` (num_route, cod_clasf_, geometry) : classe une section sans code par les lignes RTSS de MÊME numéro
    de route qu'elle longe à `tampon_m` ou moins (la règle de rattachement du Module 3), la plus longue
    l'emportant. La ligne la plus proche n'est jamais prise : c'est le plus souvent une autre route, qui croise
    la section. Sans ligne de même numéro, la route n'est pas décrite par le RTSS et le type reste « Inconnu ».

    type_route_source : code_rtss (code de la section), meme_numero (lignes de même numéro) ou aucune.
    """
    sections = sections.reset_index(drop=True)
    x, y = centroides(sections.geometry)
    classe = sections["classe"].astype(object).where(sections["classe"].notna(), None)
    source = np.where(classe.notna(), "code_rtss", "aucune").astype(object)
    sans = classe.isna().to_numpy()
    if rtss is not None and sans.any():
        lignes = rtss[["num_route", "cod_clasf_", "geometry"]].to_crs(sections.crs).reset_index(drop=True)
        paires = paires_section_lien(sections.loc[sans, ["ide_sectn_trafc", "num_route", "geometry"]],
                                     lignes.assign(id_lien=np.arange(len(lignes))), tampon_m)
        paires = paires[paires["longueur_m"] > 0]
        paires = paires.assign(classe=lignes["cod_clasf_"].to_numpy()[paires["id_lien"].to_numpy()])
        choix = (paires.groupby(["ide_sectn_trafc", "classe"], as_index=False)["longueur_m"].sum()
                 .sort_values(["ide_sectn_trafc", "longueur_m", "classe"], ascending=[True, False, True])
                 .drop_duplicates("ide_sectn_trafc"))
        position = pd.Series(np.arange(len(sections)), index=sections["ide_sectn_trafc"].to_numpy())
        i = position.loc[choix["ide_sectn_trafc"].to_numpy()].to_numpy()
        classe.iloc[i] = choix["classe"].to_numpy()
        source[i] = "meme_numero"
    longueur = sections.geometry.to_crs(CRS_BLOCS).length.to_numpy()
    return pd.DataFrame({
        "ide_sectn_trafc": sections["ide_sectn_trafc"].astype("int64").to_numpy(),
        "num_route": sections["num_route"].to_numpy(),
        "classe_rtss": classe.to_numpy(),
        "type_route": np.where(classe.isna(), INCONNU, classe.map(TYPES_ROUTE).fillna(AUTRE)).astype(object),
        "type_route_source": source,
        "longueur_m": longueur, "x": x, "y": y, "bloc_50km": bloc_de(x, y, bloc_km),
    })


def table_sections_annees(brut: pd.DataFrame, complete: pd.DataFrame, attributs: pd.DataFrame) -> pd.DataFrame:
    """Table section × année : séries (series_longues) et attributs fixes de la section, vérifiée."""
    table = series_longues(brut, complete).merge(attributs, on="ide_sectn_trafc", how="inner", validate="many_to_one")
    verifier_cibles(table)
    return table


def verifier_cibles(table: pd.DataFrame) -> None:
    """Lève une erreur si une colonne de cible porte autre chose qu'une valeur publiée par le MTQ."""
    for nom in CIBLES.values():
        cible, mesure, methode = table[f"{nom}_cible"], table[f"{nom}_mesure"], table[f"{nom}_methode"]
        if (cible.notna() != mesure).any():
            raise ValueError(f"{nom}_cible : {int((cible.notna() & ~mesure).sum())} valeur(s) non publiée(s) en cible, "
                             f"{int((cible.isna() & mesure).sum())} valeur(s) publiée(s) absente(s)")
        if ((methode == "mesure") != mesure).any():
            raise ValueError(f"{nom}_methode ne concorde pas avec {nom}_mesure")
    if "camions_cible" in table.columns:
        les_deux = table["djma_mesure"] & table["pct_camions_mesure"]
        if (table["camions_cible"].notna() != les_deux).any():
            raise ValueError("camions_cible : cible hors des lignes où le DJMA et le % de camions sont publiés")


def exemples(table: pd.DataFrame, cible: str) -> pd.DataFrame:
    """Exemples d'apprentissage pour `cible` (« djma », « pct_camions » ou « camions »).

    Ne garde que les lignes dont la cible est publiée, et retire les colonnes qui la répètent ou la
    contiennent : sa valeur complétée (égale à la cible presque partout), ses drapeaux, son nombre d'années
    publiées (toujours ≥ 1 ici, nul là où l'on voudrait prédire), et les camions par jour (produit du DJMA
    et du % de camions). Pour le DJMA et les camions, DJME et DJMH partent aussi : ils sortent de la même
    estimation du ministère, ce sont des cibles sœurs. Les autres colonnes *_complete restent, avec leur
    méthode : à l'utilisateur de décider s'il accepte une variable complétée.
    """
    verifier_cibles(table)
    composantes = {"djma": ["djma"], "pct_camions": ["pct_camions"], "camions": ["djma", "pct_camions"]}
    if cible not in composantes:
        raise ValueError(f"cible inconnue : {cible} (djma, pct_camions ou camions)")
    retirer = [f"{nom}_{suffixe}" for nom in composantes[cible]
               for suffixe in ("complete", "mesure", "methode", "ecrase", "n_mesures")]
    retirer += ["camions_cible"] if cible != "camions" else ["djma_cible", "pct_camions_cible"]
    if "djma" in composantes[cible]:
        retirer += list(SOEURS_DJMA)
    lignes = table[table[f"{cible}_cible"].notna()]
    return lignes.drop(columns=[c for c in retirer if c in lignes.columns]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Graphe RTSS : nœuds, arêtes, comptages qui les longent
# ---------------------------------------------------------------------------

def ecart_jonction(noeuds: gpd.GeoDataFrame, liens: gpd.GeoDataFrame) -> np.ndarray:
    """Plus grande distance (m) entre les extrémités des liens qui aboutissent à chaque nœud (0 si un seul lien).

    Un lien part de son nœud `u` et finit à son nœud `v`. Là où le projet a raccordé une extrémité pendante
    à une autre ligne, les deux extrémités restent écartées de la distance du raccord.
    """
    geoms = liens.geometry.to_crs(CRS_BLOCS).to_numpy()
    debut = shapely.get_coordinates(shapely.get_point(geoms, 0))
    fin = shapely.get_coordinates(shapely.get_point(geoms, -1))
    e = pd.DataFrame({"noeud": np.r_[liens["u"].to_numpy(), liens["v"].to_numpy()],
                      "x": np.r_[debut[:, 0], fin[:, 0]], "y": np.r_[debut[:, 1], fin[:, 1]]})
    p = e.merge(e, on="noeud")
    ecart = pd.Series(np.hypot(p["x_x"] - p["x_y"], p["y_x"] - p["y_y"])).groupby(p["noeud"].to_numpy()).max()
    return ecart.reindex(noeuds["id_noeud"].to_numpy(), fill_value=0.0).to_numpy()


def table_noeuds(noeuds: gpd.GeoDataFrame, liens: gpd.GeoDataFrame, bloc_km: float = BLOC_KM) -> pd.DataFrame:
    """Nœuds du graphe RTSS : position, degré, bloc, et comment la jonction a été établie.

    `jonction_nouee` : le fichier RTSS n'a pas d'extrémité commune à cet endroit, le projet y a noué les
    lignes. Le plus souvent, c'est un carrefour en T (une extrémité posée sur l'intérieur d'une autre ligne :
    le RTSS ne coupe pas ses lignes aux carrefours) ; parfois, une extrémité pendante raccordée à la ligne
    la plus proche. `ecart_jonction_m` les distingue : au-delà de la tolérance de croisement (2 m), la
    jonction est une estimation.
    """
    x, y = centroides(noeuds.geometry)
    return pd.DataFrame({"id_noeud": noeuds["id_noeud"].to_numpy(), "x": x, "y": y,
                         "degre": noeuds["degre"].to_numpy(), "jonction_nouee": noeuds["raccord"].to_numpy(dtype=bool),
                         "ecart_jonction_m": ecart_jonction(noeuds, liens), "bloc_50km": bloc_de(x, y, bloc_km)})


def table_aretes(liens: gpd.GeoDataFrame, bloc_km: float = BLOC_KM) -> pd.DataFrame:
    """Arêtes du graphe RTSS (non orienté). `estime` : tronçon estimé par le projet, absent du RTSS."""
    x, y = centroides(liens.geometry)
    classe = liens["cod_clasf_"].astype(str)
    return pd.DataFrame({
        "id_lien": liens["id_lien"].to_numpy(), "u": liens["u"].to_numpy(), "v": liens["v"].to_numpy(),
        "longueur_m": liens["longueur_m"].to_numpy(), "num_route": liens["num_route"].to_numpy(),
        "classe_rtss": classe.to_numpy(), "type_route": classe.map(TYPES_ROUTE).fillna(AUTRE).to_numpy(),
        "cote_chaussee": liens["cod_cote_c"].to_numpy(), "estime": liens["raccord"].to_numpy(dtype=bool),
        "x": x, "y": y, "bloc_50km": bloc_de(x, y, bloc_km),
    })


def table_aretes_sections(paires: pd.DataFrame, attributs: pd.DataFrame) -> pd.DataFrame:
    """Incidence lien × section : longueur de la section le long du lien, et bloc de la section.

    `paires` : sortie de reseau.sections.paires_section_lien (ide_sectn_trafc, id_lien, longueur_m).
    """
    t = pd.DataFrame({"id_lien": paires["id_lien"].to_numpy(),
                      "ide_sectn_trafc": paires["ide_sectn_trafc"].astype("int64").to_numpy(),
                      "longueur_commune_m": paires["longueur_m"].to_numpy(dtype=float)})
    t = t[t["longueur_commune_m"] > 0]
    t = t.merge(attributs[["ide_sectn_trafc", "bloc_50km"]].rename(columns={"bloc_50km": "bloc_section"}),
                on="ide_sectn_trafc", how="inner", validate="many_to_one")
    return t.sort_values(["id_lien", "ide_sectn_trafc"], ignore_index=True)


def etiquettes_aretes(aretes_sections: pd.DataFrame, sections_annees: pd.DataFrame,
                      sections_gardees: Iterable[int] | None = None) -> pd.DataFrame:
    """Lien × année : valeurs des sections qui longent le lien, en moyenne pondérée par la longueur commune.

    <mesure>_cible          moyenne des seules valeurs publiées ; vide si aucune section du lien n'est publiée
    <mesure>_part_mesuree   part de la longueur couverte dont la valeur est publiée (0 à 1)
    <mesure>_complete       moyenne des valeurs complétées de toutes les sections du lien

    `sections_gardees` : sections autorisées à étiqueter (celles de l'entraînement). Une section mise de
    côté longe plusieurs liens ; sans ce filtre, sa valeur étiquetterait des liens des deux côtés du découpage.
    """
    inc = aretes_sections
    if sections_gardees is not None:
        inc = inc[inc["ide_sectn_trafc"].isin(set(sections_gardees))]
    colonnes = ["ide_sectn_trafc", "annee", "camions_cible"] + [f"{nom}_{s}" for nom in CIBLES.values()
                                                                for s in ("cible", "complete")]
    m = inc.merge(sections_annees[colonnes], on="ide_sectn_trafc")
    poids = m["longueur_commune_m"].to_numpy()
    sommes = pd.DataFrame({"id_lien": m["id_lien"].to_numpy(), "annee": m["annee"].to_numpy(), "poids": poids})
    for nom in (*CIBLES.values(), "camions"):
        cible = m[f"{nom}_cible"].to_numpy()
        sommes[f"{nom}_p"] = np.where(np.isnan(cible), 0.0, poids)
        sommes[f"{nom}_pc"] = np.where(np.isnan(cible), 0.0, poids * cible)
    for nom in CIBLES.values():
        sommes[f"{nom}_pk"] = poids * m[f"{nom}_complete"].to_numpy()
    g = sommes.groupby(["id_lien", "annee"], sort=True).sum()
    resultat = pd.DataFrame(index=g.index)
    resultat["n_sections"] = sommes.groupby(["id_lien", "annee"], sort=True).size()
    for nom in (*CIBLES.values(), "camions"):
        resultat[f"{nom}_cible"] = (g[f"{nom}_pc"] / g[f"{nom}_p"]).where(g[f"{nom}_p"] > 0)
        resultat[f"{nom}_part_mesuree"] = g[f"{nom}_p"] / g["poids"]
        if nom in CIBLES.values():
            resultat[f"{nom}_complete"] = g[f"{nom}_pk"] / g["poids"]
    return resultat.reset_index()
