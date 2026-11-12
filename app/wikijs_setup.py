#!/usr/bin/env python3
"""Configure Wiki.js au premier lancement : compte admin, synchro Git (lecture de data/wiki), menu en arborescence.

Lancé en arrière-plan par entrypoint.sh quand COMPOSE_PROFILES contient « wikijs ». Idempotent : relancé à chaque
démarrage, il ne fait que réappliquer les mêmes réglages.
"""
import json, os, sys, time
import requests

URL = os.environ.get("WIKIJS_URL", "http://wiki:3000").rstrip("/")
EMAIL, PASSWORD = os.environ.get("WIKI_ADMIN_EMAIL", ""), os.environ.get("WIKI_ADMIN_PASSWORD", "")
SITE = os.environ.get("WIKI_SITE_URL", "http://localhost:3000")

GIT = {  # stockage Git de Wiki.js 2 : dépôt local monté en /cours/wiki, lu seulement (« Pull from target »)
    "authType": "ssh",  # en « basic », Wiki.js préfixe https:// et casse un chemin local
    "repoUrl": "/cours/wiki", "branch": "main",
    "sshPrivateKeyMode": "path", "sshPrivateKeyPath": "/dev/null", "sshPrivateKeyContent": "",
    "verifySSL": True, "basicUsername": "", "basicPassword": "",
    "defaultEmail": "slide2wiki@localhost", "defaultName": "slide2wiki",
    "localRepoPath": "./data/repo", "gitBinaryPath": "",
}


def log(*a):
    print("wikijs_setup:", *a, flush=True)


def wait(timeout=600):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if requests.get(URL, timeout=5).status_code < 500:
                return True
        except requests.RequestException:
            pass
        time.sleep(5)
    return False


def gql(query, token=None, **variables):
    h = {"Authorization": f"Bearer {token}"} if token else {}
    r = requests.post(f"{URL}/graphql", json={"query": query, "variables": variables}, headers=h, timeout=30)
    r.raise_for_status()
    body = r.json()
    if body.get("errors"):
        raise RuntimeError(body["errors"][0].get("message"))
    return body["data"]


def login():
    d = gql('mutation($u:String!,$p:String!){authentication{login(username:$u,password:$p,strategy:"local")'
            '{responseResult{succeeded message} jwt}}}', u=EMAIL, p=PASSWORD)["authentication"]["login"]
    if not d["responseResult"]["succeeded"]:
        raise RuntimeError(d["responseResult"]["message"])
    return d["jwt"]


def main():
    if not EMAIL or len(PASSWORD) < 8:
        return log("WIKI_ADMIN_EMAIL et WIKI_ADMIN_PASSWORD (8 caractères min.) à remplir dans .env : Wiki.js non configuré")
    if not wait():
        return log(f"Wiki.js ne répond pas sur {URL}, abandon (relancé au prochain démarrage)")
    # premier lancement : l'assistant d'installation de Wiki.js n'expose que /finalize ; ensuite, cette route n'existe plus
    r = requests.post(f"{URL}/finalize", timeout=120, json={
        "adminEmail": EMAIL, "adminPassword": PASSWORD, "adminPasswordConfirm": PASSWORD, "siteUrl": SITE, "telemetry": False})
    if r.ok and r.headers.get("content-type", "").startswith("application/json") and r.json().get("ok"):
        log("compte admin créé, redémarrage de Wiki.js")
        time.sleep(10)
        wait()
    for attempt in range(30):  # après /finalize, Wiki.js met quelques secondes à accepter les connexions
        try:
            token = login()
            break
        except Exception as e:
            err = e
            time.sleep(5)
    else:
        return log(f"connexion admin impossible ({err}) : vérifier WIKI_ADMIN_EMAIL / WIKI_ADMIN_PASSWORD")
    target = {"isEnabled": True, "key": "git", "mode": "pull", "syncInterval": "PT5M",
              "config": [{"key": k, "value": json.dumps({"v": v})} for k, v in GIT.items()]}
    res = gql("mutation($t:[StorageTargetInput]!){storage{updateTargets(targets:$t){responseResult{succeeded message}}}}",
              token, t=[target])["storage"]["updateTargets"]["responseResult"]
    log("synchro Git :", "ok" if res["succeeded"] else res["message"])
    res = gql('mutation{storage{executeAction(targetKey:"git",handler:"importAll"){responseResult{succeeded message}}}}',
              token)["storage"]["executeAction"]["responseResult"]
    log("import des pages :", "ok" if res["succeeded"] else res["message"])
    try:
        gql("mutation{navigation{updateConfig(mode:TREE){responseResult{succeeded}}}}", token)
        log("menu en arborescence : ok")
    except Exception as e:  # réglage de confort : sans lui, le wiki marche
        log(f"menu en arborescence non appliqué : {e}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # jamais bloquant pour la page de dépôt
        log(f"échec : {type(e).__name__}: {e}")
        sys.exit(1)
