"""
visualisation_demo.py
=====================
Version de présentation du jumeau numérique : une page courte pour un public universitaire
non spécialisé. La version technique (docs/jumeau/) garde tout le détail.

Pseudo-code
-----------
1. publiques()    lit le JSON intégré à docs/jumeau/index.html : seule entrée, déjà publiée
2. nom_lisible()  « Noeud_Drum » → « Carrefour de Drummondville »
3. reduire()      garde ce que la page affiche, et rien d'autre (liste blanche) : tracés, camions
                  par tracé, fermeture de chaque liaison ramenée à un jour, erreur de validation
4. exporter()     gabarit scripts/gabarits/demo.html + thème + moteur Canvas + données

Aucune donnée nouvelle n'est publiée : ce script ne lit ni outputs/, ni l'artefact, ni l'ENR.
La page se regénère donc après la version technique.

Sortie : docs/demo/index.html
Usage : uv run python scripts/visualisation_demo.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from theme_pages import injecter

RACINE = Path(__file__).resolve().parent.parent
DIR_GABARITS = RACINE / "scripts" / "gabarits"
ENTREE = RACINE / "docs" / "jumeau" / "index.html"
SORTIE = RACINE / "docs" / "demo" / "index.html"

JOURS_PAR_AN = 365
SEUIL_DETOUR = 5.0                  # camions par jour : en deçà, l'écart n'est pas transmis à la page
EXEMPLE = ("Saint-Hyacinthe", "Drummondville")
MODELE_VALIDE = "modèle calé"       # ligne de la validation croisée (calibration_<objectif>.json)
ETIQUETTES = {"Montréal", "Québec", "Gatineau", "Sherbrooke", "Trois-Rivières", "Saguenay", "Rimouski", "Rouyn-Noranda",
              "Sept-Îles", "Baie-Comeau", "Gaspé", "Drummondville", "Chibougamau", "Rivière-du-Loup",
              "Ontario", "Ontario-Sud", "Nouveau-Brunswick", "Maine", "Terre-Neuve-et-Labrador"}
NOMS_AFFICHES = {
    "Noeud_Nord": "Carrefour de Val-d'Or",
    "Noeud_Sud": "Carrefour Mont-Laurier–Maniwaki",
    "Noeud_Sorel": "Carrefour vers Sorel-Tracy",
    "Noeud_Richelieu": "Carrefour de Richelieu",
    "Noeud_Baie_James": "Carrefour de la Baie-James",
    "Noeud_LacStJean": "Carrefour du Lac-Saint-Jean",
    "Noeud_Quebec_Sud": "Carrefour de Québec (sud)",
    "Noeud_Drum": "Carrefour de Drummondville",
    "Noeud_Thetford": "Carrefour de Thetford",
    "Noeud_MTL_Ouest": "Carrefour de Montréal (ouest)",
    "Noeud_Granby": "Carrefour de Granby",
    "Ile-du-Prince-Edouard": "Île-du-Prince-Édouard",
    "Nouvelle-Ecosse": "Nouvelle-Écosse",
}


def publiques(chemin: Path = ENTREE) -> dict:
    """Données déjà publiées, lues dans la page de la version technique."""
    html = chemin.read_text(encoding="utf-8")
    bloc = re.search(r'<script id="donnees" type="application/json">(.*?)</script>', html, re.S)
    if bloc is None:
        raise ValueError(f"données introuvables dans {chemin}")
    donnees = json.loads(bloc.group(1).replace("<\\/", "</"))
    if not donnees["meta"].get("publique"):
        raise ValueError(f"{chemin} n'est pas la version publique")
    return donnees


def nom_lisible(nom: str) -> str:
    """Nom d'affichage d'un nœud du graphe : les villes gardent leur nom."""
    return NOMS_AFFICHES.get(nom, nom)


def fermetures(d: dict) -> list[dict]:
    """Fermeture de chaque liaison, ramenée à un jour ; les fermetures d'une seule route sont écartées."""
    sc = d["scenarios"]
    lignes = [dict(zip(sc["colonnes"], ligne)) for ligne in sc["lignes"]]
    liens = d["liaisons"]
    extremites = {i: (a, b) for i, a, b in zip(liens["ID_ARC"], liens["VILLE_A"], liens["VILLE_B"])}
    indices = {f["properties"]["ID_ROUTE"]: f["properties"]["i"] for f in d["routes"]["features"]}
    sortie = []
    for ligne in lignes:
        if ligne["scenario"] != "liaison":
            continue
        arc = int(ligne["ID_ARC"])
        ids, ecarts = sc["reports"].get(f"L{arc}", ([], []))
        gardes = [(indices[r], e) for r, e in zip(ids, ecarts) if abs(e) >= SEUIL_DETOUR]
        a, b = extremites[arc]
        sortie.append({
            "id": arc, "a": nom_lisible(a), "b": nom_lisible(b),
            "camions_jour": round(ligne["camions_sur_liaison"] / JOURS_PAR_AN),
            "heures_jour": round(ligne["delta_heures_meilleur"] / JOURS_PAR_AN),
            "sans_itineraire": bool(ligne["coupure"]),
            "sans_chemin_jour": round(ligne["trajets_non_desservis"] / JOURS_PAR_AN),
            "detours": [[i for i, _ in gardes], [e for _, e in gardes]],
        })
    return sortie


def reduire(d: dict) -> dict:
    """Liste blanche : toute clé absente d'ici n'atteint pas la page."""
    noeuds = [{"nom": n["nom"], "frontiere": n["type"].startswith("frontiere"), "lon": n["lon"], "lat": n["lat"]}
              for n in d["noeuds"] if n["nom"] in ETIQUETTES]
    routes = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": f["geometry"],
         "properties": {k: f["properties"][k] for k in ("i", "arc", "km", "nums")}}
        for f in d["routes"]["features"]]}
    fermes = fermetures(d)
    exemple = next((f["id"] for f in fermes if (f["a"], f["b"]) == EXEMPLE), fermes[0]["id"])
    cale = d["cale"]
    meta = {
        "genere_le": d["meta"]["genere_le"], "n_liaisons": d["meta"]["n_liaisons"], "n_routes": d["meta"]["n_routes"],
        "n_sections_mesurees": d["meta"]["n_sections_mesurees"],
        "n_camions_enquetes": cale["observations"]["camions_enquetes"],
        "erreur_mediane": cale["validation"][MODELE_VALIDE]["erreur_mediane"]["moyenne"],
        "exemple": exemple,
    }
    return {"meta": meta, "fond": d["fond"], "routes": routes, "noeuds": noeuds, "flux": cale["flux"],
            "liaisons": fermes}


def exporter(donnees: dict, cible: Path = SORTIE) -> None:
    """Gabarit + thème + moteur Canvas + données → page autonome."""
    html = injecter((DIR_GABARITS / "demo.html").read_text(encoding="utf-8"))
    html = html.replace("/*__MOTEUR_CANVAS__*/", (DIR_GABARITS / "js" / "moteur_canvas.js").read_text(encoding="utf-8"), 1)
    charge = json.dumps(donnees, ensure_ascii=False, separators=(",", ":"), allow_nan=False).replace("</", "<\\/")
    html = html.replace("/*__DONNEES__*/null", charge, 1)
    if re.search(r"/\*__[A-Z_]+__\*/", html):
        raise ValueError("marqueur non remplacé dans le gabarit")
    cible.parent.mkdir(parents=True, exist_ok=True)
    cible.write_text(html, encoding="utf-8")
    print(f"→ {cible.relative_to(RACINE)} ({cible.stat().st_size / 1e6:.1f} Mo)")


if __name__ == "__main__":
    exporter(reduire(publiques()))
