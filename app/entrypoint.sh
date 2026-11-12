#!/bin/sh
# Prépare data/ (dossiers et dépôt Git du wiki) puis lance la page de dépôt ; configure Wiki.js s'il est activé.
set -e
mkdir -p "$COURS_ROOT/inbox" "$COURS_ROOT/md" "$COURS_ROOT/wiki"
if [ ! -d "$COURS_ROOT/wiki/.git" ]; then
  git -C "$COURS_ROOT/wiki" init -q -b main
  git -C "$COURS_ROOT/wiki" config user.name "${GIT_NAME:-slide2wiki}"
  git -C "$COURS_ROOT/wiki" config user.email "${GIT_EMAIL:-slide2wiki@localhost}"
  git -C "$COURS_ROOT/wiki" commit -q --allow-empty -m "init wiki"
fi
# Wiki.js pousse ses modifications dans ce dépôt non-bare : met à jour les fichiers au lieu de refuser
git -C "$COURS_ROOT/wiki" config receive.denyCurrentBranch updateInstead
# clé SSH montée depuis l'hôte (droits trop ouverts sous Windows : ssh la refuserait) -> copie privée en 600
if ls /run/ssh/id_* >/dev/null 2>&1; then
  mkdir -p "$HOME/.ssh" && cp /run/ssh/id_* "$HOME/.ssh/" && chmod 700 "$HOME/.ssh" && chmod 600 "$HOME"/.ssh/id_*
fi
# dépôt Git de ton propre Wiki.js (WIKI_GIT_REMOTE) : branché ici, wiki existant repris au premier démarrage
python3 /app/gitsync.py setup
case "$COMPOSE_PROFILES" in
  *wikijs*) python3 /app/wikijs_setup.py & ;;
esac
exec python3 /app/upload.py
