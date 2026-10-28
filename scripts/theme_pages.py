"""
theme_pages.py
==============
Thème commun des pages interactives (Volets 1, 2 et 3), tiré d'une source unique :
`scripts/gabarits/theme/jetons.json` (format DTCG, importable par Style Dictionary).

Pseudo-code
-----------
lire les jetons → valeurs simples (on retire $type et $description)
css()        : @font-face (polices OFL de data/reference/fonts) + propriétés CSS par thème
               (sombre par défaut ; clair si <html data-theme="clair">) + familles de polices
js()         : choix du thème (paramètre ?theme=, sinon dernier choix mémorisé, sinon sombre)
               + PALETTE : couleurs des données sur la carte, pour le thème choisi
injecter()   : remplace /*__THEME_CSS__*/ et /*__THEME_JS__*/ dans un gabarit HTML ;
               les polices y sont intégrées en base64 (page autonome, même hors ligne)
main()       : écrit theme.css (polices par URL relative fonts/) et palette.js à côté des
               jetons, pour l'application web du jumeau numérique officiel

N'importe que la bibliothèque standard : les scripts du venv `portfolio` (Volets 1 et 2)
et ceux du paquet `jumeau_cirano` (Volet 3) s'en servent tous les deux.

Usage : python scripts/theme_pages.py
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
DIR_THEME = RACINE / "scripts" / "gabarits" / "theme"
DIR_POLICES = RACINE / "data" / "reference" / "fonts"
THEMES = ("sombre", "clair")


def jetons() -> dict:
    """Jetons bruts (DTCG)."""
    return json.loads((DIR_THEME / "jetons.json").read_text(encoding="utf-8"))


def valeurs(noeud: object) -> object:
    """Arbre DTCG → valeurs simples : {"bg": {"$value": "#191a21"}} devient {"bg": "#191a21"}."""
    if isinstance(noeud, dict):
        if "$value" in noeud:
            return noeud["$value"]
        return {k: valeurs(v) for k, v in noeud.items() if not k.startswith("$")}
    return noeud


def famille(pile: list[str]) -> str:
    return ",".join(f'"{f}"' if " " in f else f for f in pile)


def css(polices_en_ligne: bool = True) -> str:
    """Feuille de style du thème : polices, propriétés par thème, familles de polices."""
    j = jetons()
    blocs = []
    for p in j["$extensions"]["cirano.polices"]:
        if polices_en_ligne:
            b64 = base64.b64encode((DIR_POLICES / p["fichier"]).read_bytes()).decode("ascii")
            src = f"url(data:font/woff2;base64,{b64})"
        else:
            src = f'url("fonts/{p["fichier"]}")'
        blocs.append(f'@font-face{{font-family:"{p["famille"]}";src:{src} format("woff2");'
                     f'font-weight:{p["graisse"]};font-style:{p["style"]};font-display:swap}}')
    interface = valeurs(j["interface"])
    for theme in THEMES:
        selecteur = ':root,:root[data-theme="sombre"]' if theme == "sombre" else ':root[data-theme="clair"]'
        schema = "dark" if theme == "sombre" else "light"
        props = "".join(f"--{k}:{v};" for k, v in interface[theme].items())
        blocs.append(f"{selecteur}{{color-scheme:{schema};{props}}}")
    polices = valeurs(j["police"])
    blocs.append(":root{" + "".join(f"--{k}:{famille(v)};" for k, v in polices.items())
                 + f"--r:{valeurs(j['rayon'])['r']};font-synthesis-weight:none}}")
    blocs.append("@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;"
                 "transition:none!important;scroll-behavior:auto!important}}")
    return "\n".join(blocs)


def js() -> str:
    """Choix du thème + PALETTE (couleurs des données sur la carte) ; à placer dans <head>."""
    palettes = json.dumps(valeurs(jetons()["carte"]), ensure_ascii=False, separators=(",", ":"))
    return f"""(function () {{
  let t = "sombre";
  try {{
    t = new URLSearchParams(location.search).get("theme") || localStorage.getItem("cirano-theme") || t;
  }} catch (e) {{}}
  document.documentElement.dataset.theme = t === "clair" ? "clair" : "sombre";
}})();
const PALETTES = {palettes};
const THEME = document.documentElement.dataset.theme;
const PALETTE = PALETTES[THEME];
function basculerTheme() {{
  const t = THEME === "sombre" ? "clair" : "sombre";
  try {{ localStorage.setItem("cirano-theme", t); }} catch (e) {{}}
  const u = new URL(location.href); u.searchParams.set("theme", t); location.replace(u.href);
}}"""


def injecter(html: str) -> str:
    """Remplace les deux marqueurs du thème dans un gabarit (polices intégrées)."""
    for marqueur in ("/*__THEME_CSS__*/", "/*__THEME_JS__*/"):
        if marqueur not in html:
            raise ValueError(f"marqueur {marqueur} absent du gabarit")
    return html.replace("/*__THEME_CSS__*/", css(), 1).replace("/*__THEME_JS__*/", js(), 1)


def main() -> None:
    """Écrit theme.css et palette.js (pour l'application officielle) à côté des jetons."""
    (DIR_THEME / "theme.css").write_text(
        "/* Généré par scripts/theme_pages.py depuis jetons.json : ne pas modifier à la main.\n"
        "   Polices : copier data/reference/fonts/*.woff2 dans un dossier fonts/ à côté de ce fichier. */\n"
        + css(polices_en_ligne=False) + "\n", encoding="utf-8")
    (DIR_THEME / "palette.js").write_text(
        "// Généré par scripts/theme_pages.py depuis jetons.json : ne pas modifier à la main.\n" + js() + "\n",
        encoding="utf-8")
    print(f"→ {DIR_THEME / 'theme.css'}\n→ {DIR_THEME / 'palette.js'}")


if __name__ == "__main__":
    main()
