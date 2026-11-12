#!/usr/bin/env python3
"""Synchro de data/wiki avec un dépôt Git distant (WIKI_GIT_REMOTE), celui que le Wiki.js de l'utilisateur synchronise.

Avant chaque cours : `pull` (les pages modifiées dans Wiki.js arrivent ici et sont prises en compte).
Après chaque cours : `push` (Wiki.js les récupère à sa synchro suivante). Sans WIKI_GIT_REMOTE : rien, tout reste local.
  python3 gitsync.py setup    # au démarrage (entrypoint.sh) : branche le dépôt, reprend le wiki existant s'il y en a un
"""
import os, subprocess, sys
import watcher as w

REMOTE = os.environ.get("WIKI_GIT_REMOTE", "")
BRANCH = os.environ.get("WIKI_GIT_BRANCH", "main")


class Erreur(Exception):
    """Dépôt distant injoignable ou refusé : on attend, rien n'est perdu (le travail est commité en local)."""


def git(*args, check=True):
    r = subprocess.run(["git", "-C", str(w.WIKI), *args], capture_output=True, text=True, encoding="utf-8")
    if check and r.returncode:
        raise Erreur(f"git {args[0]} : {(r.stderr or r.stdout).strip()[-300:]}")
    return r


def hide(text):
    """Le jeton éventuel de l'URL (https://user:jeton@…) n'apparaît jamais dans les messages."""
    return text.replace(REMOTE, "<WIKI_GIT_REMOTE>") if REMOTE else text


def setup():
    """Branche le dépôt distant. S'il contient déjà un wiki et qu'ici il n'y a que le commit de départ, on part du sien."""
    if not REMOTE:
        return "pas de dépôt distant (WIKI_GIT_REMOTE vide) : wiki local seulement"
    git("remote", "set-url" if git("remote", "get-url", "origin", check=False).returncode == 0 else "add", "origin", REMOTE)
    fetched = git("fetch", "-q", "origin", BRANCH, check=False)
    if fetched.returncode and "couldn't find remote ref" not in fetched.stderr:
        raise Erreur(f"dépôt distant injoignable : {hide(fetched.stderr.strip()[-300:])}")
    if fetched.returncode == 0 and git("rev-list", "--count", "HEAD").stdout.strip() == "1":
        git("reset", "-q", "--hard", f"origin/{BRANCH}")  # premier démarrage : le wiki existant devient la base
        return f"wiki existant repris depuis le dépôt distant ({BRANCH})"
    pull()
    return "dépôt distant branché" + ("" if push() else " (envoi en attente)")


def pull():
    """Récupère les changements du dépôt distant (pages éditées dans Wiki.js). Sans effet sans dépôt distant."""
    if not REMOTE:
        return
    r = git("pull", "-q", "--rebase", "--autostash", "origin", BRANCH, check=False)  # autostash : fichiers d'un arrêt brutal
    if r.returncode:
        git("rebase", "--abort", check=False)
        if "couldn't find remote ref" in r.stderr:  # dépôt distant encore vide : le premier push le remplira
            return
        raise Erreur(f"récupération du dépôt distant impossible : {hide((r.stderr or r.stdout).strip()[-300:])}")


def push():
    """Envoie les commits locaux. Rend False si l'envoi échoue : il sera refait au prochain cours (rien n'est perdu)."""
    if not REMOTE:
        return True
    for attempt in (1, 2):
        r = git("push", "-q", "origin", f"HEAD:{BRANCH}", check=False)
        if r.returncode == 0:
            return True
        if attempt == 1 and ("rejected" in r.stderr or "fetch first" in r.stderr):  # Wiki.js a poussé entre-temps
            try:
                pull()
            except Erreur:
                break
    print(f"gitsync: envoi impossible, refait au prochain cours : {hide(r.stderr.strip()[-300:])}", flush=True)
    return False


def selftest():
    import tempfile
    from pathlib import Path
    global REMOTE
    saved = REMOTE, w.WIKI
    run = lambda d, *a: subprocess.run(["git", "-C", str(d), *a], check=True, capture_output=True, text=True, encoding="utf-8")
    ident = ("-c", "user.email=t@t", "-c", "user.name=t")
    try:
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            remote, wikijs, w.WIKI = d / "remote.git", d / "wikijs", d / "wiki"
            subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
            subprocess.run(["git", "clone", "-q", str(remote), str(wikijs)], check=True, capture_output=True)
            (wikijs / "page.md").write_text("wiki existant\n")  # le wiki de l'utilisateur, déjà dans le dépôt
            run(wikijs, *ident, "add", "-A"); run(wikijs, *ident, "commit", "-q", "-m", "wiki.js"); run(wikijs, "push", "-q", "origin", "HEAD:main")
            w.WIKI.mkdir(); run(w.WIKI, "init", "-q", "-b", "main")
            run(w.WIKI, "config", "user.email", "s@s"); run(w.WIKI, "config", "user.name", "s")
            run(w.WIKI, "commit", "-q", "--allow-empty", "-m", "init wiki")
            REMOTE = str(remote)
            assert "repris" in setup() and (w.WIKI / "page.md").read_text() == "wiki existant\n"
            (w.WIKI / "cours.md").write_text("page de Claude\n"); run(w.WIKI, "add", "-A"); run(w.WIKI, "commit", "-q", "-m", "cours")
            # Wiki.js pousse une édition pendant ce temps : le push est refusé, gitsync récupère puis renvoie
            (wikijs / "page.md").write_text("édité dans Wiki.js\n")
            run(wikijs, *ident, "commit", "-qam", "edit"); run(wikijs, "push", "-q", "origin", "HEAD:main")
            assert push() is True
            run(wikijs, "pull", "-q", "--rebase", "origin", "main")
            assert (wikijs / "cours.md").exists() and (w.WIKI / "page.md").read_text() == "édité dans Wiki.js\n"
            (wikijs / "autre.md").write_text("x\n"); run(wikijs, *ident, "add", "-A"); run(wikijs, *ident, "commit", "-qm", "x")
            run(wikijs, "push", "-q", "origin", "HEAD:main")
            pull()
            assert (w.WIKI / "autre.md").exists()
            REMOTE = str(d / "absent.git")  # dépôt injoignable : pull lève Erreur, push rend False sans lever
            run(w.WIKI, "remote", "set-url", "origin", REMOTE)
            try:
                pull(); assert False
            except Erreur:
                pass
            assert push() is False
    finally:
        REMOTE, w.WIKI = saved
    print("selftest OK")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        selftest()
    elif sys.argv[1:] == ["setup"]:
        try:
            print("gitsync:", setup(), flush=True)
        except Erreur as e:  # jamais bloquant : la page de dépôt démarre quand même, pull réessaiera au prochain cours
            print(f"gitsync: {hide(str(e))}", flush=True)
