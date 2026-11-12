#!/usr/bin/env python3
"""Demandes sur ses cours : fiche de révision, exercices corrigés, synthèse, QCM ou simple question.

Claude ne reçoit que les pages du wiki les plus proches du sujet (BM25, SOURCES_CHARS au plus) et les règles :
coût borné, comme pour un cours. Le résultat est montré sur la page et, si demandé, enregistré comme nouvelle page
dans <matière>/<dossier du type>/, avec la liste des pages utilisées, en un commit (poussé vers le dépôt distant).
"""
import re, subprocess, traceback
import gitsync
import integrer as g
import watcher as w

SOURCES_CHARS = 40000  # ~11k tokens de pages du wiki par demande
# type : (dossier où la page est rangée, titre par défaut, consigne)
TYPES = {
    "fiche": ("fiches", "Fiche de révision", "une fiche de révision : l'essentiel à retenir en une page (définitions, "
              "formules ou commandes clés, schéma du raisonnement, pièges classiques), en listes courtes"),
    "exercices": ("exercices", "Exercices", "5 à 10 exercices progressifs dans le style du cours, chacun suivi de sa "
                  "correction détaillée dans un bloc repliable <details><summary>Correction</summary>…</details>"),
    "synthese": ("syntheses", "Synthèse", "une synthèse qui relie les notions entre elles : vue d'ensemble, ordre logique, "
                 "liens et différences entre notions voisines, tableau comparatif si utile"),
    "qcm": ("qcm", "QCM", "un QCM de 10 questions à 4 choix ; les réponses et leurs explications regroupées à la fin, "
            "dans un bloc repliable <details><summary>Réponses</summary>…</details>"),
    "question": ("questions", "Réponse", "une réponse précise et directe à la question, en citant les pages utilisées"),
}
OUTPUTS = {folder for folder, _, _ in TYPES.values()}  # pages produites ici : pas reprises comme sources

PROMPT = """# Demande : {label}
Sujet : {sujet}
{consigne}
À produire : {task}.
Appuie-toi uniquement sur les pages du wiki ci-dessous (elles viennent des cours) ; si elles ne suffisent pas pour
une partie de la demande, dis-le dans le rapport au lieu d'inventer.

## Pages du wiki
{sources}

## Réponse attendue
<<<FICHIER {path}>>>
---
title: …
description: …
tags: …
---
…
<<<FIN>>>
<<<RAPPORT>>>
une ligne : ce qui a été fait ; puis les limites (notions absentes des pages, doutes)
<<<FIN>>>
Rien d'autre dans la réponse."""


WHOLE = re.compile(r"\b(tou[ts] (le|les) (cours|chapitres?)|toute la mati[eè]re|l'ensemble (du|des|de la) (cours|chapitres|mati[eè]re)"
                   r"|(cours|mati[eè]re) (complet|compl[eè]te|enti[eè]re?)|r[ée]vision g[ée]n[ée]rale)\b", re.I)


def sources(matiere, sujet):
    """Pages les plus proches du sujet (hors pages déjà produites par des demandes), dans la limite de SOURCES_CHARS.
    « Tout le cours », le nom de la matière, ou un sujet introuvable mot pour mot : toutes les pages de la matière,
    chacune résumée à sa part du budget (la recherche par mots ne connaît pas les synonymes)."""
    root = w.slug(matiere) if matiere else ""
    scope = [p for p in g.pages({}) if (not root or p.startswith(root + "/"))
             and not set(p.split("/")[1:-1]) & OUTPUTS]
    ranked = g.rank(sujet, scope, {})
    if root and scope and (not ranked or WHOLE.search(sujet) or w.slug(sujet.split()[0] if sujet.split() else "") == root):
        share = max(400, SOURCES_CHARS // len(scope))
        out, size = [], 0
        for p in sorted(scope):
            meta, body = g.front(g.text_of(p, {}))
            t = f"# {meta.get('title', p)}\n{body.strip()}"  # sans frontmatter : le budget va au contenu
            t = t if len(t) <= share else t[:share] + "\n[… page tronquée : la suite existe dans le wiki]"
            if size + len(t) > SOURCES_CHARS:
                break
            out.append((p, t)); size += len(t)
        return out
    out, size = [], 0
    for p in ranked:
        t = g.text_of(p, {})
        if size + len(t) > SOURCES_CHARS:
            if out:
                continue
            t = t[:SOURCES_CHARS]  # une seule page, énorme : son début suffit plutôt que rien
        out.append((p, t)); size += len(t)
    return out


def target(matiere, kind, sujet):
    """Chemin libre pour la nouvelle page : <matière>/<dossier>/<sujet>[-2…].md (jamais d'écrasement)."""
    root = w.slug(matiere) if matiere else "general"
    base = f"{root}/{TYPES[kind][0]}/{w.slug(sujet)[:60]}"
    path, k = f"{base}.md", 2
    while (w.WIKI / path).exists():
        path, k = f"{base}-{k}.md", k + 1
    return path


def run(d, step=lambda s: None):
    """d = {type, matiere, sujet, consigne, enregistrer}. Rend (etat, détail, markdown, chemin ou None).
    Ne lève jamais : une erreur imprévue (réponse illisible…) tuerait le fil de traitement et bloquerait la file."""
    try:
        return _run(d, step)
    except Exception as e:
        traceback.print_exc()
        return "erreur", f"{type(e).__name__}: {gitsync.hide(str(e))[:300]}", "", None


def _run(d, step):
    kind, matiere, sujet = d.get("type", ""), d.get("matiere", ""), d.get("sujet", "").strip()
    if kind not in TYPES or not sujet:
        return "erreur", "type de demande ou sujet manquant", "", None
    try:
        g.sync(step, "récupération du wiki")
    except g.Attente as e:
        return "attente", str(e), "", None
    src = sources(matiere, f"{sujet} {sujet} {d.get('consigne', '')}")
    if not src:
        return "erreur", f"aucune page du wiki ne parle de « {sujet} »" + (f" dans {matiere}" if matiere else ""), "", None
    path = target(matiere, kind, sujet)
    folder, label, task = TYPES[kind]
    consigne = d.get("consigne", "").strip()
    user = PROMPT.format(label=label, sujet=sujet, consigne=f"Précisions : {consigne}" if consigne else "", task=task,
                         path=path, sources="\n\n".join(f"<<<PAGE {p}>>>\n{t}\n<<<FIN>>>" for p, t in src))
    system = g.RULES.read_text(encoding="utf-8")
    step(f"Claude : {label.lower()} à partir de {len(src)} page(s)")
    try:
        files, _, report = g.ask_parsed(system, user, step, "demande", kind, g.REVIEW / "demandes")
    except g.Attente as e:
        return "attente", str(e), "", None
    if not files:
        return "erreur", "Claude n'a pas rendu de page", report, None
    text = files[0][1]  # le chemin vient d'ici, pas de Claude : rien ne peut aller ailleurs
    links = "\n".join(f"- [{g.front(t)[0].get('title') or p}](/{p[:-3]})" for p, t in src)
    text = text.rstrip() + f"\n\n## Sources\n\n{links}\n"
    why = g.check(path, text, matiere, set(), {})
    if why:
        return "erreur", f"page refusée : {why}", text, None
    page = g.finish(text, path, {})
    if not d.get("enregistrer"):
        return "ok", f"{label} prête (non enregistrée)" + (f" · {report}" if report else ""), page, None
    (w.WIKI / path).parent.mkdir(parents=True, exist_ok=True)
    (w.WIKI / path).write_text(page, encoding="utf-8")
    subprocess.run(["git", "-C", str(w.WIKI), "add", path], check=True)
    subprocess.run(["git", "-C", str(w.WIKI), "commit", "-q", "-m", f"demande: {label.lower()} « {sujet} »\n\n{report}"], check=True)
    pushed = gitsync.push()
    seen = None
    if pushed:
        step("Wiki.js : import de la page")
        seen = gitsync.notify_wikijs()
    when = {True: "visible dans Wiki.js", False: "visible à la prochaine synchro de Wiki.js",
            None: "visible à la prochaine synchro de Wiki.js (5 min au plus)"}[seen]
    return "ok", f"{label} enregistrée : /{path[:-3]} · " + (when if pushed else "envoi Git en attente"), page, path


def selftest():
    import tempfile
    from pathlib import Path
    saved = w.WIKI, g.ask, g.log_usage, g.RULES, gitsync.REMOTE
    t = lambda title, body: f"---\ntitle: {title}\ndescription: d\ntags: x\n---\n## En bref\n{body} " + "mot " * 20 + "\n"
    try:
        with tempfile.TemporaryDirectory() as d:
            w.WIKI = Path(d, "wiki"); g.RULES = Path(d, "regles.md"); g.RULES.write_text("règles", encoding="utf-8")
            gitsync.REMOTE = ""
            for p, body in (("reseaux/osi.md", "modele osi couches"), ("reseaux/tcp.md", "protocole tcp poignee"),
                            ("reseaux/fiches/osi.md", "fiche osi deja faite"), ("c/pointeurs.md", "pointeurs osi")):
                (w.WIKI / p).parent.mkdir(parents=True, exist_ok=True)
                (w.WIKI / p).write_text(t(p.split("/")[-1][:-3].upper(), body), encoding="utf-8")
            git = lambda *a: subprocess.run(["git", "-C", str(w.WIKI), *a], check=True, capture_output=True, encoding="utf-8").stdout
            git("init", "-q", "-b", "main"); git("config", "user.email", "t@t"); git("config", "user.name", "t")
            git("add", "-A"); git("commit", "-q", "-m", "init")
            # sources : la matière seulement, pas les fiches déjà produites, la page la plus proche d'abord
            assert [p for p, _ in sources("Réseaux", "modele osi")] == ["reseaux/osi.md"]
            assert target("Réseaux", "fiche", "Modèle OSI") == "reseaux/fiches/modele-osi.md"
            (w.WIKI / "reseaux/fiches/modele-osi.md").write_text("x", encoding="utf-8")
            assert target("Réseaux", "fiche", "Modèle OSI") == "reseaux/fiches/modele-osi-2.md"  # jamais d'écrasement
            sent = []
            g.log_usage = lambda *a: None
            g.ask = lambda system, user, step: sent.append(user) or {"result": (
                "<<<FICHIER ailleurs/hack.md>>>\n" + t("Fiche OSI", "les 7 couches") + "<<<FIN>>>\n<<<RAPPORT>>>\nfait\n<<<FIN>>>")}
            etat, detail, page, path = run({"type": "fiche", "matiere": "Réseaux", "sujet": "modèle OSI", "enregistrer": True})
            assert etat == "ok" and path == "reseaux/fiches/modele-osi-2.md", (etat, detail)  # chemin imposé, pas celui de Claude
            assert "<<<PAGE reseaux/osi.md>>>" in sent[0] and "pointeurs" not in sent[0]
            assert "## Sources" in page and "[OSI](/reseaux/osi)" in page and (w.WIKI / path).exists()
            assert git("log", "-1", "--format=%s").startswith("demande: fiche de révision")
            etat, detail, page, path = run({"type": "question", "matiere": "Réseaux", "sujet": "osi", "enregistrer": False})
            assert etat == "ok" and path is None and "non enregistrée" in detail
            # toute la matière : « tout le cours », le nom de la matière, ou un sujet absent mot pour mot (synonymes)
            for sujet in ("tout le cours", "Réseaux", "photosynthèse"):
                assert [p for p, _ in sources("Réseaux", sujet)] == ["reseaux/osi.md", "reseaux/tcp.md"], sujet
            assert sources("Réseaux", "tout le cours")[0][1].startswith("# OSI\n## En bref")  # extrait sans frontmatter
            assert [p for p, _ in sources("Réseaux", "toutes les couches osi")] == ["reseaux/osi.md"]  # « toutes » seul : sujet précis
            assert run({"type": "fiche", "matiere": "", "sujet": "photosynthèse"})[0] == "erreur"  # rien nulle part
            assert run({"type": "inconnu", "sujet": "x"})[0] == "erreur"

            def illisible(*a):
                raise RuntimeError("claude : réponse illisible")
            g.ask = illisible  # erreur imprévue : rendue comme un état, jamais levée (elle tuerait le fil de traitement)
            assert run({"type": "fiche", "matiere": "Réseaux", "sujet": "osi"})[:2] == ("erreur", "RuntimeError: claude : réponse illisible")
    finally:
        w.WIKI, g.ask, g.log_usage, g.RULES, gitsync.REMOTE = saved
    print("selftest OK")


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["--selftest"]:
        selftest()
