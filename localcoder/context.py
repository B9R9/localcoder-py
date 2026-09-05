"""User-controlled context: files/dirs/globs the user explicitly loads —
never an automatic scan or index. Mirrors src/context.mjs.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

MAX_FILE_CHARS = 6000
MAX_TREE_ENTRIES = 300
IGNORE_DIRS = {"node_modules", ".git", "dist", "build", ".next", ".nuxt", "coverage"}


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"\n...[truncated, {len(text) - max_len} more chars]"


def _walk(directory: Path, depth: int, entries: list[str]) -> None:
    if len(entries) >= MAX_TREE_ENTRIES:
        return
    try:
        items = sorted(directory.iterdir(), key=lambda p: p.name)
    except OSError:
        return
    for item in items:
        if len(entries) >= MAX_TREE_ENTRIES:
            return
        if item.name in IGNORE_DIRS:
            continue
        is_dir = item.is_dir()
        entries.append(f"{item}/" if is_dir else str(item))
        if is_dir and depth > 0:
            _walk(item, depth - 1, entries)


# Minimal glob support: a single "*" wildcard in the LAST path segment only
# (e.g. "docs/adr/*.md"). No dependency, no recursive **, no mid-path
# wildcards — enough for "load every file in this folder", not a full glob
# engine.
def _expand_glob(pattern: str, cwd: Path) -> list[str]:
    if "/" in pattern:
        dir_part, file_part = pattern.rsplit("/", 1)
    else:
        dir_part, file_part = ".", pattern
    full_dir = (cwd / dir_part).resolve()
    if not full_dir.is_dir():
        return []

    regex_body = re.escape(file_part).replace(r"\*", ".*")
    regex = re.compile(f"^{regex_body}$")

    try:
        names = sorted(p.name for p in full_dir.iterdir() if p.is_file() and regex.match(p.name))
    except OSError:
        return []
    return [name if dir_part == "." else f"{dir_part}/{name}" for name in names]


# Loads one concrete (non-glob) --context path: a file is read in full
# (capped), a directory becomes a shallow file tree — enough to orient the
# model, not a dump of every file's content.
def load_context_entry(path: str, cwd: Path) -> dict:
    full = (cwd / path).resolve()
    if not full.exists():
        return {"path": path, "kind": "error", "content": f"Context path not found: {path}"}
    if full.is_dir():
        entries: list[str] = []
        _walk(full, 3, entries)
        rel = [e.replace(f"{full}/", "") for e in entries]
        return {"path": path, "kind": "dir", "content": "\n".join(rel)}
    try:
        content = full.read_text(encoding="utf-8")
        return {"path": path, "kind": "file", "content": _truncate(content, MAX_FILE_CHARS)}
    except OSError as err:
        return {"path": path, "kind": "error", "content": f"Could not read {path}: {err}"}


# Loads one --context input, which may be a glob (expands to full content of
# every matching file — this is how ADRs etc. get loaded as guardrails, not
# just listed) or a plain file/dir path. Always returns a list.
def load_context_path(path_or_glob: str, cwd: Path) -> list[dict]:
    if "*" in path_or_glob:
        matches = _expand_glob(path_or_glob, cwd)
        if not matches:
            return [{"path": path_or_glob, "kind": "error", "content": f"No files matched: {path_or_glob}"}]
        return [load_context_entry(m, cwd) for m in matches]
    return [load_context_entry(path_or_glob, cwd)]


def load_context(paths: list[str], cwd: Path) -> list[dict]:
    result: list[dict] = []
    for p in paths:
        result.extend(load_context_path(p, cwd))
    return result


def format_context_entry(entry: dict) -> str:
    if entry["kind"] == "error":
        return f"[context: {entry['path']}] {entry['content']}"
    if entry["kind"] == "dir":
        return f"[context: {entry['path']} — file tree]\n{entry['content']}"
    return f"[context: {entry['path']}]\n{entry['content']}"


# Flattened project file/dir listing — powers the interactive /context add
# picker in the terminal UI. Not sent to the model, just for the picker, so
# it can afford a bit more depth than the tree shown as context.
def list_project_files(cwd: Path) -> list[str]:
    entries: list[str] = []
    _walk(cwd, 6, entries)
    return [e.replace(f"{cwd}/", "") for e in entries]


# Named context sets: a saved list of paths/globs the user can switch to
# with /context load <name>, independent of /session (which bundles context
# with role/skills/history as one blob). Stored one file per name so they
# can be listed and swapped without touching the rest of session state.
def _contexts_dir(cwd: Path) -> Path:
    return cwd / ".localcoder" / "contexts"


def save_context_set(cwd: Path, name: str, paths: list[str]) -> None:
    folder = _contexts_dir(cwd)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.json").write_text(json.dumps({"paths": paths}), encoding="utf-8")


def load_context_set_paths(cwd: Path, name: str) -> list[str] | None:
    full = _contexts_dir(cwd) / f"{name}.json"
    if not full.exists():
        return None
    try:
        data = json.loads(full.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data.get("paths") or []


def list_context_sets(cwd: Path) -> list[str]:
    folder = _contexts_dir(cwd)
    if not folder.is_dir():
        return []
    return sorted(p.stem for p in folder.glob("*.json"))
