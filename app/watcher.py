#!/usr/bin/env python3
"""Lecture d'un cours (PDF -> Docling -> md/) et rangement du PDF ; la rédaction des pages est dans integrer.py (Claude).

Appelé par upload.py à chaque dépôt (pas de surveillance permanente) ; `watcher.py` seul fait une passe sur inbox/.
inbox/<matiere>/fichier.pdf force la matière ; un PDF à la racine d'inbox/ laisse Claude choisir le dossier racine.
"""
import hashlib, os, re, shutil, sys, time, traceback, unicodedata
from pathlib import Path
import requests
from pypdf import PdfReader
try:
    import structure
except ImportError:  # PyMuPDF absent (apt install python3-fitz) : cours découpé en blocs de taille fixe
    structure = None

ROOT = Path(os.environ.get("COURS_ROOT", "/srv/cours"))
INBOX, MD, WIKI = ROOT / "inbox", ROOT / "md", ROOT / "wiki"
DONE, FAILED, CANCELLED = INBOX / "_traites", INBOX / "_erreurs", INBOX / "_annules"
DOCLING = os.environ.get("DOCLING_URL", "http://localhost:5001")
PAGE_MARK = "<!-- page-break -->"
DOCLING_PAGES = 10  # ~1 à 3 min par tranche sur 2 cœurs pour des diapos avec images


def slug(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:80] or "sans-titre"


def cache_path(pdf):
    """md/<nom>-<empreinte>.md : deux PDF de même nom dans deux matières (« Cours 1.pdf ») n'ont pas le même cache."""
    return MD / f"{pdf.stem}-{hashlib.sha256(pdf.read_bytes()).hexdigest()[:12]}.md"


def docling_to_md(pdf, step=lambda s: None):
    """Par tranches de DOCLING_PAGES : progression visible (docling-serve n'en donne pas) et jamais près de sa limite de 30 min."""
    total, parts, t0 = len(PdfReader(pdf).pages), [], time.time()
    for a in range(1, total + 1, DOCLING_PAGES):
        b = min(a + DOCLING_PAGES - 1, total)
        reste = f", ~{(time.time() - t0) / (a - 1) * (total - a + 1) / 60:.0f} min restantes" if a > 1 else ""
        step(f"conversion Docling pages {a}-{b}/{total}{reste}")
        with pdf.open("rb") as f:
            r = requests.post(f"{DOCLING}/v1/convert/file", timeout=1900,
                              files={"files": (pdf.name, f, "application/pdf")},
                              data={"to_formats": "md", "image_export_mode": "placeholder",
                                    "md_page_break_placeholder": PAGE_MARK, "page_range": [a, b]})
        r.raise_for_status()
        doc = r.json()
        if doc.get("status") not in ("success", "partial_success"):
            raise RuntimeError(f"docling pages {a}-{b}: {doc.get('status')} {doc.get('errors')}")
        md = doc["document"]["md_content"]
        if md.count(PAGE_MARK) != b - a:  # les numéros de diapo envoyés à Claude en dépendent
            print(f"attention {pdf.name} p.{a}-{b} : {md.count(PAGE_MARK)} sauts de page au lieu de {b - a}", flush=True)
        parts.append(md)
    return f"\n\n{PAGE_MARK}\n\n".join(parts)


def page_lines(md):
    """Lignes (page, texte) ; les marqueurs de saut de page disparaissent."""
    out, page = [], 1
    for line in md.splitlines():
        if line.strip() == PAGE_MARK:
            page += 1
        else:
            out.append((page, line))
    return out


def drop_repeated(lines):
    """Retire les lignes présentes sur plus d'un tiers des pages (en-tête de diapo, nom d'école, logos) : du bruit.
    Seulement des lignes avec un vrai mot : « } », « { » ou une puce seule reviennent partout dans du code."""
    pages = {}
    for p, t in lines:
        if re.search(r"[^\W\d_]{3,}", t):
            pages.setdefault(t.strip(), set()).add(p)
    n = len({p for p, _ in lines})
    noisy = {t for t, ps in pages.items() if len(ps) > n / 3} if n >= 4 else set()
    return [(p, t) for p, t in lines if t.strip() not in noisy]


def read_plan(pdf):
    """Plan de structure.py, ou méthode « aucune » s'il est indisponible ou plante (PDF exotique) : jamais bloquant."""
    if structure is None:
        return {"methode": "aucune", "raison": "PyMuPDF absent"}
    try:
        return structure.plan(str(pdf))
    except Exception as e:
        print(f"structure.py a échoué sur {pdf.name}, cours découpé en blocs : {e}", flush=True)
        return {"methode": "aucune", "raison": str(e)}


class Annule(Exception):
    """Traitement annulé depuis la page : levé au pas suivant, avant toute écriture dans le wiki."""


def pending():
    for pdf in sorted(INBOX.rglob("*.pdf")):
        if any(part.startswith("_") for part in pdf.relative_to(INBOX).parts[:-1]):  # _traites, _erreurs, _annules
            continue
        if time.time() - pdf.stat().st_mtime > 15:  # copie terminée (pas modifié depuis 15 s)
            yield pdf


def move(pdf, dest):
    target = dest / pdf.relative_to(INBOX)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(pdf), str(target))


def handle(pdf, step=lambda s: None):
    """Traite un PDF d'inbox/ et le range. Rend (etat, détail) : ok, attente (laissé dans inbox) ou erreur."""
    import integrer  # importé ici : integrer importe ce module
    print(f"traitement {pdf}", flush=True)
    try:
        n, detail = integrer.process(pdf, step)
        move(pdf, DONE)
        return "ok", f"{n} pages : {detail}"
    except integrer.Attente as e:  # quota atteint, jeton absent, Claude injoignable
        print(f"en attente : {e}", flush=True)
        return "attente", str(e)
    except Annule:  # parties déjà payées gardées (md/…claude.json) : un nouveau dépôt du PDF reprend sans repayer
        move(pdf, CANCELLED)
        return "annulé", "annulé : rien n'a été écrit dans le wiki, PDF rangé dans inbox/_annules"
    except (requests.ConnectionError, requests.Timeout) as e:
        print(f"Docling injoignable, PDF laissé dans inbox: {e}", flush=True)
        return "attente", "Docling injoignable (conteneur docling démarré ?)"
    except requests.HTTPError as e:
        if e.response.status_code >= 500:  # Docling redémarré ou planté : passager
            print(f"{e} : Docling en erreur, PDF laissé dans inbox", flush=True)
            return "attente", f"HTTP {e.response.status_code} de Docling (redémarrage ?) : relancer"
        traceback.print_exc(); move(pdf, FAILED)
        return "erreur", str(e)
    except Exception as e:
        traceback.print_exc(); move(pdf, FAILED)
        return "erreur", f"{type(e).__name__}: {e}"


def main():
    """Passe unique sur inbox/ (PDF copiés à la main) ; en temps normal c'est upload.py qui appelle handle()."""
    for d in (INBOX, MD, WIKI, DONE, FAILED):
        d.mkdir(parents=True, exist_ok=True)
    print(f"watcher: {INBOX} -> {WIKI} (docling {DOCLING})", flush=True)
    for pdf in pending():
        print(*handle(pdf), flush=True)


def selftest():
    lines = page_lines(f"# Chap 1\nintro\n{PAGE_MARK}\n## A\ntexte a\n## B\ntexte b")
    assert [p for p, _ in lines] == [1, 1, 2, 2, 2, 2]
    assert slug("Théorème de Gödel !") == "theoreme-de-godel"
    lines = [(p, t) for p in range(1, 6) for t in ("ESGI", f"texte {p}")]
    assert [t for _, t in drop_repeated(lines)] == [f"texte {p}" for p in range(1, 6)]  # en-tête présent partout
    lines = [(p, t) for p in range(1, 6) for t in (f"x = {p};", "}", "●")]
    assert drop_repeated(lines) == lines  # accolades et puces de code gardées
    print("selftest OK")


if __name__ == "__main__":
    selftest() if sys.argv[1:] == ["--selftest"] else main()
