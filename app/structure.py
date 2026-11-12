#!/usr/bin/env python3
"""Plan d'un cours en diapos lu directement dans le PDF (tailles de police), sans LLM.

Pour chaque page : titre (plus gros bloc du haut de page), sous-titre, nombre de mots. Puis :
- chapitres : diapos-intercalaires (peu de mots, titre bien plus gros que d'habitude, souvent un « 01 »),
  sinon la diapo « Sommaire / Plan » dont les entrées sont retrouvées dans les titres, sinon rien ;
- notions : suites de diapos au même titre, coupées sur les sous-titres quand la suite est longue.
methode = "aucune" (PDF sans texte, ou un même titre partout) : le découpage revient au LLM.
  python3 structure.py cours.pdf   # affiche le plan
"""
import collections, difflib, json, re, statistics, sys, unicodedata
try:
    import pymupdf
except ImportError:  # Debian 12 : paquet python3-fitz (PyMuPDF 1.21)
    import fitz as pymupdf

BULLET = "•●▪■◦–-*>→ \t"
AGENDA = re.compile(r"^(sommaire|plan( du cours)?|agenda|programme|au programme|menu|table des mati[eè]res|contenu)\s*:?$", re.I)
END = re.compile(r"^(merci|questions?|fin|conclusion|pour aller plus loin|aller plus loin|bibliographie|sources?)\b", re.I)
THANKS = re.compile(r"^merci\b", re.I)
STOP = set("le la les l de des du d un une et en a à au aux pour par sur dans avec ce ces son sa ses the of and to".split())
LONG_RUN = 3  # à partir de 3 diapos au même titre, on coupe sur les sous-titres
CODE = re.compile(r"^(//|#|\$|<|https?:)|[{};=]|\s-{1,2}\w|\w\(\)")  # ligne de code ou commande : pas un sous-titre


def norm(t):
    t = re.sub(r"[’'`]", " ", t)  # « L’énumération » : l’apostrophe typographique ne doit pas coller l’article au mot
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"\((suite|fin)\)|\b(suite|ii+|iv|v|vi+)\s*$", "", t)  # « Méthodes II », « TCP (suite) » : même titre
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def ordered(t):
    return [w.rstrip("s") for w in norm(t).split() if w not in STOP and len(w) > 1]


def words(t):
    return set(ordered(t))


def similar(a, b):
    """Entrée de sommaire a retrouvée dans le titre b : tous ses mots présents, ou même texte aux espaces près (« Burp Suite »)."""
    wa, wb = words(a), words(b)
    if wa and wa <= wb and len(wb) <= len(wa) + 4:
        return 1
    la, lb = ordered(a), ordered(b)
    if la and lb[:len(la)] == la:  # « énumération web (pages, sous domaines…): gobuster… » commence par l'entrée
        return 1
    return difflib.SequenceMatcher(None, norm(a).replace(" ", ""), norm(b).replace(" ", "")).ratio()


def blocks(page):
    """Lignes de texte (taille, position, texte), triées de haut en bas. Une ligne qui en continue visiblement
    une autre (« DIRECTIVES & » / « INSTRUCTIONS ») lui est recollée ; les entrées d'une liste restent séparées."""
    lines = []
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            t = re.sub(r"\s+", " ", "".join(s["text"] for s in l["spans"])).strip(BULLET)
            if t:  # tailles arrondies au demi-point : 20.0 et 20.1 sont la même police
                lines.append({"size": round(max(s["size"] for s in l["spans"]) * 2) / 2, "x": l["bbox"][0],
                              "x1": l["bbox"][2], "y": l["bbox"][1], "y1": l["bbox"][3], "text": t})
    out = []
    for l in sorted(lines, key=lambda l: (l["y"], l["x"])):
        prev = below(out, l)
        if prev and (re.search(r"[&,:–-]$|\b(de|des|du|la|le|les|et|à|en|d’|l’)$", prev["text"], re.I) or l["text"][:1].islower()):
            prev["text"] += " " + l["text"]; prev["y1"] = l["y1"]; prev["x1"] = max(prev["x1"], l["x1"])
        else:
            out.append(dict(l))
    return out


def below(lines, l):
    """Ligne de même taille juste au-dessus de l, qui la chevauche horizontalement (titre centré sur deux lignes)."""
    return next((o for o in reversed(lines) if o["size"] == l["size"] and o["x"] < l["x1"] and l["x"] < o["x1"]
                 and 0 <= l["y"] - o["y1"] < l["size"] * 0.8), None)


def full_title(heads, title):
    """Titre de diapo complet : la ligne la plus grosse et celles de même taille empilées juste dessous."""
    text, cur = title["text"], title
    for l in sorted(heads, key=lambda l: l["y"]):
        if l is not cur and below([cur], l):
            text += " " + l["text"]; cur = l
    return text


def read_pages(pdf):
    doc = pymupdf.open(pdf)
    pages, sizes = [], collections.Counter()
    for page in doc:
        bl = blocks(page)
        for b in bl:
            sizes[b["size"]] += len(b["text"])
        pages.append({"h": page.rect.height, "blocks": bl, "words": len(page.get_text().split())})
    body = sizes.most_common(1)[0][0] if sizes else 0
    for i, p in enumerate(pages, 1):
        p["n"] = i
        heads = [b for b in p["blocks"] if b["size"] > body * 1.15 and len(b["text"]) <= 120]
        number = [b for b in heads if re.fullmatch(r"\d{1,2}", b["text"])]  # « 01 » des intercalaires
        top = [b for b in heads if b not in number and b["y"] < p["h"] * 0.45] or [b for b in heads if b not in number]
        title = max(top, key=lambda b: (b["size"], -b["y"])) if top else None
        p["title"], p["size"] = (full_title(heads, title), title["size"]) if title else ("", 0)
        p["number"] = bool(number)
        # « (ne fonctionne plus… » ou « et donc… » : un bout de phrase en gros, pas un sous-titre
        subs = [b for b in heads if b is not title and b not in number and b["size"] < p["size"]
                and len(b["text"]) <= 80 and not CODE.search(b["text"]) and not re.match(r"[(\[a-zà-ÿ]", b["text"])]
        p["sub"] = max(subs, key=lambda b: (b["size"], -b["y"]))["text"] if subs else ""
        p["heads"] = heads
    return pages, body


def find_agenda(pages):
    """Entrées de la diapo de plan (dans les 5 premières pages) : titre « Sommaire… » ou plusieurs entrées courtes de même taille."""
    for p in pages[:5]:
        items = [b for b in p["heads"] if b["text"] != p["title"] and len(b["text"].split()) <= 8 and not AGENDA.match(b["text"])]
        same = collections.Counter(b["size"] for b in items).most_common(1)
        if not same:
            continue
        items = sorted((b for b in items if b["size"] == same[0][0]), key=lambda b: (round(b["y"] / 25), b["x"]))
        if AGENDA.match(p["title"]) and len(items) >= 2 or p["n"] > 1 and len(items) >= 3 and p["words"] <= 40:
            return p["n"], [b["text"] for b in items]
    return None, []


def chapters(pages, agenda_page, agenda):
    """[(page de début, titre)] des chapitres, et la méthode utilisée."""
    sizes = [p["size"] for p in pages if p["title"]]
    usual = statistics.median(sizes) if sizes else 0
    divs = [(p["n"], p["title"]) for p in pages[1:] if p["title"] and p["words"] <= (30 if p["number"] else 15)
            and not AGENDA.match(p["title"]) and not END.match(p["title"]) and (p["number"] or p["size"] >= usual * 1.5)]
    if len(divs) >= 2:
        return divs, "intercalaires"
    if agenda:  # entrées du plan retrouvées dans l'ordre parmi les titres qui suivent
        found, start = [], agenda_page + 1
        for item in agenda:
            hit = next((p["n"] for p in pages[start - 1:]
                        if max(similar(item, t) for t in [p["title"], *(h["text"] for h in p["heads"])]) >= 0.85), None)
            if hit:
                found.append((hit, item)); start = hit + 1
        if len(found) >= 2:
            return found, "sommaire"
    return [], "titres"


def notions(pages, a, b, generic):
    """Notions des pages a..b : suites de diapos au même titre ; une suite longue est coupée sur ses sous-titres."""
    runs = []
    for p in pages[a - 1:b]:
        t = "" if norm(p["title"]) in generic else p["title"]
        # « Sommaire », « Merci ! » (même avec les coordonnées du prof), « Conclusion » vide : avec la diapo d'avant
        if AGENDA.match(t) or THANKS.match(t) or END.match(t) and p["words"] < 15:
            t = ""
        if runs and (norm(t) == norm(runs[-1]["titre"]) or not t):
            runs[-1]["pages"][1] = p["n"]; runs[-1]["subs"].append((p["n"], p["sub"]))
        else:
            runs.append({"titre": t, "pages": [p["n"], p["n"]], "subs": [(p["n"], p["sub"])]})
    out = []
    for r in runs:
        subs = [(n, s) for n, s in r["subs"] if s]
        if r["pages"][1] - r["pages"][0] + 1 >= LONG_RUN and len({norm(s) for _, s in subs}) >= 2:
            cur = None
            for n, s in r["subs"]:
                if s and (cur is None or norm(s) != norm(cur["titre"])):
                    cur = {"titre": s, "parent": r["titre"], "pages": [n, n]}; out.append(cur)
                elif cur is None:
                    cur = {"titre": r["titre"], "pages": [n, n]}; out.append(cur)
                else:
                    cur["pages"][1] = n
        else:
            out.append({"titre": r["titre"], "pages": r["pages"]})
    return out


def plan(pdf):
    pages, body = read_pages(pdf)
    n = len(pages)
    with_text = sum(1 for p in pages if p["words"] >= 5)
    titled = [norm(p["title"]) for p in pages if p["title"]]
    generic = {t for t, c in collections.Counter(titled).items() if c > max(3, n * 0.5)}  # titre du cours répété partout
    if with_text < n * 0.3 or len(set(titled) - generic) < 3:
        return {"pages": n, "methode": "aucune", "raison": "pas de texte (scan, OCR nécessaire)" if with_text < n * 0.3
                else "titres absents ou identiques sur toutes les diapos", "chapitres": []}
    agenda_page, agenda = find_agenda(pages)
    starts, how = chapters(pages, agenda_page, agenda)
    if not starts:
        starts = [(1, "")]  # pas de chapitres trouvés : un seul bloc, le LLM nommera les chapitres
    elif starts[0][0] > 1:
        starts = [(1, "Introduction")] + starts
    out = []
    for i, (s, t) in enumerate(starts):
        e = starts[i + 1][0] - 1 if i + 1 < len(starts) else n
        divider = s < e and norm(pages[s - 1]["title"]) == norm(t) and pages[s - 1]["words"] <= 30
        ns = notions(pages, s + divider, e, generic)
        if divider:  # l'intercalaire n'est pas une notion : rattaché à la première notion du chapitre
            ns[0]["pages"][0] = s
        out.append({"titre": t, "pages": [s, e], "notions": ns})
    return {"pages": n, "methode": how, "sommaire": agenda, "titres_generiques": sorted(generic), "chapitres": out}


def show(path, res):
    print(f"\n=== {path} : {res['pages']} p., méthode {res['methode']}" + (f" ({res['raison']})" if res.get("raison") else ""))
    if res.get("sommaire"):
        print(f"    sommaire : {res['sommaire']}")
    for c in res["chapitres"]:
        print(f"  [{c['pages'][0]}-{c['pages'][1]}] {c['titre']}")
        for x in c["notions"]:
            print(f"      p.{x['pages'][0]}-{x['pages'][1]}  {x['titre'] or '(sans titre)'}" + (f"   ⟵ {x['parent']}" if x.get("parent") else ""))


def selftest():
    assert norm("Les Méthodes en Pratique II") == norm("Les méthodes en pratique") == "les methodes en pratique"
    assert norm("TCP (suite)") == "tcp"
    assert similar("Les protocoles", "Protocole HTTP") == 1  # pluriel, mot en plus
    assert similar("Burp Suite: l’outil indispensable", "BurpSuite : l'outil indispensable") > 0.85  # espaces
    assert similar("L’énumération web", "énumération web (pages, sous domaines, vhosts…): gobuster ffuf") == 1
    assert similar("Les outils de scan automatisés", "Les outils importants") < 0.85
    assert AGENDA.match("Sommaire") and AGENDA.match("Plan :") and not AGENDA.match("Plan de Classe")
    assert THANKS.match("Merci !") and not THANKS.match("Mercure")
    assert CODE.search("gobuster dir -u https://x") and CODE.search("}") and not CODE.search("Faille XSS permanente")
    print("selftest OK")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if sys.argv[1:] == ["--selftest"]:
        sys.exit(selftest())
    for f in sys.argv[1:]:
        r = plan(f)
        show(f, r) if "--json" not in sys.argv else print(json.dumps(r, ensure_ascii=False))
