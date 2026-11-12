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
case "$COMPOSE_PROFILES" in
  *wikijs*) python3 /app/wikijs_setup.py & ;;
esac
exec python3 /app/upload.py
