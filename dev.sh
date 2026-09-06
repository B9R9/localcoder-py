#!/usr/bin/env bash
# Launches localcoder in dev mode with a single command.
#
#   ./dev.sh               # auto venv + dependencies, then --watch (reloads on every change)
#   ./dev.sh --no-watch    # starts without automatic reload
#   ./dev.sh --warm-up     # also preloads the model on startup
#   ./dev.sh --session x   # any other argument is passed through to localcoder as-is
#
# --no-warm-up is enabled by default: in --watch mode, every reload restarts a
# process, and we don't want to pay the model preload cost on every edit.
set -eo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$root"

# 1) Pick a Python: >= 3.10 is required (pyproject.toml) — use the best one available.
py=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    py="$c"
    break
  fi
done
if [ -z "$py" ]; then
  echo "[dev] Error: Python >= 3.10 not found. e.g. 'brew install python@3.11'" >&2
  exit 1
fi
echo "[dev] Using Python: $py"

# 2) Virtual environment + prompt_toolkit — created only once.
venv="$root/.venv"
if [ ! -x "$venv/bin/python" ]; then
  echo "[dev] Creating .venv ..."
  "$py" -m venv "$venv"
fi
if ! "$venv/bin/python" -c 'import prompt_toolkit' >/dev/null 2>&1; then
  echo "[dev] Installing dependencies (prompt_toolkit) ..."
  "$venv/bin/pip" install --quiet -r "$root/requirements.txt"
fi

# 3) --watch / --no-warm-up by default, each can be disabled.
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

echo "[dev] Launching: localcoder $watch_flag $warm_flag $cmd"
eval "exec '$venv/bin/python' -m localcoder $watch_flag $warm_flag $cmd"