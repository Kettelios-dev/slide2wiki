#!/usr/bin/env python3
"""Intègre un cours dans le wiki avec Claude (abonnement, Claude Code en mode non interactif).

Coût borné et constant, quelle que soit la taille du wiki :
- le cours est lu ici (Docling + plan de structure.py), pas par Claude ; Claude ne reçoit que du texte ;
- un appel par partie du cours (chapitre, ou ~MAX_UNIT_CHARS), sans outils, sans historique : une session neuve à chaque fois ;
- le wiki n'est jamais envoyé en entier : seulement les PAGES_MAX pages les plus proches (BM25 calculé ici),
  un index de INDEX_MAX titres proches et la liste des dossiers de la matière ;
- consigne courte et fixe (CLAUDE.md) passée en prompt système à la place de celui de Claude Code : elle est mise en cache.
Chaque appel est journalisé dans claude-usage.csv (tokens) pour vérifier que ça n'augmente pas.
Reprise : les parties déjà faites sont gardées dans md/<cours>-<empreinte>.claude.json (un quota atteint ne refait rien de payé).
  python3 integrer.py cours.pdf [matiere]   # traite un PDF à la main (sinon appelé par watcher.handle)
"""
import collections, csv, datetime, json, math, os, re, subprocess, sys, time
from pathlib import Path
import watcher as w

RULES = Path(os.environ.get("COURS_RULES", Path(__file__).with_name("regles.md")))  # monté depuis ./regles.md
CLAUDE = os.environ.get("CLAUDE_BIN", "claude")
MODEL = os.environ.get("CLAUDE_MODEL", "sonnet")
USAGE = w.ROOT / "claude-usage.csv"
REVIEW = w.ROOT / "a-revoir"  # pages refusées par les contrôles : rien n'est perdu, rien n'est écrasé
MAX_UNIT_CHARS = 24000  # ~7k tokens de cours par appel
PAGES_MAX, PAGE_CHARS, PAGES_CHARS = 5, 12000, 30000  # pages complètes envoyées pour être complétées
INDEX_MAX, DIRS_MAX = 40, 80
TIMEOUT = 900


class Attente(Exception):
    """Quota de l'abonnement atteint ou Claude injoignable : le PDF reste dans inbox, à relancer plus tard."""


# ---------- le wiki, vu par morceaux ----------

def front(text):
    """(dict du frontmatter, corps)."""
    m = re.match(r"---\n(.*?)\n---\n?", text, re.S)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        k, _, v = line.partition(":")
        if v:
            meta[k.strip()] = v.strip().strip('"')
    return meta, text[m.end():]


def tokens(text):
    return [t for t in re.findall(r"[a-z0-9]{3,}", w.slug(text).replace("-", " ")) if t not in STOP]


STOP = set("les des une pour par sur dans avec est sont pas que qui son ses aux cette ces elle ils mais plus "
           "tout tous faire peut etre avoir comme the and for with this that from are".split())


def pages(overlay):
    """{chemin relatif: texte} du wiki tel qu'il sera après cette intégration (disque + pages déjà rédigées)."""
    out = {str(p.relative_to(w.WIKI)).replace("\\", "/"): None for p in w.WIKI.rglob("*.md") if ".git" not in p.parts}
    out.update(overlay)
    return out


def text_of(path, overlay):
    return overlay[path] if path in overlay else (w.WIKI / path).read_text(encoding="utf-8")


def rank(query, paths, overlay):
    """Pages classées par BM25 sur titre, description, tags et titres de sections (pas le corps : rapide et assez précis)."""
    docs = {}
    for p in paths:
        meta, body = front(text_of(p, overlay))
        heads = " ".join(re.findall(r"^#+ (.*)$", body, re.M))
        docs[p] = tokens(" ".join([meta.get("title", ""), meta.get("title", ""), meta.get("description", ""),
                                   meta.get("tags", ""), heads, p.replace("/", " ")]))
    q = collections.Counter(tokens(query))
    n = len(docs) or 1
    df = collections.Counter(t for d in docs.values() for t in set(d))
    avg = sum(map(len, docs.values())) / n or 1
    def score(d):
        tf = collections.Counter(d)
        return sum(math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) * tf[t] * 2.2 / (tf[t] + 1.2 * (0.25 + 0.75 * len(d) / avg))
                   * min(c, 3) for t, c in q.items() if t in tf)
    scored = sorted(((score(d), p) for p, d in docs.items()), reverse=True)
    return [p for s, p in scored if s > 0]


# ---------- découpage du cours ----------

def units(lines, plan):
    """[(titre, page début, page fin)] : chapitres du plan, coupés si trop longs ; sinon blocs de ~MAX_UNIT_CHARS."""
    size = collections.Counter()
    for p, t in lines:
        size[p] += len(t) + 1
    last = max(size) if size else 1
    chaps = [(c["titre"], *c["pages"]) for c in plan.get("chapitres", [])] or [("", 1, last)]
    chaps[-1] = (chaps[-1][0], chaps[-1][1], max(chaps[-1][2], last))  # page Docling en plus : à la fin
    out = []
    for titre, a, b in chaps:
        start, acc = a, 0
        for p in range(a, b + 1):
            if acc and acc + size[p] > MAX_UNIT_CHARS:
                out.append((titre, start, p - 1)); start, acc = p, 0
            acc += size[p]
        out.append((titre, start, b))
    chars = lambda u: sum(size[p] for p in range(u[1], u[2] + 1))
    merged = []  # chapitres courts regroupés : moins d'appels, donc moins de consigne et d'index répétés
    for u in out:
        if merged and chars(merged[-1]) + chars(u) <= MAX_UNIT_CHARS:
            m = merged[-1]
            merged[-1] = (" / ".join(t for t in (m[0], u[0]) if t), m[1], u[2])
        else:
            merged.append(u)
    return [u for u in merged if chars(u) > 200]  # page de garde seule : rien à intégrer


def slides(lines, a, b):
    out, cur = [], None
    for p, t in lines:
        if a <= p <= b:
            if p != cur:
                out.append(f"\n--- diapo {p} ---"); cur = p
            if t.strip():
                out.append(t.rstrip())
    return re.sub(r"<!-- image -->\n?", "", "\n".join(out)).strip()


# ---------- appel à Claude ----------

def prompt(cours, part, n, unit, text, matiere, overlay):
    titre, a, b = unit
    allp = pages(overlay)
    root = w.slug(matiere) if matiere else ""
    scope = [p for p in allp if p.startswith(root + "/")] if root else list(allp)
    ranked = rank(f"{titre} {titre} {text}", scope, overlay)
    full, size = [], 0
    for p in ranked:
        t = text_of(p, overlay)
        if len(full) < PAGES_MAX and len(t) <= PAGE_CHARS and size + len(t) <= PAGES_CHARS:
            full.append(p); size += len(t)
    index = []
    for p in ranked[:INDEX_MAX]:
        meta, _ = front(text_of(p, overlay))
        index.append(f"/{p[:-3]} | {meta.get('title', '')} | {meta.get('description', '')}")
    dirs = sorted({str(Path(p).parent).replace("\\", "/") for p in scope} - {"."})[:DIRS_MAX]
    if root:
        where = f"Matière : **{matiere}** — toutes les pages vont sous `{root}/`."
    else:
        tops = sorted({p.split("/")[0] for p in allp if "/" in p})
        where = f"Matière non précisée : choisis un dossier racine parmi {tops or 'aucun encore'} ou crée-le (nom court, minuscules, tirets)."
    out = [f"# Intégration du cours « {cours} », partie {part}/{n}" + (f" : {titre}" if titre else "") + f" (diapos {a}-{b})",
           where,
           "## Dossiers existants\n" + ("\n".join(dirs) or "(aucun)"),
           "## Pages proches déjà dans le wiki (lien | titre | description)\n" + ("\n".join(index) or "(aucune)")]
    if full:
        out.append("## Pages à compléter si le cours en parle (contenu complet)\n" + "\n\n".join(
            f"<<<PAGE {p}>>>\n{text_of(p, overlay)}\n<<<FIN>>>" for p in full))
    out.append("## Diapos du cours (texte extrait du PDF)\n" + text)
    out.append(f"""## Réponse attendue
Pour chaque page créée ou modifiée, son contenu COMPLET :
<<<FICHIER chemin/relatif.md>>>
---
title: …
description: …
tags: …
---
…
<<<FIN>>>
Tu ne peux modifier une page existante que si son contenu complet est donné ci-dessus ({', '.join(full) or 'aucune ici'}) ; pour une autre page existante, crée plutôt une page liée.
Puis termine par :
<<<RAPPORT>>>
une ligne par page : « créée chemin » ou « complétée chemin » — en quelques mots ; puis les doutes éventuels
<<<FIN>>>
Rien d'autre dans la réponse.""")
    return "\n\n".join(out), set(full)


def token_env():
    """Seules les variables utiles passent à Claude Code (le jeton vient du .env, via docker compose)."""
    keep = ("PATH", "HOME", "LANG", "LC_ALL", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")
    env = {k: v for k, v in os.environ.items() if k in keep and v}
    if not env.get("CLAUDE_CODE_OAUTH_TOKEN") and not env.get("ANTHROPIC_API_KEY"):
        raise Attente("jeton Claude absent : remplir CLAUDE_CODE_OAUTH_TOKEN (ou ANTHROPIC_API_KEY) dans .env, puis `docker compose up -d`")
    env.update(DISABLE_TELEMETRY="1", DISABLE_ERROR_REPORTING="1", CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")
    return env


def ask(system, user, step):
    # sans outils : une seule réponse, rien d'exécuté ; ni skills, ni MCP, ni CLAUDE.md découvert (cwd /tmp) : rien
    # d'autre que la consigne et le prompt n'est envoyé (pas --bare : il refuse le jeton d'abonnement)
    cmd = [CLAUDE, "-p", "--model", MODEL, "--output-format", "json", "--tools", "", "--system-prompt", system,
           "--no-session-persistence", "--disable-slash-commands", "--strict-mcp-config"]
    t0 = time.time()
    try:
        r = subprocess.run(cmd, input=user, capture_output=True, text=True, timeout=TIMEOUT, env=token_env(), cwd="/tmp")
    except subprocess.TimeoutExpired:
        raise Attente("Claude n'a pas répondu à temps")
    try:
        res = json.loads(r.stdout)
    except json.JSONDecodeError:
        msg = (r.stderr or r.stdout).strip()[-300:]
        raise (Attente if re.search(r"limit|quota|network|ENOTFOUND|ECONN|auth|login|token", msg, re.I) else RuntimeError)(f"claude : {msg}")
    if res.get("is_error"):
        msg = str(res.get("result", ""))[:300]
        raise (Attente if re.search(r"limit|quota|overloaded|auth|token|credit", msg, re.I) else RuntimeError)(f"claude : {msg}")
    res["duree"] = time.time() - t0
    return res


def log_usage(cours, part, res):
    u = res.get("usage", {})
    new = not USAGE.exists()
    with USAGE.open("a", newline="", encoding="utf-8") as f:
        c = csv.writer(f)
        if new:
            c.writerow(["date", "cours", "partie", "entree", "cache_ecrit", "cache_lu", "sortie", "secondes", "cout_api_usd"])
        c.writerow([datetime.datetime.now().isoformat(timespec="seconds"), cours, part, u.get("input_tokens", 0),
                    u.get("cache_creation_input_tokens", 0), u.get("cache_read_input_tokens", 0), u.get("output_tokens", 0),
                    round(res["duree"]), res.get("total_cost_usd", "")])


# ---------- réponse : contrôles puis écriture ----------

def parse(answer):
    files = re.findall(r"<<<FICHIER\s+(.+?)>>>\n(.*?)\n<<<FIN>>>", answer, re.S)
    report = re.search(r"<<<RAPPORT>>>\n(.*?)(?:\n<<<FIN>>>|$)", answer, re.S)
    return [(p.strip().strip("`/"), t.strip() + "\n") for p, t in files], (report.group(1).strip() if report else "")


def heads(text):
    return {w.slug(h) for h in re.findall(r"^#{2,3} (.*)$", front(text)[1], re.M)}


def check(path, text, matiere, given, overlay):
    """None si la page est acceptable, sinon la raison du refus."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9/_-]*\.md", path) or ".." in path or path.count("/") > 5:
        return "chemin invalide"
    if matiere and not path.startswith(w.slug(matiere) + "/"):
        return f"hors de la matière {w.slug(matiere)}/"
    meta, body = front(text)
    if not meta.get("title") or len(body.split()) < 15:
        return "frontmatter sans titre ou page vide"
    exists = path in overlay or (w.WIKI / path).exists()
    if exists:
        if path not in given and path not in overlay:
            return "page existante modifiée sans avoir été fournie"
        old = text_of(path, overlay)
        if len(text) < 0.8 * len(old) or not heads(old) <= heads(text):
            return "page existante raccourcie ou section supprimée"
    return None


def finish(text, path, overlay):
    """Frontmatter au format Wiki.js (published, editor, dates)."""
    meta, body = front(text)
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    old = front(text_of(path, overlay))[0] if path in overlay or (w.WIKI / path).exists() else {}
    tags = ", ".join(t for t in (w.slug(x) for x in meta.get("tags", "").split(",")) if t)
    q = lambda v: json.dumps(v, ensure_ascii=False)
    head = ["---", f"title: {q(meta['title'])}", f"description: {q(meta.get('description', ''))}", "published: true",
            f"date: {now}", f"tags: {tags}", "editor: markdown", f"dateCreated: {old.get('dateCreated', now)}", "---", ""]
    return "\n".join(head) + body.strip() + "\n"


def links(overlay):
    """Liens internes cassés : [(page, cible)]."""
    allp = {p[:-3] for p in pages(overlay)}
    return [(p, l) for p in overlay for l in re.findall(r"\]\(/([^)#\s]+)", overlay[p]) if l.rstrip("/") not in allp]


def ask_parsed(system, user, step, cours, k, dump_dir):
    """Pages et rapport de la partie k. Une relance si la réponse n'a aucune balise : Claude répond parfois par une
    remarque de prudence sur un cours de sécurité offensive (vu le 11/11 : 489 tokens, puis 8 pages au 2e essai).
    Chaque réponse illisible est gardée dans dump_dir pour qu'on puisse la lire."""
    for essai in (1, 2):
        res = ask(system, user, step)
        log_usage(cours, k, res)
        files, report = parse(res.get("result", ""))
        if files or report:
            return files, report
        dump_dir.mkdir(parents=True, exist_ok=True)
        (dump_dir / f"partie-{k}-essai-{essai}.txt").write_text(res.get("result", ""), encoding="utf-8")
        step(f"Claude : partie {k} illisible, nouvel essai")
    raise RuntimeError(f"réponse de Claude illisible (partie {k}, 2 essais), gardée dans {dump_dir}")


# ---------- un cours ----------

def process(pdf, step=lambda s: None, matiere=None):
    matiere = matiere or (pdf.parent.name if pdf.parent != w.INBOX else None)
    md_path = w.cache_path(pdf)
    if not md_path.exists():
        md_path.write_text(w.docling_to_md(pdf, step), encoding="utf-8")
    lines = w.drop_repeated(w.page_lines(md_path.read_text(encoding="utf-8")))
    step("lecture du plan du PDF")
    todo = units(lines, w.read_plan(pdf))
    state_path = md_path.with_suffix(".claude.json")
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"faites": [], "pages": {}, "rapport": []}
    overlay, system = state["pages"], RULES.read_text(encoding="utf-8")
    for k, unit in enumerate(todo, 1):
        if f"{unit[1]}-{unit[2]}" in state["faites"]:
            continue
        step(f"Claude : partie {k}/{len(todo)} (diapos {unit[1]}-{unit[2]})")
        user, given = prompt(pdf.name, k, len(todo), unit, slides(lines, unit[1], unit[2]), matiere, overlay)
        files, report = ask_parsed(system, user, step, pdf.name, k, REVIEW / pdf.stem)
        for path, text in files:
            why = check(path, text, matiere, given, overlay)
            if why:
                dest = REVIEW / pdf.stem / path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(text, encoding="utf-8")
                state["rapport"].append(f"REFUSÉE {path} ({why}) -> {dest}")
            else:
                overlay[path] = finish(text, path, overlay)
        state["rapport"] += [l for l in report.splitlines() if l.strip()]
        state["faites"].append(f"{unit[1]}-{unit[2]}")
        state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")  # reprise sans rien repayer
    broken = links(overlay)
    # tout est écrit d'un coup, en un commit : un échec en route ne laisse pas un wiki à moitié modifié
    created = sum(not (w.WIKI / p).exists() for p in overlay)
    for p, t in overlay.items():
        (w.WIKI / p).parent.mkdir(parents=True, exist_ok=True)
        (w.WIKI / p).write_text(t, encoding="utf-8")
    rapport = state["rapport"] + [f"lien cassé dans {p} : /{l}" for p, l in broken]
    (w.ROOT / "rapports").mkdir(exist_ok=True)
    (w.ROOT / "rapports" / f"{pdf.stem}.txt").write_text("\n".join(rapport) + "\n", encoding="utf-8")
    msg = f"cours: {pdf.name} ({created} créées, {len(overlay) - created} complétées)"
    subprocess.run(["git", "-C", str(w.WIKI), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(w.WIKI), "commit", "-q", "--allow-empty", "-m", msg + "\n\n" + "\n".join(rapport)], check=True)
    state_path.unlink()
    print(f"OK {msg}", flush=True)
    return len(overlay), f"{created} créées, {len(overlay) - created} complétées" + (f", {len(broken)} liens cassés" if broken else "")


def selftest():
    t = "---\ntitle: XSS\ndescription: d\ntags: Web, XSS\n---\n## En bref\n" + "mot " * 20 + "\n## Se protéger\nx\n"
    assert front(t)[0]["title"] == "XSS" and heads(t) == {"en-bref", "se-proteger"}
    files, rep = parse("blabla\n<<<FICHIER /web/xss.md>>>\n" + t + "<<<FIN>>>\n<<<RAPPORT>>>\ncréée web/xss.md\n<<<FIN>>>")
    assert files == [("web/xss.md", t)] and rep == "créée web/xss.md"
    ov = {"web/xss.md": t}
    assert check("web/xss.md", t.replace("## Se protéger", "## Autre"), "web", set(), ov) == "page existante raccourcie ou section supprimée"
    assert check("web/xss.md", t + "ajout\n", "web", set(), ov) is None
    assert check("autre/x.md", t, "web", set(), ov) == "hors de la matière web/"
    assert check("web/../x.md", t, None, set(), ov) == "chemin invalide"
    assert check("web/vide.md", "---\ntitle: V\n---\ncourt", None, set(), ov) == "frontmatter sans titre ou page vide"
    f = finish(t, "web/new.md", {})
    assert "tags: web, xss" in f and "published: true" in f and "editor: markdown" in f
    assert rank("faille xss javascript", ["web/xss.md"], ov) == ["web/xss.md"]
    assert rank("registre processeur", ["web/xss.md"], ov) == []
    lines = [(p, "x" * 9000) for p in range(1, 9)]
    plan = {"chapitres": [{"titre": "A", "pages": [1, 2]}, {"titre": "B", "pages": [3, 7]}]}
    assert units(lines, plan) == [("A", 1, 2), ("B", 3, 4), ("B", 5, 6), ("B", 7, 8)]  # page 8 (Docling) à la fin
    lines = [(p, "x" * 2900) for p in range(1, 9)]
    plan = {"chapitres": [{"titre": "A", "pages": [1, 2]}, {"titre": "B", "pages": [3, 4]}, {"titre": "C", "pages": [5, 8]}]}
    assert units(lines, plan) == [("A / B / C", 1, 8)]  # chapitres courts : un seul appel
    import tempfile
    global ask, log_usage
    real = ask, log_usage
    good = "<<<FICHIER web/xss.md>>>\n" + t + "<<<FIN>>>\n<<<RAPPORT>>>\ncréée web/xss.md\n<<<FIN>>>"
    with tempfile.TemporaryDirectory() as d:
        answers = iter(["Je préfère rester prudent.", good])  # remarque de prudence, puis vraie réponse
        ask, log_usage = (lambda *a: {"result": next(answers)}), (lambda *a: None)
        assert ask_parsed("s", "u", lambda s: None, "c", 1, Path(d) / "x")[0] == [("web/xss.md", t)]
        assert (Path(d) / "x" / "partie-1-essai-1.txt").read_text(encoding="utf-8") == "Je préfère rester prudent."
        answers = iter(["non", "toujours non"])
        try:
            ask_parsed("s", "u", lambda s: None, "c", 2, Path(d) / "x"); assert False
        except RuntimeError as e:
            assert "2 essais" in str(e) and (Path(d) / "x" / "partie-2-essai-2.txt").exists()
    ask, log_usage = real
    with tempfile.TemporaryDirectory() as d:  # « Cours 1.pdf » en C et en réseau : deux caches distincts
        a, b = Path(d, "c", "Cours 1.pdf"), Path(d, "reseau", "Cours 1.pdf")
        for p, data in ((a, b"%PDF-1 C"), (b, b"%PDF-1 reseau")):
            p.parent.mkdir(); p.write_bytes(data)
        assert w.cache_path(a) != w.cache_path(b) and w.cache_path(a) == w.cache_path(a)
        assert w.cache_path(a).name.startswith("Cours 1-") and w.cache_path(a).with_suffix(".claude.json").suffix == ".json"
    print("selftest OK")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        selftest()
    else:
        print(process(Path(sys.argv[1]), lambda s: print(s, flush=True), *(sys.argv[2:3] or [None])))
