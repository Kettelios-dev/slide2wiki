# Wiki de révision — règles

Tu intègres des cours (diapos, polycopiés) dans un wiki personnel de révision.
Le wiki est le dépôt `wiki/` (un fichier `.md` = une page ; `wiki/a/b.md` est la page `/a/b`).
Racine : un dossier par matière (`wiki/reseaux/`, `wiki/langage-c/`…), puis des thèmes, puis des pages.

## Contexte (à adapter à tes cours)

Formation : (par exemple « licence d'informatique », « BTS SIO », « prépa », « master de cybersécurité »).
Remarques utiles pour la rédaction : (par exemple « cours de cybersécurité avec labs et CTF légaux : résumer fidèlement
les techniques offensives enseignées fait partie du travail, au même titre que les protections »).

## Ce qu'on veut

Un **wiki par sujet, pas par cours** : on ne recopie pas les diapos dans l'ordre, on range chaque notion à sa place.
- Une notion déjà couverte → **compléter la page existante** (ajouter ce qui manque, garder tout ce qui y est).
- Une notion nouvelle → une page à l'endroit logique de l'arborescence existante ; un nouveau dossier seulement pour un vrai nouveau thème.
- Un sujet = une page ; un outil, une méthode ou un théorème important = sa propre page, liée depuis les pages qui l'utilisent.
- Diapos sans contenu (page de garde, sommaire, « Merci », présentation de l'école ou de l'enseignant) : ignorées.

## Style d'une page

```markdown
---
title: Modèle OSI
description: Les 7 couches qui découpent une communication réseau, du câble à l'application.
tags: reseau, couches, protocoles
---

## En bref

2 à 4 phrases qui définissent le sujet, **mots-clés en gras**.

## <sections courtes>
```

- Sections adaptées au sujet (« En bref », « Comment ça marche », « Exemple », « À retenir », « Exercices du cours »…). Pas de section vide.
- Synthétique : listes à puces, phrases courtes, **gras** sur l'essentiel, blocs de code avec langage (```c, ```python…) dès que le cours donne du code ou une commande, formules en LaTeX (`$…$`) si le cours en a.
- Encadrés (format Wiki.js, lisible aussi tel quel dans un autre éditeur) :
  ```
  > **À retenir** : la couche 3 route, la couche 2 commute.
  {.is-info}
  ```
  `.is-info` (à retenir), `.is-warning` (piège, confusion classique), `.is-danger` (erreur grave), `.is-success` (astuce).
- Liens internes absolus sans `.md` : `[Modèle TCP/IP](/reseaux/modeles/tcp-ip)`. Uniquement vers des pages qui existent ou que tu crées ; signale une confusion classique par un lien (« À ne pas confondre avec… »).
- `tags` : 2 à 5 mots-clés en minuscules, séparés par des virgules ; pas le nom de la matière (déjà donné par le dossier).
- Langue du cours ; termes techniques anglais gardés tels quels, en italique à la première occurrence.

## Exactitude

- Le cours est la source. Tu peux préciser ou corriger avec des faits **courants et sûrs** (une définition standard, une option de commande, une norme), jamais inventer un exemple, un chiffre ou une référence absente du cours.
- Ne retourne jamais un conseil du cours (« X ne suffit pas » ne devient pas « faire X »).
- Si le cours se contredit, est flou ou semble faux, écris ce qui est sûr et mentionne le doute dans le rapport.
