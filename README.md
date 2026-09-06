# localcoder (Python)

Réécriture complète en Python de [localcoder](../localcoder) (la version
Node) — même philosophie : un agent de code minimal pour Ollama, contexte
explicite, empreinte mémoire réduite. La raison du passage en Python :
le menu interactif du terminal fait main en Node (mode raw + codes ANSI)
demandait de tout réinventer soi-même ; ici c'est
[`prompt_toolkit`](https://python-prompt-toolkit.readthedocs.io/), une lib
mature construite exactement pour ça (complétion, navigation clavier,
Ctrl+C/Ctrl+D propres) — une seule vraie dépendance, tout le reste est la
bibliothèque standard.

▍ **localcoder** 🐼 — Lazzy le panda, ton agent de code local-first, contexte
explicite, zéro bloat.

Ce que ça fait :
- Parle directement à l'API native d'Ollama (`/api/chat`), pas à la couche
  OpenAI-compatible — pour fixer `num_ctx` explicitement à chaque requête.
  `urllib` (stdlib), pas de client HTTP tiers.
- Expose 6 tools de base — `read_file`, `list_dir`, `search_code`,
  `edit_file`, `write_file`, `run_shell` — plus `semantic_search` (si un
  index existe, `/index build`) et `find_definition`/`find_references`
  (si Universal Ctags est installé). Chaque tool optionnel n'apparaît dans
  la liste envoyée au modèle que si sa dépendance est réellement là —
  sinon zéro coût en tokens.
- Deux tools de délégation à des sous-agents, même endpoint/modèle Ollama que
  la conversation principale mais chacun avec sa propre fenêtre de contexte
  jetable — le modèle principal ne récupère que leur réponse finale, jamais
  leurs appels d'outils intermédiaires, pour ne pas saturer son propre
  contexte sur une recherche large. Les deux sont en lecture seule (pas
  d'écriture de fichier, pas de `run_shell`) puisqu'ils tournent sans
  personne pour approuver une action pendant leur exécution :
    - `spawn_subagent` (mode **loop**) — une tâche d'investigation à la
      fois, séquentielle : repérer comment quelque chose fonctionne dans
      une zone du code, par exemple.
    - `spawn_subagents` (mode **graph**) — plusieurs tâches indépendantes à
      la fois, en parallèle (4 branches max), quand le travail se découpe
      naturellement en parties qui ne dépendent pas les unes des autres
      (ex. investiguer le module A et le module B séparément) ; chaque
      branche renvoie sa propre réponse, et c'est au modèle principal de
      les recomposer en une réponse finale.
- `search_code` utilise `ripgrep` s'il est installé, sinon `grep` en repli.
- `edit_file` fait un remplacement ciblé (ancien texte → nouveau texte,
  doit matcher exactement une fois) plutôt que de réécrire tout le fichier.
- Demande confirmation avant toute action qui modifie quelque chose
  (écriture de fichier, commande shell) — sauf en mode `--yolo`.
- Contexte explicite : `--context`, config, ou `/context add` en session —
  fichier, dossier ou glob — jamais de scan automatique du projet.
- Rôle explicite : un seul actif à la fois (`--role`, `/role use`).
- Sessions nommées et persistantes (`--session <nom>`), historique + rôle,
  d'une invocation à l'autre.
- Recherche sémantique optionnelle (`/index build`) et recherche par
  symboles (`find_definition`/`find_references`, via Universal Ctags).
- Interface plein écran dans un vrai terminal, façon Vibe : historique
  défilable en haut, ligne de saisie toujours visible en bas, menu `/`
  centré à l'écran — voir "Interface plein écran" plus bas.
- Warm-up au démarrage : un appel silencieux à Ollama précharge le modèle
  en mémoire pendant qu'un petit panda anime la barre de statut, pour que
  ton premier vrai message ne paie pas le coût du cold-start
  (`--no-warm-up` pour désactiver).
- Change de modèle, de température ou de taille de contexte en cours de
  session (`/model use`, `/set temperature`, `/set num_ctx`) — sans
  relancer localcoder.
- Stats à la `ollama run --verbose` : `/stats` pour un résumé cumulé de la
  session, `/verbose` (ou `--verbose`) pour un détail après chaque réponse
  (durées et débit de prompt-eval/eval).
- `/summary` : demande au modèle un récapitulatif de la conversation en
  cours, affiché à l'écran ou écrit dans un fichier.
- `/search` et `/find` : recherche directe (texte ou symbole) sans passer
  par le modèle — pour toi, pas pour l'agent.
- Mentions `@chemin` dans un message : `corrige @src/auth.js` ajoute ce
  fichier au contexte au vol, en plus/à la place de `/context add`.
- Rôle (une seule persona active, `/role use`) **et** skills (plusieurs
  actifs en même temps, `/skill use`) — les deux peuvent être créés depuis
  l'interface avec `/role create`/`/skill create`, sans sortir de
  localcoder.
- Mode dév `--watch` : lance le REPL dans un sous-processus et le recharge
  automatiquement dès qu'un fichier surveillé change — pratique pour
  développer localcoder lui-même (voir « Mode dév (`--watch`) » plus bas).
- `Ctrl+C` interrompt la génération en cours (le tour, pas tout le
  programme) ; à la ligne de commande vide, `Ctrl+C`/`Ctrl+D` quitte.
- Aucune étape de build : `pip install`, puis `python -m localcoder`.

## Installation

Prérequis : Python 3.10+, un serveur Ollama qui tourne en local avec un
modèle déjà pull (`devstral-small-2` ou `qwen3-coder:30b`).

```bash
cd localcoder-py
pip install -r requirements.txt         # juste prompt_toolkit
# ou, pour avoir la commande `localcoder` disponible partout :
pip install -e .
```

## Utilisation

Depuis la racine du projet sur lequel tu veux travailler :

```bash
# sans installation (juste prompt_toolkit dans le PYTHONPATH) :
PYTHONPATH=/chemin/vers/localcoder-py python3 -m localcoder

# ou, après `pip install -e .` :
localcoder
```

**Pour développer localcoder lui-même** : tout-en-un avec `./dev.sh`.

```bash
./dev.sh               # venv + prompt_toolkit auto, puis lancement avec --watch (recharge à chaque modif)
./dev.sh --no-watch    # sans rechargement automatique
./dev.sh --warm-up     # active le preload du modèle au démarrage
./dev.sh --session x   # tout autre argument passe tel quel à localcoder
```

Le script crée `.venv/` et installe les dépendances au premier lancement,
choisit un Python ≥ 3.10 (3.11/3.12 s'ils existent, sinon 3.10), et démarre
par défaut avec `--no-warm-up` — en mode `--watch`, chaque rechargement
relance un processus et on ne veut pas repayer le preload du modèle à
chaque édition. Utilise `--warm-up` si tu veux démarrer une vraie
conversation.

Alias pratique dans ton `.zshrc` :

```bash
alias localcoder="PYTHONPATH=/chemin/vers/localcoder-py python3 -m localcoder"
```

### Commandes

- `/index build [nom] [modèle]` — (re)construit l'index sémantique ; ne
  ré-embedde que les fichiers dont le contenu a changé depuis le dernier
  build. Sans argument, reconstruit l'index actif avec `embed_model` de la
  config. Un nom permet de garder plusieurs index côte à côte (chacun son
  fichier), par exemple pour comparer deux modèles d'embedding ; construire
  un index le rend actif
- `/index use <nom>` — bascule l'index actif vers un index déjà construit
- `/index list` — liste tous les index construits pour ce projet (modèle,
  nombre de fichiers/chunks, lequel est actif)
- `/index status` — nombre de fichiers/chunks indexés, modèle utilisé,
  date de dernière construction, pour l'index actif
- `/session save [nom]` — nomme (si besoin) et sauvegarde la session
  courante ; sans argument, sauvegarde sous le nom déjà actif
- `/session load <nom>` — charge une session sauvegardée (historique +
  rôle + contexte associés), remplace l'état courant
- `/session new <nom>` — démarre une session vierge sous ce nom
- `/session list` — liste les sessions sauvegardées pour ce projet
- `/role use <nom>` — charge `roles/<nom>.md` (ou
  `~/.localcoder/roles/<nom>.md`) et remplace le rôle actif
- `/role list` — liste les rôles disponibles (projet + global)
- `/role create <nom>` — écrit un nouveau rôle depuis l'interface (voir
  "Créer un rôle ou un skill depuis l'interface")
- `/role clear` — désactive le rôle courant
- `/skill use <nom>` — active un skill ; contrairement au rôle, plusieurs
  skills peuvent être actifs en même temps
- `/skill list` — liste les skills disponibles (projet + global), marque
  ceux actifs
- `/skill create <nom>` — écrit un nouveau skill depuis l'interface, même
  flux que `/role create`
- `/skill clear` — désactive tous les skills actifs
- `/context add <path|glob>` — ajoute un fichier, dossier ou pattern glob
  au contexte pour le reste de la session
- `/context list` — affiche le contexte actuellement chargé
- `/context clear` — vide le contexte (indépendant de `/reset`)
- `/context save <nom>` — sauvegarde les chemins/globs actuellement chargés
  comme un jeu de contexte nommé, indépendant de `/session` (qui regroupe
  contexte + rôle + skills + historique)
- `/context load <nom>` — recharge un jeu de contexte sauvegardé, remplace
  le contexte actuel
- `/context sets` — liste les jeux de contexte sauvegardés pour ce projet
- `/model use <nom>` — change de modèle pour le reste de la session
- `/model list` — liste les modèles déjà pull dans Ollama (`/api/tags`)
- `/set temperature <val>` — change la température pour le reste de la
  session
- `/set num_ctx <val>` — change la taille de la fenêtre de contexte pour
  le reste de la session
- `/set max_subagents <val>` — change le nombre max de branches parallèles
  pour `spawn_subagents` pour le reste de la session (défaut 4, voir
  `--max-subagents` ; chaque branche est une conversation + requête Ollama
  de plus en mémoire, à ajuster selon la RAM dispo)
- `/stats` — résumé cumulé de la session (modèle, tours, tokens, temps
  total, % de la fenêtre de contexte utilisé au dernier tour)
- `/verbose` — active/désactive le détail par tour façon
  `ollama run --verbose` (durées + débit prompt-eval/eval)
- `/debug` — active/désactive la trace complète (traceback Python) sur les
  erreurs, au lieu d'un message court ; utile pour comprendre pourquoi
  quelque chose a vraiment échoué
- `/socratic` — active/désactive le mode socratique : au lieu de donner
  directement la réponse ou le code, le modèle guide avec des questions
  et de petits indices, pour continuer à apprendre plutôt que juste copier
- `/summary [fichier]` — demande un récapitulatif de la conversation ;
  sans argument il s'affiche à l'écran, avec un chemin il est écrit dedans
- `/search <motif>` — recherche directe dans le projet (texte/regex),
  sans passer par le modèle
- `/find <symbole>` — définition + références d'un symbole, en direct
  (voir "Recherche par symboles")
- `@chemin` dans un message normal — ajoute ce fichier/dossier au
  contexte avant d'envoyer le message (raccourci pour `/context add`)
- `/reset` — vide l'historique de conversation (garde rôle, skills et
  contexte)
- `/restart` — relance localcoder proprement : re-démarrage complet avec le
  même dossier et les mêmes options (la session est autosauvegardée avant)
- `/help` — affiche cette liste avec les descriptions
- `/exit` — quitte
- `Ctrl+C` — interrompt la génération en cours si le modèle est en train
  de répondre (le tour est annulé, la session continue) ; à la ligne de
  commande vide, `Ctrl+C` ou `Ctrl+D` quitte proprement

### Interface plein écran

Dans un vrai terminal (pas dans un pipe/script), localcoder s'ouvre en
plein écran façon Vibe plutôt que d'imprimer du texte qui défile dans le
terminal normal :

```
┌──────────────────────────────────────────────────────────┐
│  historique défilable (bannière, réponses, outils...)     │
│  ...                                                       │
├──────────────────────────────────────────────────────────┤
│  🐼 devstral-small-2 · role: tdd · context: 2 · ~1.2k/8k   │  ← barre de statut
├──────────────────────────────────────────────────────────┤
│  you> _                                                     │  ← toujours en bas
└──────────────────────────────────────────────────────────┘
```

- L'écran est effacé au lancement — la bannière (mascotte, modèle, rôle,
  skills, contexte, commandes) s'affiche en haut de l'historique, pas
  dans l'ancien contenu du terminal.
- La ligne de saisie reste **toujours visible en bas de l'écran**, même
  pendant que l'historique défile au-dessus — plus besoin de la
  retrouver après une longue réponse. Elle occupe maintenant plusieurs
  lignes de haut (3 à 8 selon ce qui est tapé) au lieu d'une seule ligne
  serrée.
- L'historique se scrolle avec **Page Haut / Page Bas** — testé de bout
  en bout, y compris que la vue revient bien tout en bas dès qu'un
  nouveau message arrive. La molette de la souris, elle, ne fait rien :
  c'est volontaire, pour que le **copier-coller natif du terminal**
  marche normalement (clic-glisser + Cmd/Ctrl+C comme d'habitude), sans
  aucune touche à maintenir. C'est le compromis retenu après essai de
  l'inverse (souris activée pour le scroll) : ça cassait la sélection de
  texte, ce qui gênait plus que de perdre le scroll à la molette.
- Chaque nouveau message que tu envoies est précédé d'une fine barre de
  séparation, pour repérer un échange dans le défilement d'un coup d'œil.
- Les réponses du modèle sont préfixées par **Lazzy>** (plus "assistant>")
  et s'écrivent token par token sur une seule ligne qui s'enroule, comme
  dans un vrai terminal.
- Le code dans une réponse est mis en forme au fur et à mesure : un bloc
  ```` ```...``` ```` reçoit son propre panneau (fond distinct), et le
  `code entre backticks simples` ressort de la prose en couleur — plus
  besoin de plisser les yeux pour repérer où le code commence et finit.
- Pendant qu'Ollama réfléchit ou pendant le warm-up, la barre de statut
  affiche un petit panda 🐼 qui roule dans une barre de progression
  animée, à la place du statut habituel.
- `Ctrl+C` interrompt vraiment une réponse en cours, même si Ollama est
  en train de calculer et n'a encore rien renvoyé — avant, dans ce cas
  précis, ça pouvait remonter une fausse "erreur timeout" au lieu
  d'annuler proprement le tour.
- Les commandes lentes (`/summary` notamment, qui interroge le modèle)
  tournent maintenant en arrière-plan comme un vrai message : le spinner
  continue de bouger et `Ctrl+C` les interrompt, au lieu de figer
  l'interface jusqu'à la fin.

Taper `/` ouvre un menu **centré à l'écran, dans un cadre** (comme un
panneau de settings classique), avec une vraie explication pour chaque
commande (pas juste son nom) et une ligne de raccourcis en dessous :

```
        ┌─ /role use ────────────────────────────────────────────┐
        │  /role use    — Switch the active role (persona)...    │
        │  /role list   — List roles available in roles/...      │
        │  /role create — Write a new role .md file...           │
        │  /role clear  — Deactivate the current role...         │
        └──────────────────────────────────────────────────────────┘
              ↑/↓ Move  PgUp/PgDn Page  Tab Complete  Enter Select  Esc Clear
```

- **↑ / ↓** — déplace la sélection dans le menu. La fenêtre affichée suit
  la sélection : quand tout ne tient pas dans le cadre, une ligne
  `▲ N more above` / `▼ N more below` indique ce qui reste caché de chaque
  côté — plus rien n'est hors de portée
- **PgUp / PgDn** — quand le menu est ouvert, fait défiler la sélection
  d'une page entière (pratique pour parcourir la trentaine de commandes) ;
  dehors du menu, ils font défiler l'historique comme avant
- **Tab** — complète la ligne avec l'item sélectionné **en entier** (sans
  jamais valider) — pratique pour descendre dans `/role use `, `/context
  add ` etc. sans perdre ce qui a déjà été tapé
- **Entrée** — si un item du menu est en surbrillance, le sélectionne
  (et l'exécute tout de suite s'il n'a besoin de rien d'autre) ; sinon
  envoie la ligne telle quelle
- **Échap** — vide la ligne (ferme le menu)

Cinq commandes ont un sous-menu qui liste des valeurs réelles plutôt que
du texte statique : `/role use` (rôles sur disque), `/skill use` (skills
sur disque), `/session load` (sessions déjà sauvegardées), `/model use`
(modèles déjà pull dans Ollama) et `/context add` — voir juste en dessous.

Sur une entrée non-interactive (script, pipe, tests), localcoder détecte
l'absence de vrai TTY et repasse automatiquement sur le mode ligne par
ligne classique (`input()`/`print()`, pas de plein écran) — rien ne
change pour l'usage scripté ou pour la suite de tests.

Les descriptions longues du menu passent maintenant à la ligne au lieu
d'être coupées en plein milieu d'un mot sur les terminaux étroits.

### Naviguer les fichiers pour `/context add`

`/context add` a son propre mini-explorateur de fichiers dans le menu `/` :
taper `/context add ` liste le contenu d'un dossier, un niveau à la fois —
pas un gros dump à plat de tout le projet. Le point de départ est `src/`
(ou `source/` si `src/` n'existe pas), pas la racine du projet, puisque
c'est presque toujours là que se trouvent les fichiers pertinents :

```
you> /context add
              components/
              auth.js
              index.mjs
```

Tab ou Entrée sur un dossier (il se termine par `/`) descend dedans sans
rien valider ; sur un fichier, ça l'ajoute au contexte. `node_modules`,
`.git` et les fichiers cachés sont exclus.

Le contexte n'est pas enfermé dans le projet courant — c'est volontaire,
pour pouvoir piocher un fichier dans un projet voisin (un frontend et un
backend développés côte à côte, par exemple) sans en faire une galère :

- Taper `../` remonte au-dessus de la racine du projet et continue à
  naviguer dossier par dossier normalement à partir de là.
- Taper un chemin absolu (`/Users/.../autre-projet/...`) ou commençant
  par `~/` navigue n'importe où sur le disque, avec le même
  Tab/aperçu de dossier.

### Options

```bash
localcoder --model devstral-small-2 --num-ctx 8192 --temperature 0.2
localcoder --host http://localhost:11434
localcoder --yolo   # auto-approuve write_file, edit_file et run_shell (à utiliser avec prudence)
localcoder --context README.md --context src/auth   # répétable, fichier ou dossier
localcoder --context "docs/adr/*.md"                # glob — charge le contenu de chaque fichier
localcoder --role code-review
localcoder --session auth-bug --role code-review   # reprend/démarre le fil "auth-bug"
localcoder --verbose         # ou -v : détail par tour dès le départ
localcoder --no-warm-up      # saute le préchargement du modèle au démarrage
localcoder --max-subagents 2 # limite les branches parallèles de spawn_subagents (défaut 4)
# Mode dév : relance à chaque changement de fichier surveillé
localcoder --watch
localcoder --watch-path roles --watch-path tests/base.py   # surveille aussi ces chemins
```

### Mode dév (`--watch`)

`--watch` lance le REPL dans un sous-processus et le **recharge tout seul
dès qu'un fichier surveillé change** — l'équivalent d'un `--reload` de
serveur pour cette interface interactive. Par défaut, les sources de
`localcoder/`, `pyproject.toml` et `requirements.txt` sont surveillés :
parfait pour développer localcoder lui-même.

- Le sous-processus garde le vrai terminal (stdin/stdout/stderr passés
  tels quels) — l'interface plein écran fonctionne à l'identique.
- `--watch-path <chemin>` (répétable) ajoute des chemins à surveiller :
  fichiers ou dossiers.
- Un fichier changé fait **relancer** la session en cours (la conversation
  n'est pas sauvegardée automatiquement… sauf si elle est nommée via
  `--session`, comme d'habitude).
- Si le code écrit plante localcoder (ex. erreur de syntaxe en plein
  développement), le watcher ne relance pas en boucle : il reste en vie et
  relancera dès que tu corriges le fichier.
- Quitter le REPL (`/exit`) quitte aussi le watcher.

Le plus simple pour l'utiliser : `./dev.sh` (voir « Utilisation ») — il crée
`.venv/`, installe les dépendances et lance `--watch` automatiquement.

### Contexte

`--context` (et `/context add` en session) accepte un fichier, un dossier
ou un glob :
- **Fichier** → lu en entier (tronqué à 6000 caractères) et injecté comme
  message système, avant même ton premier message.
- **Dossier** → transformé en arborescence (3 niveaux, `node_modules`/
  `.git` etc. exclus).
- **Glob** (`docs/adr/*.md`) — un seul `*` dans le dernier segment du
  chemin, charge le contenu complet de chaque fichier qui matche.

C'est volontairement toi qui décides quoi charger — pas de scan
automatique du projet entier.

### Rôles et skills

Un rôle est un simple fichier `.md`/`.txt` dans `roles/` (projet) ou
`~/.localcoder/roles/` (global). Un seul rôle actif à la fois : `/role use
tdd` remplace le rôle courant plutôt que de s'accumuler avec lui — c'est
la "persona" de la session (revue de code, TDD, un stack particulier...).

Un skill est le même genre de fichier, dans `skills/` ou
`~/.localcoder/skills/`, mais **plusieurs peuvent être actifs en même
temps** : `/skill use write-tests` puis `/skill use commit-messages`
activent les deux, chacun ajoute son propre contenu au contexte envoyé au
modèle. Utile pour des consignes ponctuelles ("comment on écrit un test
ici", "le format de nos messages de commit") qui n'ont pas besoin de
remplacer toute la persona active.

Trois exemples de rôles fournis dans `roles.example/` (`code-review`,
`tdd`, `vue-quasar`) :

```bash
mkdir -p ~/.localcoder/roles
cp roles.example/*.md ~/.localcoder/roles/
```

### Créer un rôle ou un skill depuis l'interface

Pas besoin de sortir dans un éditeur : `/role create <nom>` (ou `/skill
create <nom>`) bascule la ligne de saisie en mode capture — tape le
contenu ligne par ligne, une ligne contenant juste `.` sauve le fichier
dans `roles/<nom>.md` (ou `skills/<nom>.md`), une ligne contenant juste
`!` annule sans rien écrire :

```
you> /role create pair-programmer
[role] Type the role content below. A line with just "." saves it, a line with just "!" cancels.
role> Think out loud before every change.
role> Ask before any refactor touching more than one file.
role> .
[role] Saved "pair-programmer" to roles/pair-programmer.md.
```

Ce flux marche à l'identique en mode non-interactif (script/pipe) — les
mêmes lignes tapées une par une, dans l'ordre.

### Sessions

Une session = un fil de travail nommé avec son propre historique, son
rôle et ses skills actifs, sauvegardé dans
`.localcoder/sessions/<nom>.json` à la racine du projet.

```bash
localcoder --session auth-bug --role code-review
# ... tu discutes, corriges, etc. ...
localcoder --session auth-bug   # reprend exactement où tu en étais
```

Nommer une session (`--session`, `/session new`, ou `/session save`)
déclenche la sauvegarde automatique après chaque message — sans nom de
session, rien n'est écrit sur disque.

```bash
echo ".localcoder/" >> .gitignore
```

### Recherche sémantique

```bash
ollama pull nomic-embed-text
localcoder
you> /index build
```

`/index build` parcourt le projet, découpe chaque fichier en blocs
d'environ 40 lignes et calcule un embedding par bloc via Ollama. Les
fichiers inchangés depuis le dernier build ne sont pas ré-embeddés
(comparaison par hash SHA1). L'index vit dans `.localcoder/index.json`.

Une fois l'index construit, le tool `semantic_search` apparaît
automatiquement dans la liste envoyée au modèle.

### Recherche par symboles

```bash
brew install universal-ctags
```

**Important sur macOS** : le système fournit déjà un vieux `ctags` (BSD,
`/usr/bin/ctags`) qui ne comprend pas les options modernes. Si après
l'install Homebrew `ctags --version` n'affiche pas "Universal Ctags",
c'est que le PATH pointe encore vers l'ancien.

- **`find_definition`** — s'appuie sur Universal Ctags (parsing, pas du
  texte brut) pour trouver la vraie définition d'un symbole.
- **`find_references`** — grep à mots entiers (`\bsymbole\b`), sans faux
  positifs style `add` dans `address`.

Les deux tools n'apparaissent que si Universal Ctags est détecté au
démarrage. Aucun index à construire : `ctags` tourne à chaque appel.

### Config persistante

Au lieu de répéter les flags, crée `~/.localcoder.json` (global) ou
`./localcoder.json` (projet) :

```json
{
  "model": "devstral-small-2",
  "num_ctx": 8192,
  "temperature": 0.2,
  "auto_approve": false,
  "context": ["README.md"]
}
```

Ordre de priorité : flags CLI > `./localcoder.json` > `~/.localcoder.json`
> défauts. `context` fait exception — les chemins des trois sources
s'additionnent au lieu de s'écraser.

**Note** : les clés sont en `snake_case` (`num_ctx`, `auto_approve`,
`embed_model`) contrairement à la version Node (`numCtx`, `autoApprove`,
`embedModel`) — les deux versions ne partagent ni fichiers de config ni
fichiers de session, c'est une réécriture, pas un fork binaire compatible.

### Warm-up

Au démarrage, avant même d'afficher la ligne de saisie, localcoder envoie
un appel `/api/generate` sans prompt à Ollama (juste `keep_alive`) — la
façon documentée de précharger un modèle en mémoire sans rien générer.
Le but : que le premier vrai message ne paie pas le coût du chargement du
modèle. Si Ollama ne répond pas, un avertissement s'affiche et le warm-up
est simplement sauté — ça ne bloque jamais le démarrage. Désactivable avec
`--no-warm-up`.

### Stats et mode verbeux

- `/stats` donne un résumé cumulé de la session courante (modèle,
  `num_ctx`, température, nombre de tours, tokens de prompt/réponse
  cumulés, temps total de génération, et la part de la fenêtre de
  contexte utilisée au dernier tour).
- `/verbose` (ou `--verbose`/`-v` au lancement) affiche après chaque
  réponse un détail façon `ollama run --verbose` : durée totale, durée de
  chargement, nombre/durée/débit du prompt-eval, nombre/durée/débit de
  l'eval — construit directement à partir des métadonnées que renvoie
  Ollama sur le chunk final du stream.

### Changer de modèle ou de réglages en session

```
you> /model list
  - devstral-small-2
  - qwen3-coder:30b
you> /model use qwen3-coder:30b
you> /set temperature 0.4
you> /set num_ctx 16384
```

Ces changements ne s'appliquent qu'à la session en cours (pas de fichier
de config modifié) et prennent effet dès le tour suivant.

Certains modèles (souvent les modèles "chat-only", ex. `deepseek-coder:33b`)
refusent purement et simplement les requêtes qui contiennent des tools.
Dans ce cas, localcoder ne plante pas le tour : il prévient une seule fois
(`... ne supporte pas les tool-calls — on continue sans tools pour ce
modèle`) puis continue la conversation normalement, juste sans lecture/
écriture de fichiers, recherche, etc. pendant qu'il reste actif. Un
`/model use` vers un autre modèle réactive les tools automatiquement.

### Récap de conversation

```
you> /summary
you> /summary notes/recap.md
```

Demande au modèle un résumé de ce qui a été fait/décidé jusqu'ici — utile
avant de fermer un fil ou de passer à quelqu'un d'autre. Sans argument, le
texte s'affiche ; avec un chemin, il est écrit dans ce fichier (créé si
besoin) au lieu de s'afficher.

### Recherche directe (`/search`, `/find`)

Contrairement aux tools `search_code`/`find_definition`/`find_references`
que le *modèle* utilise pendant un tour, `/search` et `/find` sont pour
*toi* : une réponse immédiate, sans aller-retour vers Ollama.

```
you> /search TODO
you> /find handleSubmit
```

`/find` réutilise les mêmes tools que le modèle (Universal Ctags pour la
définition, grep à mots entiers pour les références) — même limitation :
n'apparaît utile que si Universal Ctags est installé (voir plus bas).

Ni `/search` ni `/find` n'ont besoin de `/context` : les deux fouillent
tout le projet directement sur disque, indépendamment de ce qui a été
ajouté au contexte (le contexte, lui, sert seulement à ce qui est envoyé
au modèle). `/find` veut un **identifiant exact** (`add`, `handleSubmit`)
et non une description (`/find une fonction qui additionne` ne
trouvera jamais rien, puisqu'aucun symbole ne s'appelle littéralement
comme ça) — pour une recherche en langage libre ou par motif, `/search`
est le bon outil.

`/search` reste malgré tout une recherche texte/regex **exacte** : si tu
tapes une description plutôt que les mots réellement présents dans le
code (`/search une fonction qui additionne deux nombres`), il n'y a
souvent rien à trouver littéralement. Dans ce cas, si un index a été
construit (`/index build`), `/search` retente automatiquement une
recherche par sens (la même que `semantic_search` utilise pour le
modèle) avant d'abandonner — sans index, il te le signale et te propose
de lancer `/index build`.

## Tests

```bash
pip install pytest
pytest
```

Suite complète : logique pure (config, contexte, rôles, skills, sessions,
menu, navigation de fichiers pour `/context add`, index sémantique) +
tools + recherche par symboles contre un vrai binaire Universal Ctags +
client Ollama (chat streamé, annulation via `cancel_event`, warm-up, liste
des modèles) + la mécanique de l'interface plein écran hors rendu (calcul
du menu, mise en cache, machine à états de la ligne de saisie,
`BufferSink` → transcript, routage `submit_line`) + bout-en-bout contre un
faux serveur Ollama (streaming NDJSON, tool calls, flux de confirmation,
sessions persistées entre deux lancements, warm-up au démarrage, `/model`,
`/set`, `/stats`, `/verbose`, `/summary`, `/search`, `/find`, mentions
`@chemin`, création de rôle/skill depuis l'interface).

Le rendu à l'écran de l'interface plein écran (mise en page, animation du
spinner, Page Haut/Page Bas, disposition centrée du menu) n'est pas
couvert par la suite automatisée — ça nécessite un vrai TTY, ce que
l'environnement de test n'a pas. Il a été vérifié manuellement avec un
vrai pseudo-terminal (`pty.fork()`, comme pour Ctrl+C) : démarrage,
warm-up avec spinner, aller-retour de conversation, ouverture du menu,
Tab qui complète le texte en entier, et Ctrl+C qui annule un tour sans
tuer l'application — mais ça reste une vérification manuelle, pas un test
qui tourne en CI. Toute la logique en dessous du rendu (`tests/test_menu.py`,
`tests/test_fullscreen.py`, `tests/test_browse.py`) est testée à fond.
