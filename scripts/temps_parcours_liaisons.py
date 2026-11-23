"""
temps_parcours_liaisons.py
==========================
Projet CIRANO — Module 1 : un temps de parcours sur chaque liaison du graphe, sans refaire le routage.

Demande du directeur de recherche (2026-11-23) : chaque arc du graphe porte un temps, à côté de sa
longueur et de son trafic. Le temps se mesure sur le tracé OSRM de la liaison (étape du routage), avec
les vitesses camion par classe de route du Module 3 (configs/reseau.yaml) : le projet n'a ainsi qu'une
définition du temps. C'est un temps sans congestion ; les vitesses par classe sont une hypothèse.

Les segments RTSS déjà rattachés à chaque arc par le routage (ids_segs_rtss_*) ne conviennent pas : ce
sont tous les segments qui touchent une zone de 1,5 km autour du tracé, routes transversales comprises,
chacun avec sa longueur entière. Le temps se mesure donc tranche par tranche, sur le tracé lui-même.

Pseudo-code
-----------
1. vitesses(cfg)          vitesses camion par classe RTSS, rue municipale (« MU ») et hors Québec (Module 3)
2. charger_rtss(cfg)      sous-routes du RTSS gardées par le Module 3 (statuts actifs, sans rues locales)
3. tranches(traces)       un point au milieu de chaque tranche de 100 m du tracé, avec la longueur de la tranche
4. classer(t, rtss, hq, villes)
                          segment RTSS le plus proche à 50 m ou moins : sa classe ; sinon, hors Québec si la
                          tranche est dans une autre province ou un État américain (Natural Earth 1:50 M) ;
                          sinon, rue municipale (40 km/h) à 8 km ou moins d'une municipalité du graphe (la
                          règle d'accès du Module 3), route hors RTSS (70 km/h, vitesse d'une collectrice, celle
                          que le Module 3 donne à ses liaisons sans RTSS) au-delà : les routes de la Baie-James,
                          hors du RTSS, ne roulent pas à la vitesse d'une rue.
                          Deux règles écartées pour « hors Québec » : celle du routage (« au Québec » = à moins de
                          2 km du RTSS, longueur_qc_km), car au centre de Montréal des rues sont à 1 ou 2 km du
                          RTSS ; et « hors du contour du Québec », car à 1:50 M le fleuve entame la rive à Québec
                          et à Montréal, et des rues y tombaient hors de la province.
5. temps_par_liaison()    temps_min = Σ longueur de la tranche / vitesse de sa classe × 60 ;
                          vitesse moyenne, part d'autoroute, part hors RTSS, part hors Québec
6. controler()            sortie refusée si un tracé n'a pas de temps, si un temps est nul, si une vitesse
                          moyenne sort des vitesses du Module 3, ou si les tranches ne refont pas le tracé (1 %)
7. comparer_module3()     temps de la route la plus rapide du Module 3, selon sa source (RTSS ou repli OSRM)
8. --echantillon          arcs du couloir Montréal–Québec seulement (emprise de configs/reseau.yaml),
                          CSV dans outputs/module1/echantillon/ ; data/processed n'est pas touché
   sinon : ecrire()       4 colonnes ajoutées à arcs_enrichis (graphe_routier.gpkg), fichier temporaire relu
                          et comparé, puis remplacé ; relancer ensuite calcul_djma_methodes.py, qui les
                          reporte dans graphe_routier_djma.gpkg

Entrées : data/processed/graphe_routier.gpkg (couches trace_osrm et arcs_enrichis),
          data/raw/ReseauRoutier_RTSS.gpkg (lecture seule), configs/reseau.yaml, configs/modele.yaml,
          data/reference/ne_50m_admin_1_states_provinces.zip (autres provinces et États),
          data/raw/reseau_arcs.gpkg (couche noeuds : municipalités du graphe, lecture seule),
          data/processed/reseau_multi_routes.gpkg (Module 3, comparaison seulement, facultatif)
Sorties : data/processed/graphe_routier.gpkg (arcs_enrichis + temps_min, vitesse_moy_kmh, part_autoroute,
          part_hors_rtss) ; avec --echantillon : outputs/module1/echantillon/temps_parcours.csv

Usage : ~/.venvs/portfolio/bin/python scripts/temps_parcours_liaisons.py [--echantillon]
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
import yaml
from shapely.geometry import box

RACINE            = Path(__file__).resolve().parent.parent
FICHIER_GRAPHE    = RACINE / "data" / "processed" / "graphe_routier.gpkg"
FICHIER_MODULE3   = RACINE / "data" / "processed" / "reseau_multi_routes.gpkg"
FICHIER_PROVINCES = RACINE / "data" / "reference" / "ne_50m_admin_1_states_provinces.zip"
CONFIG_RESEAU     = RACINE / "configs" / "reseau.yaml"
CONFIG_MODELE     = RACINE / "configs" / "modele.yaml"
DIR_ECHANTILLON   = RACINE / "outputs" / "module1" / "echantillon"
COUCHES           = ("arcs_enrichis", "trajets_segments", "trace_osrm")
CRS_QC            = "EPSG:32198"
PAS_M             = 100.0     # longueur d'une tranche de tracé
TOLERANCE_RTSS_M  = 50.0      # une tranche roule sur le segment RTSS le plus proche s'il est à 50 m ou moins
CODE_AUTOROUTE    = "10"
CODE_COLLECTRICE  = "40"      # une route hors RTSS loin des villes roule à la vitesse d'une collectrice
MUNICIPAL, RURAL, HORS_QC = "MU", "RU", "HQ"
COLONNES          = ["temps_min", "vitesse_moy_kmh", "part_autoroute", "part_hors_rtss"]


def config_reseau() -> dict:
    """Configuration du réseau du Module 3 (vitesses, RTSS, échantillon)."""
    return yaml.safe_load(CONFIG_RESEAU.read_text(encoding="utf-8"))


def vitesses(cfg: dict) -> tuple[dict[str, float], dict[str, float]]:
    """Vitesses camion du Module 3 (km/h) : par code de classe RTSS, et hors RTSS (rue municipale,
    route loin des villes, hors Québec)."""
    v = {str(k): float(x) for k, x in cfg["graphe"]["vitesses_kmh"].items()}
    municipal = v.pop(MUNICIPAL)
    modele = yaml.safe_load(CONFIG_MODELE.read_text(encoding="utf-8"))
    hors_rtss = {MUNICIPAL: municipal, RURAL: v[CODE_COLLECTRICE],
                 HORS_QC: float(modele["chemins"]["vitesse_hors_qc_kmh"])}
    return v, hors_rtss


def charger_villes(cfg: dict) -> gpd.GeoDataFrame:
    """Municipalités du graphe des liaisons (couche noeuds, en EPSG:32198 sans CRS déclaré)."""
    noeuds = gpd.read_file(RACINE / cfg["entrees"]["liaisons"], layer="noeuds")
    noeuds = noeuds.set_crs(CRS_QC, allow_override=True)
    return noeuds.loc[noeuds["TYPE"] == "municipalite", ["geometry"]].reset_index(drop=True)


def charger_rtss(cfg: dict) -> gpd.GeoDataFrame:
    """Sous-routes du RTSS gardées par le Module 3 (mêmes statuts, sans rues locales), en EPSG:32198."""
    rtss = gpd.read_file(RACINE / cfg["entrees"]["rtss"], columns=["sta_sous_r", "cod_clasf_"])
    garde = (rtss["sta_sous_r"].isin(cfg["graphe"]["statuts_gardes"])
             & ~rtss["cod_clasf_"].isin(cfg["graphe"]["classes_exclues"]))
    rtss = rtss[garde].to_crs(CRS_QC).explode(index_parts=False)
    rtss = rtss[~rtss.geometry.is_empty & (rtss.geometry.length > 0)]
    return rtss[["cod_clasf_", "geometry"]].reset_index(drop=True)


def dans_emprise(traces: gpd.GeoDataFrame, emprise: dict) -> gpd.GeoDataFrame:
    """Tracés entièrement compris dans l'emprise lon/lat de l'échantillon."""
    cadre = box(emprise["lon_min"], emprise["lat_min"], emprise["lon_max"], emprise["lat_max"])
    return traces[traces.to_crs("EPSG:4326").geometry.within(cadre).to_numpy()].copy()


def tranches(traces: gpd.GeoDataFrame, pas_m: float = PAS_M) -> gpd.GeoDataFrame:
    """Découpe chaque tracé en tranches de `pas_m` (la dernière plus courte) : un point au milieu de chacune."""
    morceaux = []
    for id_arc, geom in zip(traces["ID_ARC"], traces.geometry, strict=True):
        if geom is None or geom.is_empty or geom.length == 0:
            continue
        debuts = np.arange(0.0, geom.length, pas_m)
        longueurs = np.minimum(pas_m, geom.length - debuts)
        morceaux.append(pd.DataFrame({
            "ID_ARC": id_arc, "longueur_m": longueurs,
            "geometry": shapely.line_interpolate_point(geom, debuts + longueurs / 2),
        }))
    if not morceaux:
        return gpd.GeoDataFrame({"ID_ARC": [], "longueur_m": []}, geometry=[], crs=traces.crs)
    return gpd.GeoDataFrame(pd.concat(morceaux, ignore_index=True), geometry="geometry", crs=traces.crs)


def hors_quebec() -> shapely.Geometry:
    """Les autres provinces canadiennes et les États américains (Natural Earth 1:50 M), en EPSG:32198."""
    admin = gpd.read_file(f"zip://{FICHIER_PROVINCES}", columns=["name", "adm0_a3"])
    quebec = admin["name"].str.contains("Qu.bec", regex=True)
    if not quebec.any():
        raise ValueError(f"{FICHIER_PROVINCES.name} : le Québec est introuvable, la couche n'est pas celle attendue")
    autres = admin[admin["adm0_a3"].isin(["CAN", "USA"]) & ~quebec]
    return autres.to_crs(CRS_QC).geometry.union_all()


def classer(t: gpd.GeoDataFrame, rtss: gpd.GeoDataFrame, ailleurs: shapely.Geometry,
            villes: gpd.GeoDataFrame, rayon_ville_m: float) -> pd.Series:
    """Classe de chaque tranche : code RTSS du segment le plus proche (≤ 50 m) ; sinon « HQ » dans une autre
    province ou un État, « MU » (rue municipale) près d'une municipalité, « RU » (route hors RTSS) au-delà."""
    j = gpd.sjoin_nearest(t[["geometry"]], rtss, how="left", max_distance=TOLERANCE_RTSS_M, distance_col="dist_m")
    j = j[~j.index.duplicated(keep="first")].reindex(t.index)       # égalité de distance : un seul segment
    sur_rtss = j["dist_m"].notna().to_numpy()
    shapely.prepare(ailleurs)
    hors = shapely.contains(ailleurs, t.geometry.to_numpy())
    v = gpd.sjoin_nearest(t[["geometry"]], villes, how="left", max_distance=rayon_ville_m, distance_col="dist_m")
    pres_ville = v[~v.index.duplicated(keep="first")].reindex(t.index)["dist_m"].notna().to_numpy()
    classe = np.where(sur_rtss, j["cod_clasf_"].astype(str).to_numpy(),
                      np.where(hors, HORS_QC, np.where(pres_ville, MUNICIPAL, RURAL)))
    return pd.Series(classe, index=t.index, name="classe")


def temps_par_liaison(t: gpd.GeoDataFrame, classe: pd.Series, v_classe: dict[str, float],
                      v_hors_rtss: dict[str, float]) -> pd.DataFrame:
    """Temps de parcours de chaque liaison : Σ longueur de la tranche / vitesse de sa classe, et ses parts."""
    toutes = {**v_classe, **v_hors_rtss}
    # Code de classe sans vitesse : la plus basse, comme le Module 3 (fillna(min) dans construire_graphe)
    vit = classe.map(toutes).fillna(min(toutes.values())).to_numpy(dtype=float)
    d = pd.DataFrame({"ID_ARC": t["ID_ARC"].to_numpy(), "km": t["longueur_m"].to_numpy(dtype=float) / 1000,
                      "classe": classe.to_numpy()})
    d["min"] = d["km"] / vit * 60
    part = lambda masque: d["km"].where(masque, 0.0).groupby(d["ID_ARC"]).sum()  # noqa: E731
    km = d.groupby("ID_ARC")["km"].sum()
    res = pd.DataFrame({
        "km_tranches": km,
        "temps_min": d.groupby("ID_ARC")["min"].sum(),
        "part_autoroute": part(d["classe"] == CODE_AUTOROUTE) / km,
        "part_hors_rtss": part(d["classe"].isin([MUNICIPAL, RURAL, HORS_QC])) / km,
        "part_hors_qc": part(d["classe"] == HORS_QC) / km,
    })
    res["vitesse_moy_kmh"] = res["km_tranches"] / (res["temps_min"] / 60)
    return res


def controler(res: pd.DataFrame, traces: gpd.GeoDataFrame, v_min: float, v_max: float) -> None:
    """Refuse la sortie (exception, rien n'est écrit) si une règle est violée."""
    erreurs = []
    manquants = sorted(set(traces["ID_ARC"]) - set(res.index))
    if manquants:
        erreurs.append(f"{len(manquants)} tracés sans temps : {manquants[:10]}")
    if not np.isfinite(res["temps_min"]).all() or (res["temps_min"] <= 0).any():
        erreurs.append("temps nul, négatif ou non fini")
    hors = res[(res["vitesse_moy_kmh"] < v_min - 1e-9) | (res["vitesse_moy_kmh"] > v_max + 1e-9)]
    if len(hors):
        erreurs.append(f"{len(hors)} vitesses moyennes hors de [{v_min:g}, {v_max:g}] km/h")
    longueur = (traces.set_index("ID_ARC").geometry.length / 1000).reindex(res.index)
    ecart = ((res["km_tranches"] - longueur).abs() / longueur).fillna(np.inf)
    if (ecart > 0.01).any():
        erreurs.append(f"{int((ecart > 0.01).sum())} liaisons : les tranches ne refont pas le tracé (écart > 1 %)")
    if erreurs:
        raise ValueError("contrôles échoués, rien n'est écrit :\n  - " + "\n  - ".join(erreurs))


def comparer_module3(res: pd.DataFrame) -> pd.DataFrame | None:
    """Temps de la route la plus rapide du Module 3 pour chaque liaison (None si son réseau est absent)."""
    if not FICHIER_MODULE3.exists():
        return None
    r = gpd.read_file(FICHIER_MODULE3, layer="routes", ignore_geometry=True,
                      columns=["ID_ARC", "rang", "source", "minutes"])
    r = r[r["rang"] == 1].set_index("ID_ARC")[["minutes", "source"]]
    c = res.join(r.rename(columns={"minutes": "min_module3", "source": "source_module3"}), how="left")
    c["rapport"] = c["temps_min"] / c["min_module3"]
    return c


def reparer(texte: object) -> object:
    """Nom lu en Latin-1 alors qu'il est en UTF-8 dans la source (« QuÃ©bec ») : réparé pour l'affichage."""
    if not isinstance(texte, str):
        return texte
    try:
        return texte.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return texte


def resumer(res: pd.DataFrame, t: gpd.GeoDataFrame, classe: pd.Series, comp: pd.DataFrame | None) -> None:
    """Affiche la distribution des temps, la composition par classe et la comparaison avec le Module 3."""
    q = res["temps_min"].quantile([0.1, 0.5, 0.9])
    print(f"  {len(res)} liaisons · temps : médiane {q[0.5]:.1f} min (10e centile {q[0.1]:.1f}, 90e {q[0.9]:.1f}), "
          f"max {res['temps_min'].max():.0f} min")
    print(f"  vitesse moyenne : médiane {res['vitesse_moy_kmh'].median():.1f} km/h "
          f"(min {res['vitesse_moy_kmh'].min():.1f}, max {res['vitesse_moy_kmh'].max():.1f})")
    km = pd.Series(t["longueur_m"].to_numpy() / 1000).groupby(classe.to_numpy()).sum()
    parts = " · ".join(f"{c} {v / km.sum():.1%}" for c, v in km.sort_values(ascending=False).items())
    print(f"  longueur des tracés par classe : {parts}")
    if comp is None:
        print("  Module 3 : reseau_multi_routes.gpkg absent, pas de comparaison")
        return
    for source, g in comp.dropna(subset=["min_module3"]).groupby("source_module3"):
        r = g["rapport"]
        corr = np.corrcoef(np.log(g["temps_min"]), np.log(g["min_module3"]))[0, 1] if len(g) > 2 else np.nan
        print(f"  Module 3, route la plus rapide ({source}, {len(g)} liaisons) : rapport Module 1 / Module 3 "
              f"médian {r.median():.2f} ; à ±10 % : {r.between(0.9, 1.1).mean():.0%} ; corrélation des log {corr:.3f}")


def ecrire(couches: dict[str, gpd.GeoDataFrame], cible: Path) -> None:
    """Écrit les couches dans un fichier temporaire, le relit et le compare, puis remplace la cible."""
    tmp = cible.with_name(cible.stem + ".tmp.gpkg")
    if tmp.exists():
        tmp.unlink()
    for nom in COUCHES:
        couches[nom].to_file(tmp, layer=nom, driver="GPKG")
    for nom in COUCHES:
        relu, ecrit = gpd.read_file(tmp, layer=nom), couches[nom]
        if list(relu.columns) != list(ecrit.columns) or len(relu) != len(ecrit):
            raise ValueError(f"{nom} : relecture différente (colonnes ou lignes) ; {tmp.name} gardé pour examen")
        if not relu.geometry.geom_equals_exact(ecrit.geometry, tolerance=0).all():
            raise ValueError(f"{nom} : géométries modifiées à l'écriture ; {tmp.name} gardé pour examen")
    os.replace(tmp, cible)


def main() -> None:
    """Calcule le temps de parcours de chaque liaison et l'écrit dans le graphe (ou un CSV d'échantillon)."""
    parser = argparse.ArgumentParser(description="Temps de parcours de chaque liaison (vitesses camion du Module 3).")
    parser.add_argument("--echantillon", action="store_true",
                        help="couloir Montréal–Québec seulement ; CSV dans outputs/module1/echantillon/")
    args = parser.parse_args()

    cfg = config_reseau()
    v_classe, v_hors_rtss = vitesses(cfg)
    toutes = [*v_classe.values(), *v_hors_rtss.values()]
    rayon_ville_m = float(cfg["acces"]["rayon_km"]) * 1000
    print("Vitesses (km/h) : " + ", ".join(f"{k} {v:g}" for k, v in v_classe.items())
          + f", rue municipale {v_hors_rtss[MUNICIPAL]:g} (à {rayon_ville_m / 1000:g} km ou moins d'une municipalité),"
          f" route hors RTSS {v_hors_rtss[RURAL]:g}, hors Québec {v_hors_rtss[HORS_QC]:g}")

    print("[1/5] tracés et RTSS")
    traces = gpd.read_file(FICHIER_GRAPHE, layer="trace_osrm")
    if args.echantillon:
        traces = dans_emprise(traces, cfg["echantillon"])
        print(f"  échantillon « {cfg['echantillon']['nom']} » : {len(traces)} liaisons")
    rtss = charger_rtss(cfg)
    print(f"  {len(traces)} tracés, {len(rtss)} segments RTSS")

    print(f"[2/5] tranches de {PAS_M:g} m et classe de chacune")
    t = tranches(traces)
    classe = classer(t, rtss, hors_quebec(), charger_villes(cfg), rayon_ville_m)
    print(f"  {len(t)} tranches")

    print("[3/5] temps par liaison et contrôles")
    res = temps_par_liaison(t, classe, v_classe, v_hors_rtss)
    controler(res, traces, min(toutes), max(toutes))
    print("  contrôles passés")

    print("[4/5] résumé et comparaison avec le Module 3")
    comp = comparer_module3(res)
    resumer(res, t, classe, comp)

    print("[5/5] écriture")
    if args.echantillon:
        noms = traces.set_index("ID_ARC")[["VILLE_A", "VILLE_B"]].map(reparer)
        sortie = noms.join(comp if comp is not None else res).reset_index()
        DIR_ECHANTILLON.mkdir(parents=True, exist_ok=True)
        cible = DIR_ECHANTILLON / "temps_parcours.csv"
        sortie.round(3).to_csv(cible, index=False, encoding="utf-8")
        print(f"→ {cible.relative_to(RACINE)} (data/processed n'est pas touché)")
        return
    couches = {nom: gpd.read_file(FICHIER_GRAPHE, layer=nom) for nom in COUCHES}
    arcs = couches["arcs_enrichis"].drop(columns=[c for c in COLONNES if c in couches["arcs_enrichis"].columns])
    valeurs = res[COLONNES].round({"temps_min": 1, "vitesse_moy_kmh": 1, "part_autoroute": 3, "part_hors_rtss": 3})
    arcs = arcs.join(valeurs, on="ID_ARC")
    arcs = arcs[[c for c in arcs.columns if c != "geometry"] + ["geometry"]]
    ecrire({**couches, "arcs_enrichis": arcs}, FICHIER_GRAPHE)
    print(f"→ {FICHIER_GRAPHE.relative_to(RACINE)} : {', '.join(COLONNES)} ajoutées à arcs_enrichis")
    print("  Relancer calcul_djma_methodes.py pour les reporter dans graphe_routier_djma.gpkg.")


if __name__ == "__main__":
    main()
