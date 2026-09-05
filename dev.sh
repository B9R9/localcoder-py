#!/usr/bin/env bash
# Lance localcoder en mode développement en une seule commande.
#
#   ./dev.sh               # venv + dépendances auto, puis --watch (recharge à chaque modif)
#   ./dev.sh --no-watch    # démarre sans rechargement automatique
#   ./dev.sh --warm-up     # fait aussi le preload du modèle au démarrage
#   ./dev.sh --session x   # tout autre argument est transmis tel quel à localcoder
#
# --no-warm-up est activé par défaut : en mode --watch, chaque rechargement
# relance un processus, on ne veut pas repayer le preload du modèle à chaque édit.
set -eo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$root"

# 1) Choix du Python : >= 3.10 est requis (pyproject.toml) — on prend le meilleur disponible.
py=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    py="$c"
    break
  fi
done
if [ -z "$py" ]; then
  echo "[dev] Erreur : Python >= 3.10 introuvable. 'brew install python@3.11' par ex." >&2
  exit 1
fi
echo "[dev] Python choisi : $py"

# 2) Environnement virtuel + prompt_toolkit — créés une seule fois.
venv="$root/.venv"
if [ ! -x "$venv/bin/python" ]; then
  echo "[dev] Création de .venv ..."
  "$py" -m venv "$venv"
fi
if ! "$venv/bin/python" -c 'import prompt_toolkit' >/dev/null 2>&1; then
  echo "[dev] Installation des dépendances (prompt_toolkit) ..."
  "$venv/bin/pip" install --quiet -r "$root/requirements.txt"
fi

# 3) --watch / --no-warm-up par défaut, chacun désactivable.
watch_flag="--watch"
warm_flag="--no-warm-up"
cmd=""
for a in "$@"; do
  case "$a" in
    --no-watch) watch_flag="" ;;
    --warm-up) warm_flag="" ;;
    *) cmd="$cmd $(printf '%q' "$a")" ;;
  esac
done

echo "[dev] Lancement : localcoder $watch_flag $warm_flag $cmd"
eval "exec '$venv/bin/python' -m localcoder $watch_flag $warm_flag $cmd"