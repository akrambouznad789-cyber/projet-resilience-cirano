// Généré par scripts/theme_pages.py depuis jetons.json : ne pas modifier à la main.
(function () {
  let t = "sombre";
  try {
    t = new URLSearchParams(location.search).get("theme") || localStorage.getItem("cirano-theme") || t;
  } catch (e) {}
  document.documentElement.dataset.theme = t === "clair" ? "clair" : "sombre";
})();
const PALETTES = {"sombre":{"terre":"#22232d","eau":"#141b2b","bord":"#3a3c4e","qc":"#7c819b","reseau":"#565a72","encre":"#f8f8f2","encre2":"#c3c6d4","muet":"#9a9fb8","vide":"#4b4e63","pale":"#343746","contour":"#191a21","noeud":"#f8f8f2","noeud_transparent":"rgba(57,135,229,0.45)","noeud_bord":"#86b6ef","rattache":"#7c819b","selection":"#bd93f9","bleu":["#1c5cab","#2a78d6","#5598e7","#86b6ef","#cde2fb"],"or":["#875600","#b37600","#dc9600","#f0c35b","#f8e2a8"],"div":["#f6b896","#ec835a","#a8441c","#4b4e63","#1c5cab","#5598e7","#b7d3f6"],"cat":["#3987e5","#d95926","#199e70"],"ecarte":"#6b6f86","vert":"#199e70","orange":"#d95926","rtss":{"auto":"#8a8fa8","nat":"#73778f","reg":"#5f6379","col":"#4f5266","autre":"#43465a"},"qual":{"sdr":"#86b6ef","dr":"#256abf","comte":"#3fc493","etat":"#1b6e52","prov":"#d95926"},"chemin":["#ec835a","#a8441c","#256abf","#86b6ef"],"methodes":{"c":"#3987e5","i":"#b7d3f6","e":"#f0c35b"},"statut":{"ok":"#5598e7","hors":"#6b6f86"}},"clair":{"terre":"#f1efe6","eau":"#bcdcf2","bord":"#d9d5c7","qc":"#8e99a6","reseau":"#c9c5b8","encre":"#17181b","encre2":"#4f4e4a","muet":"#7f7d76","vide":"#bdbab1","pale":"#dcd9d0","contour":"#ffffff","noeud":"#ffffff","noeud_transparent":"rgba(42,120,214,0.45)","noeud_bord":"#1b5aa6","rattache":"#8e99a6","selection":"#17181b","bleu":["#a9c8ef","#6fa5e4","#3f86d9","#1f5fae","#0d2d52"],"or":["#f8e2a8","#f0c35b","#dc9600","#a66d00","#6b4600"],"div":["#b8401c","#e9845c","#f4c3a8","#c8c6bf","#a9c8ef","#4a8ddb","#1c4f95"],"cat":["#2a78d6","#eb6834","#1baf7a"],"ecarte":"#a9a69c","vert":"#1baf7a","orange":"#eb6834","rtss":{"auto":"#a29d91","nat":"#b5b0a4","reg":"#c6c2b7","col":"#d0ccc2","autre":"#dad7cf"},"qual":{"sdr":"#1f5fae","dr":"#86b6ef","comte":"#1baf7a","etat":"#9fdcc3","prov":"#eb6834"},"chemin":["#e9845c","#f4c3a8","#a9c8ef","#3f86d9"],"methodes":{"c":"#1f5fae","i":"#86b6ef","e":"#f0c35b"},"statut":{"ok":"#3f86d9","hors":"#8e8b82"}}};
const THEME = document.documentElement.dataset.theme;
const PALETTE = PALETTES[THEME];
function basculerTheme() {
  const t = THEME === "sombre" ? "clair" : "sombre";
  try { localStorage.setItem("cirano-theme", t); } catch (e) {}
  const u = new URL(location.href); u.searchParams.set("theme", t); location.replace(u.href);
}
