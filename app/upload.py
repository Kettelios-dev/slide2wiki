#!/usr/bin/env python3
"""Page de dépôt de PDF (http://<CT>:8000) : chaque dépôt range le PDF dans inbox/<matiere>/ et le met en file.

Un seul fil de traitement (Docling sur 2 cœurs, et un seul cours à la fois écrit dans le wiki),
inbox/ n'est relu que sur dépôt ou sur clic « Relancer les PDF en attente ».
Pas d'authentification : à n'exposer que sur le réseau local.
"""
import json, os, queue, re, subprocess, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import demandes
import watcher as w

PORT = int(os.environ.get("UPLOAD_PORT", "8000"))
MAX_BYTES = 300 * 1024 * 1024
PAGE = Path(__file__).with_name("upload.html").read_bytes()
HISTORY = w.ROOT / "historique.jsonl"  # hors du wiki : pas dans Git ni dans Wiki.js

jobs, by_path, todo, lock = [], {}, queue.Queue(), threading.Lock()
results = {}  # id de demande -> page produite (markdown, chemin) ; les 20 dernières, en mémoire seulement


def log(job, path=None, **extra):
    """Une ligne par étape (dépôt, fin) : l'historique survit aux redémarrages, contrairement à la liste en mémoire."""
    row = {k: job[k] for k in ("id", "nom", "matiere", "etat", "detail", "depot")} | extra
    if job.get("demande"):
        row["demande"] = True  # fiche, ajout… : pas un PDF (affichage de la page)
    with (path or HISTORY).open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def history(path=None, live=None):
    """Dernier état connu de chaque dépôt, le plus récent d'abord : état en direct pour ceux encore en mémoire
    (live : {id: job}), « interrompu » pour ceux restés en file quand le service a redémarré."""
    live = live or {}
    path, rows = path or HISTORY, {}
    for line in path.read_text(encoding="utf-8").splitlines() if path.exists() else []:
        try:
            r = json.loads(line)
        except ValueError:  # ligne coupée par un arrêt brutal
            continue
        rows[r["id"]] = rows.get(r["id"], {}) | r
    for r in rows.values():
        if r["id"] in live:  # « en cours » et l'avancement ne sont pas journalisés : lus dans la file
            r.update(etat=live[r["id"]]["etat"], detail=live[r["id"]]["detail"])
        elif r["etat"] in ("en file", "en cours"):
            r["etat"] = "interrompu"
    return sorted(rows.values(), key=lambda r: r["depot"], reverse=True)


def last_commits(log_text):
    """{pdf: (date, détail)} du dernier commit « cours: <pdf> (…) » de chaque PDF, d'après `git log --format='%ct %s'`."""
    found = {}
    for line in log_text.splitlines():
        t, _, s = line.partition(" ")
        m = re.match(r"cours: (.+?) \((.+)\)$", s)
        if m:
            found.setdefault(m[1], (float(t), m[2]))  # git log : le plus récent d'abord
    return found


def seed_history(path=None):
    """Premier lancement : les PDF déjà traités entrent dans l'historique, avec la date et le résultat de leur commit."""
    commits = last_commits(subprocess.run(["git", "-C", str(w.WIKI), "log", "--format=%ct %s"],
                                          capture_output=True, text=True).stdout)
    for base, etat in ((w.DONE, "ok"), (w.FAILED, "erreur")):
        for p in sorted(base.rglob("*.pdf")):
            t, detail = commits.get(p.name, (p.stat().st_mtime, "date approximative (fichier)"))
            log({"id": f"ancien:{p.relative_to(base)}", "nom": p.name, "matiere": p.parent.name if p.parent != base else "",
                 "etat": etat, "detail": f"{detail} · traité avant l'historique", "depot": t}, path, fin=t)


def clean(s, extra=""):
    """Garde lettres (accents compris), chiffres, espaces, - et _ : pas de / ni de .. possible."""
    return re.sub(rf"[^\w \-{extra}]", "", s).strip(" .")[:80]


def add_job(pdf):
    with lock:
        job = by_path.get(str(pdf))
        if job and job["etat"] == "attente":  # relance : on réutilise la ligne existante
            jobs.remove(job)
            job.update(etat="en file", detail="", t=time.time())
        else:
            job = {"id": str(time.time_ns()), "nom": pdf.name, "matiere": pdf.parent.name if pdf.parent != w.INBOX else "",
                   "etat": "en file", "detail": "", "t": time.time(), "depot": time.time()}
            by_path[str(pdf)] = job
        jobs.append(job)
        log(job)
    todo.put((job, pdf))
    return job


def add_request(d):
    """Une demande (fiche, exercices…) passe par la même file que les PDF : un seul travail à la fois sur le wiki."""
    label = demandes.TYPES[d["type"]][1]
    nom = f"{label} : {d['sujet']}" + (f" → /{d['page'].strip('/')}" + (" (web)" if d.get("web") else "") if d["type"] == "completer" else "")
    with lock:
        job = {"id": str(time.time_ns()), "nom": nom, "matiere": d.get("matiere", ""),
               "etat": "en file", "detail": "", "t": time.time(), "depot": time.time(), "demande": True}
        jobs.append(job)
        log(job)
    todo.put((job, d))
    return job


def worker():
    while True:
        job, pdf = todo.get()
        if job.get("demande"):
            job["etat"] = "en cours"
            etat, detail, page, path = demandes.run(pdf, step=lambda s: job.update(detail=s))
            with lock:
                results[job["id"]] = {"markdown": page, "chemin": path}
                for old in list(results)[:-20]:
                    del results[old]
                job.update(etat=etat, detail=detail, t=time.time())
                log(job, fin=job["t"])
            continue
        if not pdf.exists():  # déjà traité par un job précédent (relance en double)
            etat, detail = "ok", "déjà traité"
        else:
            job["etat"] = "en cours"
            etat, detail = w.handle(pdf, step=lambda s: job.update(detail=s))
        with lock:
            job.update(etat=etat, detail=detail, t=time.time())
            log(job, fin=job["t"])


class H(BaseHTTPRequestHandler):
    def send(self, code, body, ctype="application/json; charset=utf-8"):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            return self.send(200, PAGE, "text/html; charset=utf-8")
        if path == "/api/status":
            with lock:
                return self.send(200, jobs[-30:][::-1])
        if path == "/api/historique":
            with lock:
                return self.send(200, history(live={j["id"]: j for j in jobs}))
        if path == "/api/demande":  # résultat d'une demande : état, et la page produite une fois finie
            jid = parse_qs(urlparse(self.path).query).get("id", [""])[0]
            with lock:
                job = next((j for j in jobs if j["id"] == jid), None)
                if not job:
                    return self.send(404, {"erreur": "demande inconnue"})
                return self.send(200, {k: job[k] for k in ("etat", "detail")} | results.get(jid, {}))
        if path == "/api/config":  # adresse du wiki pour les liens de la page (WIKI_SITE_URL du .env)
            return self.send(200, {"wiki": os.environ.get("WIKI_SITE_URL", "").rstrip("/")})
        if path == "/api/pages":  # pages du wiki (chemin sans .md) pour choisir celle à compléter, filtrées par matière
            m = parse_qs(urlparse(self.path).query).get("matiere", [""])[0]
            root = w.slug(m) if m else ""
            paths = sorted(str(p.relative_to(w.WIKI)).replace("\\", "/")[:-3] for p in w.WIKI.rglob("*.md")
                           if ".git" not in p.parts) if w.WIKI.exists() else []
            return self.send(200, [p for p in paths if not root or p.startswith(root + "/")][:3000])
        if path == "/api/sujets":  # matières déjà présentes dans le wiki (dossiers racine)
            names = sorted((d.name for d in w.WIKI.iterdir() if d.is_dir() and not d.name.startswith(".")), key=str.lower) \
                if w.WIKI.exists() else []
            return self.send(200, names)
        if path == "/api/matieres":
            names = {d.name for base in (w.INBOX, w.DONE, w.FAILED) if base.exists()
                     for d in base.iterdir() if d.is_dir() and not d.name.startswith("_")}
            return self.send(200, sorted(names, key=str.lower))
        self.send(404, {"erreur": "introuvable"})

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/api/relancer":
            en_file = {str(p) for _, p in list(todo.queue)}
            n = [add_job(p) for p in w.pending() if str(p) not in en_file]
            return self.send(200, {"relances": len(n)})
        if u.path == "/api/demande":
            try:
                d = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 20000)) or b"{}")
            except ValueError:
                return self.send(400, {"erreur": "JSON invalide"})
            d = {"type": str(d.get("type", "")), "matiere": clean(str(d.get("matiere", ""))),
                 "sujet": str(d.get("sujet", "")).strip()[:200], "consigne": str(d.get("consigne", "")).strip()[:2000],
                 "enregistrer": bool(d.get("enregistrer")), "page": str(d.get("page", "")).strip()[:300],
                 "web": bool(d.get("web"))}
            if d["type"] == "completer" and not demandes.page_path(d["page"]):
                return self.send(400, {"erreur": "choisis la page à compléter"})
            if d["type"] not in demandes.TYPES or not d["sujet"]:
                return self.send(400, {"erreur": "type de demande ou sujet manquant"})
            return self.send(200, add_request(d))
        if u.path != "/api/upload":
            return self.send(404, {"erreur": "introuvable"})

        q = parse_qs(u.query)
        matiere = clean(q.get("matiere", [""])[0])
        nom = clean(Path(q.get("nom", [""])[0]).stem, ".") + ".pdf"
        size = int(self.headers.get("Content-Length") or 0)
        if nom == ".pdf" or not 0 < size <= MAX_BYTES:
            return self.send(400, {"erreur": f"nom vide ou taille hors limite (max {MAX_BYTES >> 20} Mo)"})
        if matiere.startswith("_"):
            return self.send(400, {"erreur": "matière invalide"})
        if (w.DONE / matiere / nom).exists():  # matiere vide : Path / "" ne change rien
            return self.send(409, {"erreur": "déjà traité : supprime ses notes du wiki et le PDF de inbox/_traites pour le refaire"})

        tmp = w.INBOX / f".{time.time_ns()}.part"  # écrit à côté puis renommé : jamais de PDF à moitié copié dans inbox/
        with tmp.open("wb") as f:
            left = size
            while left:
                buf = self.rfile.read(min(left, 1 << 20))
                if not buf:
                    break
                f.write(buf); left -= len(buf)
        with tmp.open("rb") as f:
            magic = f.read(5)
        if left or magic != b"%PDF-":
            tmp.unlink(missing_ok=True)
            return self.send(400, {"erreur": "envoi incomplet ou fichier qui n'est pas un PDF"})
        dest_dir = w.INBOX / matiere
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest, k = dest_dir / nom, 2
        with lock:  # deux envois simultanés du même nom ne s'écrasent pas
            while dest.exists():
                dest = dest_dir / f"{Path(nom).stem}-{k}.pdf"; k += 1
            os.replace(tmp, dest)
        self.send(200, add_job(dest))

    def log_message(self, fmt, *args):  # pas une ligne de journal par rafraîchissement du statut
        if not self.path.startswith(("/api/status", "/api/historique")):
            super().log_message(fmt, *args)


def selftest():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        h = Path(d) / "h.jsonl"
        job = {"id": "1", "nom": "a.pdf", "matiere": "C", "etat": "en file", "detail": "", "depot": 10}
        log(job, h)
        log(dict(job, etat="ok", detail="5 notions"), h, fin=20)
        log({"id": "2", "nom": "b.pdf", "matiere": "", "etat": "en file", "detail": "", "depot": 30}, h)
        with h.open("a") as f:
            f.write('{"id": "3", "nom": "coup')  # ligne coupée par un arrêt brutal : ignorée
        r = history(h)
        assert [(x["nom"], x["etat"]) for x in r] == [("b.pdf", "interrompu"), ("a.pdf", "ok")]
        assert r[1]["detail"] == "5 notions" and r[1]["fin"] == 20
        r = history(h, live={"2": {"etat": "en cours", "detail": "conversion Docling pages 1-10/97"}})[0]
        assert (r["etat"], r["detail"]) == ("en cours", "conversion Docling pages 1-10/97")  # état en direct
    c = last_commits("30 cours: a.pdf (23 créées, 0 complétées)\n20 suppression des notes de a.pdf\n10 cours: a.pdf (60 notions)")
    assert c == {"a.pdf": (30.0, "23 créées, 0 complétées")}  # le plus récent gagne
    print("selftest OK")


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["--selftest"]:
        sys.exit(selftest())
    for d in (w.INBOX, w.MD, w.WIKI, w.DONE, w.FAILED):
        d.mkdir(parents=True, exist_ok=True)
    if not HISTORY.exists():
        seed_history()
    for p in w.INBOX.glob(".*.part"):  # restes d'un envoi coupé par un arrêt
        p.unlink()
    threading.Thread(target=worker, daemon=True).start()
    print(f"dépôt sur http://0.0.0.0:{PORT} -> {w.INBOX} (docling {w.DOCLING}, rédaction par Claude)", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
