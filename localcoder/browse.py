"""Directory-by-directory browsing for /context add's "/" menu — lets the
user Tab/arrow through the actual folder structure instead of substring-
filtering one giant flattened file list. Starts at the project's `src/` (or
`source/`) directory rather than the repo root, since that's almost always
where the files worth adding as context actually live; falls back to the
project root if neither exists. Pure logic, no terminal/IO, so it's unit-
testable — repl.py wires this into the menu the same way roles/sessions are.

Context isn't limited to the current project: typing "../" climbs above it
(handy for pulling something from a sibling repo — several projects worked
on side by side is a completely normal setup) and an absolute ("/...") or
home ("~/...") path browses anywhere else on disk. Only the *default*
starting point is scoped to the project; where the user actually navigates
is not.
"""

from __future__ import annotations

from pathlib import Path

from localcoder.context import IGNORE_DIRS

_SOURCE_DIR_NAMES = ("src", "source")


def default_browse_root(cwd: Path) -> str:
    """Relative path (from cwd) to start browsing in — 'src' or 'source' if
    one exists at the project root, otherwise '' (the project root itself).
    """
    for name in _SOURCE_DIR_NAMES:
        if (cwd / name).is_dir():
            return name
    return ""


def _list_dir(full_dir: Path) -> list[str]:
    try:
        items = sorted(full_dir.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError:
        return []
    names = []
    for item in items:
        if item.name in IGNORE_DIRS or item.name.startswith("."):
            continue
        names.append(f"{item.name}/" if item.is_dir() else item.name)
    return names


def _resolve_dir(cwd: Path, rel_dir: str) -> Path:
    """rel_dir may be '' (the project root), a project-relative path, one
    that climbs above the project via '..', or an absolute/'~' path to
    browse anywhere on disk.
    """
    if not rel_dir:
        return cwd
    expanded = Path(rel_dir).expanduser()
    if expanded.is_absolute():
        return expanded
    return (cwd / rel_dir).resolve()


def _join(rel_dir: str, name: str) -> str:
    if not rel_dir:
        return name
    if rel_dir.endswith("/"):
        return f"{rel_dir}{name}"
    return f"{rel_dir}/{name}"


def browse_entries(cwd: Path, partial: str) -> list[str]:
    """Given whatever the user has typed so far after "/context add " (may be
    empty), returns paths (kept in whatever form the user typed them —
    project-relative, '..'-relative, absolute, or '~'-relative) for the
    matching entries one directory level deep — directories first, each
    still trailing "/" so tabbing again descends into it. Behaves like a
    shell path completer: everything up to the last "/" picks the directory
    to list, the remainder filters that directory's entries by prefix.
    """
    root = default_browse_root(cwd)

    if "/" in partial:
        typed_dir, prefix = partial.rsplit("/", 1)
        rel_dir = typed_dir or "/"  # a bare leading "/" browses the filesystem root
    else:
        prefix = partial
        rel_dir = root

    full_dir = _resolve_dir(cwd, rel_dir)
    if not full_dir.is_dir():
        return []

    names = _list_dir(full_dir)
    if prefix:
        names = [n for n in names if n.lower().startswith(prefix.lower())]

    result = [_join(rel_dir, n) for n in names]
    if rel_dir and not prefix:
        dir_entry = rel_dir if rel_dir.endswith("/") else f"{rel_dir}/"
        if dir_entry not in result:
            result.insert(0, dir_entry)
    return result
