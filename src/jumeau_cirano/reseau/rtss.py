"""
rtss.py
=======
Réseau routier RTSS du MTQ → graphe routable, topologie réparée.

Le RTSS décrit le réseau supérieur ligne par ligne, mais ses lignes ne sont pas
nouées : une route qui en rejoint une autre s'arrête souvent à quelques mètres
d'une extrémité, ou au milieu d'une ligne (jonction en T). Réparation :

1. extrémités à moins de `tolerance_noeud_m` les unes des autres → un même nœud ;
2. extrémité posée à moins de `tolerance_croisement_m` de l'intérieur d'une autre
   ligne → carrefour : cette ligne est coupée là (cas du carrefour en croix, où
   la route qui s'arrête des deux côtés n'a pourtant aucune extrémité pendante) ;
3. extrémité restée pendante → raccordée à la ligne la plus proche à moins de
   `tolerance_jonction_m` ; cette ligne est coupée au point de raccord ;
4. un croisement sans extrémité (pont d'étagement) ne crée PAS de nœud.

Pseudo-code
-----------
charger_rtss()       statuts et classes gardés, projection, emprise optionnelle
noder()              grappes d'extrémités → raccord des pendantes → découpe des lignes
construire_graphe()  MultiGraph : km, minutes (vitesse camion par classe), classe, route
composante_principale()  plus grande composante connexe
rattacher()          point → nœud du graphe le plus proche, avec la distance
"""

from __future__ import annotations

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
import shapely
from scipy.spatial import cKDTree
from shapely.ops import substring

from jumeau_cirano.config import ConfigReseau, Emprise, chemin_absolu

# Attributs RTSS recopiés sur chaque lien du graphe
ATTRIBUTS = ["num_rts", "num_route", "cod_clasf_", "des_clasf_", "cod_sous_r", "cod_cote_c", "nom_sous_r"]


class UnionFind:
    """Union-find à compression de chemins, sur des entiers 0..n-1."""

    def __init__(self, n: int) -> None:
        self.parent = np.arange(n)

    def racine(self, i: int) -> int:
        p = self.parent
        r = i
        while p[r] != r:
            r = p[r]
        while p[i] != r:
            p[i], i = r, p[i]
        return r

    def unir(self, a: int, b: int) -> None:
        ra, rb = self.racine(a), self.racine(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)

    def racines(self) -> np.ndarray:
        return np.array([self.racine(i) for i in range(len(self.parent))])


def charger_rtss(cfg: ConfigReseau, emprise: Emprise | None = None) -> gpd.GeoDataFrame:
    """Sous-routes actives du RTSS, sans les rues locales, projetées (option : emprise lon/lat)."""
    rtss = gpd.read_file(chemin_absolu(cfg.entrees.rtss), columns=ATTRIBUTS + ["sta_sous_r"])
    garde = (rtss["sta_sous_r"].isin(cfg.graphe.statuts_gardes)
             & ~rtss["cod_clasf_"].isin(cfg.graphe.classes_exclues))
    rtss = rtss[garde]
    if emprise is not None:
        rtss = rtss.cx[emprise.lon_min:emprise.lon_max, emprise.lat_min:emprise.lat_max]
    rtss = rtss.to_crs(cfg.crs).explode(index_parts=False)
    rtss = rtss[~rtss.geometry.is_empty & (rtss.geometry.length > 0)]
    return rtss[ATTRIBUTS + ["geometry"]].reset_index(drop=True)


def noder(
    lignes: gpd.GeoDataFrame, tol_noeud: float, tol_jonction: float, tol_croisement: float = 2.0
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Répare la topologie : renvoie (liens, nœuds).

    `liens` garde les attributs de sa ligne d'origine, plus `u`, `v` (nœuds) et `longueur_m`.
    `nœuds` a une colonne `degre` et `raccord` (True si le nœud vient d'un raccord de pendante).
    """
    geoms = lignes.geometry.to_numpy()
    n = len(geoms)
    ext = np.concatenate([shapely.get_point(geoms, 0), shapely.get_point(geoms, -1)])
    xy = shapely.get_coordinates(ext)
    proprio = np.concatenate([np.arange(n), np.arange(n)])

    # 1. Grappes d'extrémités confondues
    uf_ext = UnionFind(2 * n)
    for a, b in cKDTree(xy).query_pairs(tol_noeud, output_type="ndarray"):
        uf_ext.unir(int(a), int(b))
    grappe = uf_ext.racines()
    taille = np.bincount(grappe, minlength=2 * n)[grappe]
    pendantes = np.flatnonzero(taille == 1)

    arbre = shapely.STRtree(geoms)
    longueurs = shapely.length(geoms)

    # 2. Carrefours : toute extrémité posée sur l'intérieur d'une autre ligne (toutes les lignes visées)
    ip, il = arbre.query(ext, predicate="dwithin", distance=tol_croisement)
    autre = il != proprio[ip]
    ip, il = ip[autre], il[autre]
    s = shapely.line_locate_point(geoms[il], ext[ip])
    interieur = (s > tol_noeud) & (s < longueurs[il] - tol_noeud)
    croisements = pd.DataFrame({"ext": ip[interieur], "ligne": il[interieur]})

    # 3. Raccord de chaque pendante à la ligne la plus proche (hors sa propre ligne)
    ip, il = arbre.query(ext[pendantes], predicate="dwithin", distance=tol_jonction)
    autre = il != proprio[pendantes[ip]]
    ip, il = ip[autre], il[autre]
    d = shapely.distance(ext[pendantes[ip]], geoms[il])
    ordre = np.lexsort((d, ip))
    ip, il = ip[ordre], il[ordre]
    premier = np.ones(len(ip), dtype=bool)
    premier[1:] = ip[1:] != ip[:-1]
    pendants = pd.DataFrame({"ext": pendantes[ip[premier]], "ligne": il[premier]})
    raccords = pd.concat([croisements, pendants], ignore_index=True).drop_duplicates(["ext", "ligne"])
    raccords["s"] = shapely.line_locate_point(geoms[raccords["ligne"]], ext[raccords["ext"]])

    # Points de coupe par ligne (en mètres depuis son début) ; près d'une extrémité = pas de coupe
    l_cible = longueurs[raccords["ligne"]]
    raccords["vers_debut"] = raccords["s"] <= tol_noeud
    raccords["vers_fin"] = raccords["s"] >= l_cible - tol_noeud
    coupes: dict[int, list[float]] = {}
    for r in raccords[~(raccords.vers_debut | raccords.vers_fin)].itertuples():
        coupes.setdefault(int(r.ligne), []).append(float(r.s))

    # 3. Découpe : chaque ligne → pièces ; un identifiant par point de pièce
    pieces, points_xy, ligne_de_piece = [], [], []
    id_point_coupe: dict[tuple[int, float], int] = {}
    prochain = 2 * n                                   # 0..2n-1 = extrémités d'origine
    bornes: list[tuple[int, int]] = []
    for i in range(n):
        s_list = sorted(set(round(s, 3) for s in coupes.get(i, [])))
        positions = [0.0, *s_list, float(longueurs[i])]
        ids = [i]
        for s in s_list:
            id_point_coupe[(i, s)] = prochain
            points_xy.append(shapely.get_coordinates(shapely.line_interpolate_point(geoms[i], s))[0])
            ids.append(prochain)
            prochain += 1
        ids.append(n + i)
        for k in range(len(positions) - 1):
            entiere = len(positions) == 2
            pieces.append(geoms[i] if entiere else substring(geoms[i], positions[k], positions[k + 1]))
            ligne_de_piece.append(i)
            bornes.append((ids[k], ids[k + 1]))

    # 4. Nœuds : grappes d'extrémités + raccords (pendante ↔ point de coupe ou extrémité visée)
    uf = UnionFind(prochain)
    for a, b in enumerate(grappe):
        uf.unir(a, int(b))
    for r in raccords.itertuples():
        if r.vers_debut:
            cible = int(r.ligne)
        elif r.vers_fin:
            cible = n + int(r.ligne)
        else:
            cible = id_point_coupe[(int(r.ligne), round(float(r.s), 3))]
        uf.unir(int(r.ext), cible)
    racine = uf.racines()
    tous_xy = np.vstack([xy, np.array(points_xy).reshape(-1, 2)])
    noeuds_id, inverse = np.unique(racine, return_inverse=True)
    coords = np.zeros((len(noeuds_id), 2))
    np.add.at(coords, inverse, tous_xy)
    coords /= np.bincount(inverse)[:, None]
    est_raccord = np.zeros(len(noeuds_id), dtype=bool)
    est_raccord[inverse[raccords["ext"].to_numpy()]] = True

    liens = lignes.iloc[ligne_de_piece].drop(columns="geometry").reset_index(drop=True)
    liens["u"] = [int(inverse[a]) for a, _ in bornes]
    liens["v"] = [int(inverse[b]) for _, b in bornes]
    liens = gpd.GeoDataFrame(liens, geometry=pieces, crs=lignes.crs)
    liens["longueur_m"] = liens.geometry.length
    liens = liens[(liens["u"] != liens["v"]) & (liens["longueur_m"] > 0)].reset_index(drop=True)
    liens["id_lien"] = np.arange(len(liens))

    degre = np.bincount(np.r_[liens["u"], liens["v"]], minlength=len(noeuds_id))
    noeuds = gpd.GeoDataFrame({"id_noeud": np.arange(len(noeuds_id)), "degre": degre, "raccord": est_raccord},
                              geometry=shapely.points(coords), crs=lignes.crs)
    return liens, noeuds


def raccorder_traversees(
    liens: gpd.GeoDataFrame, noeuds: gpd.GeoDataFrame, max_km: float, facteur_detour: float
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Relie les morceaux d'une même route numérotée que le RTSS laisse séparés.

    Le RTSS ne décrit que le réseau géré par le MTQ : une route numérotée qui
    traverse une ville (ou un plan d'eau, par traversier) en sort sur quelques
    kilomètres. Pour chaque numéro de route, on cherche ses morceaux (composantes
    connexes de ses seuls liens) ; une « fin de route » est un nœud où ce numéro
    n'a qu'un lien. Deux fins de morceaux DIFFÉRENTS, mutuellement les plus
    proches à moins de `max_km`, sont reliées par un raccord : ligne droite de
    classe « MU », longueur = `facteur_detour` × vol d'oiseau, sans section de
    comptage. Deux chaussées d'une même route, déjà reliées entre elles, ne sont
    donc jamais raccordées l'une à l'autre.
    """
    liens = liens.assign(raccord=False)
    xy = shapely.get_coordinates(noeuds.set_index("id_noeud").geometry)
    bouts = pd.concat([liens[["u", "num_route"]].rename(columns={"u": "noeud"}),
                       liens[["v", "num_route"]].rename(columns={"v": "noeud"})])
    par_route = bouts.groupby(["noeud", "num_route"])["noeud"].transform("size")
    bouts = bouts[par_route == 1].drop_duplicates().reset_index(drop=True)

    nouveaux = []
    for route, grp in bouts.groupby("num_route"):
        if len(grp) < 2:
            continue
        sous = nx.Graph()
        sous.add_edges_from(liens.loc[liens["num_route"] == route, ["u", "v"]].itertuples(index=False))
        morceau = {n: i for i, c in enumerate(nx.connected_components(sous)) for n in c}
        noeud = grp["noeud"].to_numpy()
        comp = np.array([morceau[n] for n in noeud])
        k = min(len(grp), 16)
        d, j = cKDTree(xy[noeud]).query(xy[noeud], k=k, distance_upper_bound=max_km * 1000)
        voisin = np.full(len(grp), -1)
        dist = np.full(len(grp), np.inf)
        for a in range(len(grp)):
            for c in range(1, k):
                if np.isfinite(d[a, c]) and comp[j[a, c]] != comp[a]:
                    voisin[a], dist[a] = j[a, c], d[a, c]
                    break
        for a in range(len(grp)):
            b = int(voisin[a])
            if b < 0 or voisin[b] != a or a > b:          # mutuellement plus proches, une seule fois
                continue
            na, nb = int(noeud[a]), int(noeud[b])
            nouveaux.append({"num_rts": "", "num_route": route, "cod_clasf_": "MU",
                             "des_clasf_": "Raccord municipal (estimé)", "cod_sous_r": "", "cod_cote_c": "",
                             "nom_sous_r": "raccord municipal", "u": na, "v": nb, "raccord": True,
                             "longueur_m": float(dist[a]) * facteur_detour,
                             "geometry": shapely.LineString([xy[na], xy[nb]])})
    if not nouveaux:
        return liens, noeuds
    ajout = gpd.GeoDataFrame(nouveaux, geometry="geometry", crs=liens.crs)
    ajout["id_lien"] = np.arange(len(ajout)) + int(liens["id_lien"].max()) + 1
    liens = pd.concat([liens, ajout], ignore_index=True)
    noeuds = noeuds.assign(degre=np.bincount(np.r_[liens["u"], liens["v"]], minlength=len(noeuds)))
    return liens, noeuds


def construire_graphe(
    liens: gpd.GeoDataFrame, noeuds: gpd.GeoDataFrame, vitesses_kmh: dict[str, float]
) -> nx.MultiGraph:
    """MultiGraph non orienté : une arête par lien, avec km, minutes, classe et numéro de route."""
    vitesse = liens["cod_clasf_"].map(vitesses_kmh).fillna(min(vitesses_kmh.values())).to_numpy()
    km = liens["longueur_m"].to_numpy() / 1000
    g = nx.MultiGraph()
    xy = shapely.get_coordinates(noeuds.geometry)
    ids = noeuds["id_noeud"]
    g.add_nodes_from((int(i), {"x": float(x), "y": float(y)}) for i, (x, y) in zip(ids, xy, strict=True))
    for r, k, m in zip(liens.itertuples(), km, km / vitesse * 60, strict=True):
        g.add_edge(int(r.u), int(r.v), key=int(r.id_lien), km=float(k), minutes=float(m),
                   classe=str(r.cod_clasf_), route=str(r.num_route))
    return g


def composante_principale(g: nx.MultiGraph) -> nx.MultiGraph:
    """Plus grande composante connexe (copie)."""
    return g.subgraph(max(nx.connected_components(g), key=len)).copy()


def noeud_acces(id_liaison: str) -> str:
    """Nom, dans le graphe RTSS, du nœud d'accès d'un nœud du graphe des liaisons."""
    return f"L:{id_liaison}"


def ajouter_acces(
    g: nx.MultiGraph, ids: list[str], points_xy: np.ndarray, k: int, rayon_km: float,
    facteur_detour: float, vitesse_kmh: float,
) -> pd.DataFrame:
    """Connecteurs d'accès (« centroid connectors ») : chaque nœud des liaisons est relié aux
    nœuds RTSS les plus proches, un par numéro de route, jusqu'à `k` routes dans `rayon_km`.

    Le RTSS ignore les rues municipales : une ville rejoint le réseau supérieur par l'une
    de ses entrées, et c'est le plus court chemin qui choisit laquelle. Modifie `g` ;
    renvoie une ligne par connecteur (id, nœud RTSS, distance en km).
    """
    rtss = np.array([n for n in g.nodes if not isinstance(n, str)])
    xy = np.array([(g.nodes[n]["x"], g.nodes[n]["y"]) for n in rtss])
    routes = [{d["route"] for _, _, d in g.edges(int(n), data=True)} for n in rtss]
    arbre = cKDTree(xy)
    lignes = []
    for id_l, (x, y) in zip(ids, points_xy, strict=True):
        a = noeud_acces(id_l)
        g.add_node(a, x=float(x), y=float(y), acces=True)
        proches = arbre.query_ball_point((x, y), rayon_km * 1000)
        if not proches:                                   # aucun nœud dans le rayon : le plus proche
            proches = [int(arbre.query((x, y))[1])]
        proches = sorted(proches, key=lambda j: np.hypot(*(xy[j] - (x, y))))
        vues: set[str] = set()
        for j in proches:
            if routes[j] <= vues:
                continue
            vues |= routes[j]
            km = float(np.hypot(*(xy[j] - (x, y)))) / 1000
            g.add_edge(a, int(rtss[j]), key=f"acces:{id_l}:{int(rtss[j])}", km=km * facteur_detour,
                       minutes=km * facteur_detour / vitesse_kmh * 60, classe="AC", route="")
            lignes.append({"ID": id_l, "noeud_rtss": int(rtss[j]), "dist_km": km})
            if len(vues) >= k:
                break
    return pd.DataFrame(lignes)


def poids_sans_transit(origine: str, destination: str, attribut: str):
    """Fonction de poids nx : interdit de traverser les nœuds d'accès autres que l'origine et la destination.

    Sans elle, un chemin pourrait entrer dans une ville par un connecteur et ressortir par
    un autre, comme un raccourci à travers ses rues.
    """
    permis = {origine, destination}

    def poids(u, v, aretes: dict) -> float | None:
        if (isinstance(u, str) and u not in permis) or (isinstance(v, str) and v not in permis):
            return None
        return min(d[attribut] for d in aretes.values())

    return poids


def rattacher(points_xy: np.ndarray, g: nx.MultiGraph) -> tuple[np.ndarray, np.ndarray]:
    """Nœud du graphe le plus proche de chaque point (mêmes coordonnées projetées) et distance en m."""
    ids = np.array(list(g.nodes))
    xy = np.array([(g.nodes[i]["x"], g.nodes[i]["y"]) for i in ids])
    d, j = cKDTree(xy).query(points_xy)
    return ids[j], d
