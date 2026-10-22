"""
generer_site_volet1.py
======================
Projet CIRANO — Volet 1 en ligne, au même format que la carte du Volet 2.

Assemble les 5 pages docs/ du Volet 1 (Données, Complétion, Routage, Méthodes,
Résultats) dans une seule page HTML : une barre d'étapes à gauche, commune aux
deux volets, et une étape affichée à la fois.

Pseudo-code
-----------
1. Pour chaque page docs/*.md du Volet 1 : retirer la barre de navigation et le
   titre de niveau 2 (repris par l'en-tête de l'étape).
2. Convertir le Markdown en HTML avec l'API Markdown de GitHub (même rendu que
   sur le dépôt), via la commande `gh`.
3. Ajouter un identifiant à chaque titre (règle des ancres de GitHub) et
   réécrire les liens : figures → ../../figures/, page.md#ancre → #etape=k&ancre=…,
   README et scripts → dépôt GitHub.
4. Injecter la navigation et les 5 étapes dans scripts/gabarits/volet1.html.

Sortie : docs/volet1/index.html (publiée par GitHub Pages).
Usage  : python scripts/generer_site_volet1.py   (nécessite `gh` authentifié)
"""

from __future__ import annotations

import html
import json
import re
import subprocess
from datetime import date
from pathlib import Path
from urllib.parse import unquote

RACINE   = Path(__file__).resolve().parent.parent
DIR_DOCS = RACINE / "docs"
GABARIT  = RACINE / "scripts" / "gabarits" / "volet1.html"
SORTIE   = DIR_DOCS / "volet1" / "index.html"

DEPOT     = "akrambouznad789-cyber/projet-resilience-cirano"
URL_DEPOT = f"https://github.com/{DEPOT}"
URL_V2    = "../volet2/carte_interactive.html"

# (fichier, titre court, sous-titre de la barre, phrase d'introduction)
ETAPES_V1: list[tuple[str, str, str, str]] = [
    ("donnees.md", "Données", "trois couches de départ",
     "Les trois couches géographiques de départ, et le problème des comptages manquants."),
    ("completion.md", "Complétion", "combler les trous",
     "Comment les années et les parts de camions manquantes sont comblées : interpolation, forêts aléatoires, stations voisines."),
    ("routage.md", "Routage", "tracer les liaisons",
     "Comment chaque liaison est tracée sur le vrai réseau routier, puis reçoit les bons comptages grâce à 3 filtres géométriques."),
    ("methodes.md", "Méthodes DJMA", "4 façons de résumer",
     "Les 4 façons de résumer le trafic d'une liaison à partir de ses tronçons, et leurs écarts."),
    ("resultats.md", "Résultats", "304 liaisons sur 307",
     "Le réseau final : 304 liaisons sur 307 ont leur trafic, et les 22 cas particuliers sont expliqués."),
]

# Même liste que ETAPES dans scripts/gabarits/visualisation_volet2.html
ETAPES_V2: list[tuple[str, str]] = [
    ("Réseau du volet 1", "point de départ"), ("ENR brute", "114 734 camions"),
    ("Filtre Québec", "37 603 retenus"), ("Postes d'enquête", "où on les arrête"),
    ("Rattachement aux nœuds", "lieux → nœuds"), ("Routage sur le graphe", "chemin par camion"),
    ("$ par tonne", "calcul dans la page"), ("Actualisation $ 2026", "calcul dans la page"),
    ("Valeur par arc", "calcul dans la page"),
]


def nettoyer_markdown(texte: str) -> str:
    """Retire la barre de navigation (haut et bas) et le titre de niveau 2."""
    lignes = [l for l in texte.splitlines() if not re.match(r"^\[.*\]\([^)]*\.md\).*·", l)]
    texte = "\n".join(lignes).strip()
    texte = re.sub(r"^(---\s*)+", "", texte).strip()
    texte = re.sub(r"(\s*---)+$", "", texte).strip()
    return re.sub(r"^## .*\n", "", texte, count=1).strip()


def rendre_gfm(texte: str) -> str:
    """Markdown → HTML par l'API de GitHub (mode gfm, contexte du dépôt)."""
    corps = json.dumps({"text": texte, "mode": "gfm", "context": DEPOT})
    resultat = subprocess.run(["gh", "api", "markdown", "--input", "-"], input=corps,
                              capture_output=True, text=True, check=True)
    return resultat.stdout


def ancre_github(titre: str) -> str:
    """Ancre d'un titre selon la règle de GitHub (minuscules, ponctuation retirée, espaces → tirets)."""
    texte = html.unescape(re.sub(r"<[^>]+>", "", titre)).strip().lower()
    return re.sub(r"[^\w\- ]", "", texte).replace(" ", "-")


def reecrire(fragment: str, k: int) -> str:
    """Identifiants de titres, liens entre étapes, chemins des figures et liens vers le dépôt."""
    pages = {fichier: i + 1 for i, (fichier, *_) in enumerate(ETAPES_V1)}
    fragment = re.sub(r"</?markdown-accessiblity-table>", "", fragment)
    fragment = re.sub(r'<h([2-4]) dir="auto">(.*?)</h\1>',
                      lambda m: f'<h{m.group(1)} id="e{k}-{ancre_github(m.group(2))}">{m.group(2)}</h{m.group(1)}>',
                      fragment)

    def lien(m: re.Match) -> str:
        attribut, cible = m.group(1), html.unescape(m.group(2))
        nom = cible.split("#")[0].split("/")[-1]
        ancre = unquote(cible.split("#", 1)[1]) if "#" in cible else ""
        if "figures/" in cible:
            nouveau = "../../figures/" + nom
        elif nom in pages:
            nouveau = f"#etape={pages[nom]}" + (f"&ancre={ancre}" if ancre else "")
        elif cible.startswith("#"):
            nouveau = f"#etape={k}&ancre={ancre}"
        elif nom == "README.md":
            nouveau = URL_DEPOT
        elif "scripts/" in cible or "data/" in cible or "qgis/" in cible:
            chemin = re.sub(r"^(\.\./)+", "", cible)
            nouveau = f"{URL_DEPOT}/blob/main/{chemin}"
        else:
            nouveau = cible
        return f'{attribut}="{html.escape(nouveau, quote=True)}"'

    return re.sub(r'\b(href|src)="([^"]*)"', lien, fragment)


def navigation() -> str:
    """Barre d'étapes commune aux deux volets (version de la page Volet 1)."""
    v1 = "".join(
        f'<a class="etape v1" href="#etape={i + 1}" data-k="{i + 1}"><b>{i + 1}</b>'
        f'<span>{html.escape(court)}<small>{html.escape(sur)}</small></span></a>'
        for i, (_, court, sur, _) in enumerate(ETAPES_V1))
    v2 = "".join(
        f'<a class="etape v2" href="{URL_V2}#etape={i}"><b>{i}</b>'
        f'<span>{html.escape(court)}<small>{html.escape(sur)}</small></span></a>'
        for i, (court, sur) in enumerate(ETAPES_V2))
    return (f'<p class="groupe-etapes v1"><span>Volet 1</span> Le trafic</p>{v1}'
            f'<p class="groupe-etapes v2"><span>Volet 2</span> La valeur</p>{v2}')


def section(k: int, court: str, intro: str, corps: str) -> str:
    """Une étape du Volet 1 : en-tête, contenu rendu et liens précédent / suivant."""
    precedent = (f'<a href="#etape={k - 1}">← {html.escape(ETAPES_V1[k - 2][1])}</a>' if k > 1 else "<span></span>")
    suivant = (f'<a href="#etape={k + 1}">{html.escape(ETAPES_V1[k][1])} →</a>' if k < len(ETAPES_V1)
               else f'<a href="{URL_V2}#etape=0">Volet 2 : la carte interactive →</a>')
    return (f'<section class="etape-v1" id="etape-{k}" data-k="{k}">'
            f'<header class="tete"><p class="eyebrow">Volet 1 · étape {k} sur {len(ETAPES_V1)}</p>'
            f'<h2>{html.escape(court)}</h2><p class="intro">{html.escape(intro)}</p></header>'
            f'<div class="md">{corps}</div>'
            f'<nav class="suite" aria-label="Étapes voisines">{precedent}{suivant}</nav></section>')


def main() -> None:
    """Rend les 5 pages et écrit docs/volet1/index.html."""
    sections = []
    for k, (fichier, court, _, intro) in enumerate(ETAPES_V1, start=1):
        print(f"[{k}/{len(ETAPES_V1)}] {fichier}")
        texte = nettoyer_markdown((DIR_DOCS / fichier).read_text(encoding="utf-8"))
        sections.append(section(k, court, intro, reecrire(rendre_gfm(texte), k)))

    page = (GABARIT.read_text(encoding="utf-8")
            .replace("<!--__NAV__-->", navigation(), 1)
            .replace("<!--__SECTIONS__-->", "\n".join(sections), 1)
            .replace("__GENERE_LE__", date.today().isoformat())
            .replace("__URL_DEPOT__", URL_DEPOT)
            .replace("__URL_V2__", URL_V2))
    SORTIE.parent.mkdir(parents=True, exist_ok=True)
    SORTIE.write_text(page, encoding="utf-8")
    print(f"→ {SORTIE.relative_to(RACINE)} ({SORTIE.stat().st_size / 1e3:.0f} ko)")


if __name__ == "__main__":
    main()
