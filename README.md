# slide2wiki

Glisse tes cours en PDF sur une page web : Claude les transforme en **wiki de révision rangé par sujet**
(pas par cours), avec des pages synthétiques, des liens entre notions et un rapport des passages douteux.
Chaque nouveau cours complète les pages existantes au lieu d'en créer des doublons.

```
navigateur ──PDF──▶ page de dépôt :8000 ─▶ Docling (texte du PDF, OCR si besoin)
                                        ─▶ plan du cours lu dans le PDF (chapitres, diapos)
                                        ─▶ Claude, partie par partie (pages proches du wiki fournies)
                                        ─▶ contrôles ─▶ data/wiki/<matière>/<thème>/<page>.md ─ commit Git
                                                           ├──▶ ton propre Wiki.js, via son dépôt Git (facultatif)
                                                           └──▶ ou le Wiki.js fourni :3000 (facultatif)
```

Trois façons de lire le résultat : **ton Wiki.js existant** (branché par son dépôt Git), le **Wiki.js fourni** avec slide2wiki, ou simplement les **fichiers Markdown** de `data/wiki/` (Obsidian, VS Code…).

## Ce qu'il faut

- **Docker** (Docker Desktop sur Windows/Mac, Docker Engine + plugin compose sur Linux). 4 Go de RAM libres conseillés (Docling).
- **Un abonnement Claude** (Pro ou Max) ou une **clé API** Anthropic. Chacun utilise le sien.

## Installation

```bash
git clone https://github.com/Kettelios-dev/slide2wiki.git && cd slide2wiki
cp .env.example .env
docker compose build
docker compose run --rm --entrypoint claude app setup-token   # ouvre un lien : se connecter à Claude, coller le code
```

Copier le jeton affiché (`sk-ant-oat01-…`) dans `.env`, ligne `CLAUDE_CODE_OAUTH_TOKEN=` (ou remplir `ANTHROPIC_API_KEY=` à la place).
Pour Wiki.js, enlever le `#` devant `COMPOSE_PROFILES=wikijs` et choisir `WIKI_ADMIN_EMAIL` / `WIKI_ADMIN_PASSWORD`.

```bash
docker compose up -d
```

- Page de dépôt : http://localhost:8000
- Wiki.js (si activé) : http://localhost:3000, avec le compte admin du `.env`. Le compte, la synchro avec `data/wiki` et le menu en arborescence sont configurés tout seuls au premier démarrage (`docker compose logs app` affiche `wikijs_setup: …`).
- Sans Wiki.js, les pages sont dans `data/wiki/` : à ouvrir avec Obsidian, VS Code ou n'importe quel éditeur Markdown.

Le premier `docker compose up` télécharge Docling (quelques Go) : compter quelques minutes.
Port 8000 ou 3000 déjà pris par un autre programme : changer `UPLOAD_PORT` / `WIKI_PORT` dans `.env` (et `WIKI_SITE_URL` avec).

## Brancher ton propre Wiki.js

Ton Wiki.js et slide2wiki partagent un **dépôt Git** (GitHub, GitLab, Gitea…) : ton Wiki.js le synchronise dans les deux sens, slide2wiki le récupère avant chaque cours (il voit donc les pages que tu as modifiées) et y envoie ses pages à la fin. Ton Wiki.js n'a pas besoin d'être sur la même machine.

1. **Un dépôt Git** pour ton wiki : un nouveau dépôt vide (privé, de préférence), ou celui que ton Wiki.js utilise déjà.
2. **Dans ton Wiki.js** : Administration → **Stockage** → **Git** :
   - activer, renseigner l'URL du dépôt et ses identifiants (*Authentication Type* « basic » avec un jeton, ou « ssh » avec une clé) ;
   - **Sync Direction : Bi-directional** (tes éditions vont dans le dépôt, les pages de slide2wiki en reviennent) ;
   - **Apply**, puis dans *Actions* : **Add Untracked Changes** (si ton wiki a déjà des pages), puis **Force Sync**.
   - Garder **Administration → Locales → Namespacing** désactivé : sinon Wiki.js range les pages dans un dossier par langue et ne lit pas celles de slide2wiki.
3. **Dans le `.env` de slide2wiki** : `WIKI_GIT_REMOTE` = l'URL du même dépôt, avec un accès en écriture :
   - HTTPS + jeton : `https://<utilisateur>:<jeton>@github.com/<utilisateur>/<depot>.git` (sur GitHub : jeton *fine-grained* limité à ce dépôt, droit *Contents : Read and write*) ;
   - ou SSH : `git@github.com:<utilisateur>/<depot>.git`, la clé privée dans `ssh/id_ed25519` et la ligne `./ssh:/run/ssh:ro` décommentée dans `docker-compose.yml`.
4. `docker compose up -d`, puis `docker compose logs app | grep gitsync` : « wiki existant repris » ou « dépôt distant branché ».

Au premier démarrage, si le dépôt contient déjà ton wiki, slide2wiki repart de lui (et range ses pages à côté des tiennes, un dossier par matière). Les nouvelles pages apparaissent dans ton Wiki.js à sa synchro suivante (5 min par défaut, ou *Force Sync*).
Dépôt injoignable avant un cours : le PDF reste « en attente », rien n'est perdu. Envoi impossible après un cours : le cours est fini et commité ici, l'envoi est refait au cours suivant.

## Utilisation

1. Choisir la **matière** (le nom du dossier dans le wiki, ex. « Réseaux ») et glisser un ou plusieurs PDF. Matière vide : Claude choisit le dossier.
2. La page affiche l'avancement : conversion Docling (environ 1 min pour 10 diapos sur CPU), puis « Claude : partie n/N ».
3. À la fin : pages créées et complétées. Le détail est dans `data/rapports/<cours>.txt`, avec les **doutes signalés par Claude** (erreur dans le cours, passage illisible, contradiction) : à relire.

Mettre tous les cours d'une matière dans la même matière : c'est ce qui permet de compléter les pages au lieu de les dupliquer.

### Demander sur ses cours

Sous la zone de dépôt, **Demander sur mes cours** : choisir ce que tu veux (**fiche de révision**, **exercices corrigés**, **synthèse**, **QCM** ou **réponse à une question**), la matière, le sujet (« le modèle OSI », « les pointeurs »…) et des précisions si besoin (« niveau examen », « 15 questions »).
- Claude ne s'appuie que sur les **pages de ton wiki** les plus proches du sujet (pas sur ses connaissances générales) ; si elles ne suffisent pas, il le dit au lieu d'inventer.
- Le résultat s'affiche sur la page. Avec **Enregistrer dans le wiki**, il devient une nouvelle page `<matière>/fiches/…` (ou `exercices/`, `syntheses/`, `qcm/`, `questions/`), avec la liste des pages utilisées en bas, en un commit (envoyé vers ton Wiki.js s'il est branché). Une page existante n'est jamais écrasée.
- Coût : un appel, ≈ 15 000 tokens d'entrée au plus (les pages sources sont plafonnées), noté dans `data/claude-usage.csv`.

### Adapter à ses cours

`regles.md` contient les consignes données à Claude (organisation, style des pages, exactitude). Remplir au moins la section **Contexte** (formation, remarques). Le fichier est relu à chaque cours : pas besoin de reconstruire l'image.

### Consommation

Claude ne reçoit jamais tout le wiki : seulement une partie du cours (≈ 24 000 caractères max), les règles, et les 5 pages existantes les plus proches. **Le coût par appel ne grandit pas avec le wiki** (≈ 15 000 tokens d'entrée au maximum). Mesures : un cours de 23 diapos = 1 appel (≈ 5k tokens en entrée, 4,5k en sortie) ; 82 diapos = 2 appels. Chaque appel est noté dans `data/claude-usage.csv`.
Quota de l'abonnement atteint : le PDF reste « en attente », les parties déjà faites sont gardées, et le bouton **Relancer** reprend là où ça s'était arrêté.

## Données (`data/`)

| Dossier | Contenu |
|---|---|
| `wiki/` | les pages (dépôt Git : un commit par cours, `git revert` pour annuler un cours) |
| `inbox/` | PDF déposés ; `_traites/` une fois faits, `_erreurs/` en cas d'échec |
| `md/` | texte extrait par Docling (cache : un PDF n'est converti qu'une fois) |
| `rapports/` | rapport de chaque cours |
| `a-revoir/` | pages refusées par les contrôles (page existante raccourcie, chemin invalide, page modifiée pendant l'intégration…) : rien n'est écrasé |

Sauvegarder `data/` suffit.

## Bon à savoir

- **Pas de mot de passe sur la page de dépôt** : par défaut elle n'écoute que sur ce PC (`UPLOAD_BIND=127.0.0.1`). Pour l'ouvrir au réseau local, mettre `0.0.0.0`, en sachant que tout le monde sur le réseau pourra consommer ton quota.
- **Corriger une page** : directement dans l'éditeur de Wiki.js (synchro Git dans les deux sens, toutes les 5 min) ou dans le fichier de `data/wiki/` (avec un commit). Si une page est modifiée pendant qu'un cours est en cours d'intégration, ta version est gardée et celle de Claude va dans `data/a-revoir/`.
- **Pages longues** : au-delà de 12 000 caractères, Claude ne reçoit plus que le plan de la page et y ajoute une section à la fin, au lieu de créer une page en double.
- **PDF scannés** : Docling fait l'OCR, mais le texte peut être moins bon et le plan du cours n'est pas détecté (Claude découpe alors par blocs).
- **Retraiter un cours** : supprimer ses pages (`git rm` + commit dans `data/wiki`) et le PDF de `data/inbox/_traites/`, puis le redéposer.
- **Linux** : si `data/` appartient à un autre utilisateur que 1000, mettre son uid/gid (`id`) dans `PUID` / `PGID`.

## Dépannage

- `docker compose logs -f app` : tout le traitement s'y affiche.
- « récupération du dépôt distant impossible » : URL, jeton (expiré ?) ou droits d'écriture de `WIKI_GIT_REMOTE` ; le jeton n'apparaît jamais dans les messages.
- « jeton Claude absent » : `.env` non rempli, ou modifié sans relancer `docker compose up -d`.
- « Docling injoignable » : le conteneur `docling` démarre encore (premier lancement) ; attendre puis **Relancer**.
- Auto-tests : `docker compose run --rm --entrypoint sh app -c "python3 integrer.py --selftest && python3 watcher.py --selftest && python3 structure.py --selftest && python3 upload.py --selftest && python3 gitsync.py --selftest && python3 demandes.py --selftest"`.

## Licence

MIT. Docling, Wiki.js et Claude Code ont leurs propres licences et conditions d'utilisation.
