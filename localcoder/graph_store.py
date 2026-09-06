"""Optional file-relationship graph — built when the user runs
`/graph_map build`, walked when the model (via the graph_neighbors tool)
asks "what does this file import, and what imports it". Nodes are project
files, edges are import statements resolved to project-relative paths.
Regex-based, one hop per lookup — mirrors index_store.py's shape.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from localcoder.index_store import _IMPORT_RE, _walk_files
from localcoder.local_state import get_state, set_state_value

DEFAULT_GRAPH_NAME = "default"

_PY_EXTS = (".py",)
_JS_EXTS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue")


def _graph_path(cwd: Path, name: str = DEFAULT_GRAPH_NAME) -> Path:
    if name == DEFAULT_GRAPH_NAME:
        return cwd / ".localcoder" / "graph.json"
    return cwd / ".localcoder" / f"graph.{name}.json"


def get_active_graph_name(cwd: Path) -> str:
    return get_state(cwd).get("activeGraph") or DEFAULT_GRAPH_NAME


def set_active_graph_name(cwd: Path, name: str) -> None:
    set_state_value(cwd, "activeGraph", name)


def load_graph(cwd: Path, name: str = DEFAULT_GRAPH_NAME) -> Optional[dict]:
    full = _graph_path(cwd, name)
    if not full.exists():
        return None
    try:
        return json.loads(full.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save_graph(cwd: Path, data: dict, name: str = DEFAULT_GRAPH_NAME) -> None:
    full = _graph_path(cwd, name)
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(json.dumps(data), encoding="utf-8")


def _within_cwd(cwd: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(cwd.resolve())
        return True
    except ValueError:
        return False


def _resolve_python_import(raw: str, importer_rel_path: str, cwd: Path) -> Optional[str]:
    if raw.startswith("."):
        level = len(raw) - len(raw.lstrip("."))
        remainder = raw[level:]
        if not remainder:
            return None  # "from . import x" — the regex can't see "x", nothing to resolve
        base = (cwd / importer_rel_path).parent
        for _ in range(level - 1):
            base = base.parent
    else:
        remainder = raw
        base = cwd

    candidate = base / remainder.replace(".", "/")

    for suffix_path in (candidate.with_suffix(".py"), candidate / "__init__.py"):
        if suffix_path.exists() and suffix_path.is_file() and _within_cwd(cwd, suffix_path):
            return str(suffix_path.resolve().relative_to(cwd.resolve()))
    return None


def _resolve_js_import(raw: str, importer_rel_path: str, cwd: Path) -> Optional[str]:
    if not (raw.startswith("./") or raw.startswith("../")):
        return None  # bare specifier — external package, not resolvable

    base = (cwd / importer_rel_path).parent
    candidate = (base / raw).resolve()

    tries = [candidate]
    tries += [candidate.with_name(candidate.name + ext) for ext in _JS_EXTS]
    tries += [candidate / f"index{ext}" for ext in _JS_EXTS]

    for path in tries:
        if path.exists() and path.is_file() and _within_cwd(cwd, path):
            return str(path.relative_to(cwd.resolve()))
    return None


def _resolve_import(match: re.Match, importer_rel_path: str, cwd: Path) -> Optional[str]:
    from_target, import_target, require_target = match.group(1), match.group(2), match.group(3)
    if require_target is not None:
        return _resolve_js_import(require_target, importer_rel_path, cwd)
    raw = from_target or import_target
    if raw is None:
        return None
    return _resolve_python_import(raw, importer_rel_path, cwd)


# Full rebuild every time — no hash-reuse. Unlike build_index, there's no
# expensive embedding call to save by skipping unchanged files: regex-parsing
# a few thousand files is cheap, so keeping this simple wins over the
# reuse-on-unchanged-hash complexity index_store.py needs for embeddings.
def build_graph(
    cwd: Path,
    on_progress: Optional[Callable[[str, int, int], None]] = None,
    name: str = DEFAULT_GRAPH_NAME,
) -> dict:
    paths: list[str] = []
    _walk_files(cwd, cwd, paths)

    nodes: dict[str, dict] = {p: {"imports": set(), "importedBy": set()} for p in paths}

    for i, rel_path in enumerate(paths):
        if on_progress:
            on_progress(rel_path, i + 1, len(paths))
        try:
            content = (cwd / rel_path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        for match in _IMPORT_RE.finditer(content):
            target = _resolve_import(match, rel_path, cwd)
            if target is None or target == rel_path or target not in nodes:
                continue
            nodes[rel_path]["imports"].add(target)
            nodes[target]["importedBy"].add(rel_path)

    edge_count = sum(len(n["imports"]) for n in nodes.values())
    serializable = {
        p: {"imports": sorted(n["imports"]), "importedBy": sorted(n["importedBy"])} for p, n in nodes.items()
    }
    data = {"updatedAt": datetime.now(timezone.utc).isoformat(), "nodes": serializable}
    _save_graph(cwd, data, name)
    return {"fileCount": len(paths), "edgeCount": edge_count}


def graph_neighbors(file: str, cwd: Path, name: str = DEFAULT_GRAPH_NAME) -> dict:
    data = load_graph(cwd, name)
    if not data:
        return {"error": f'No graph map named "{name}" built yet. Run /graph_map build.'}
    rel = file[2:] if file.startswith("./") else file
    node = data["nodes"].get(rel)
    if node is None:
        return {"error": f'"{file}" not found in graph map "{name}". It may not exist, or the graph needs rebuilding.'}
    return {"imports": node["imports"], "importedBy": node["importedBy"]}


def graph_stats(cwd: Path, name: str = DEFAULT_GRAPH_NAME) -> Optional[dict]:
    data = load_graph(cwd, name)
    if not data:
        return None
    edge_count = sum(len(n["imports"]) for n in data["nodes"].values())
    return {"updatedAt": data["updatedAt"], "fileCount": len(data["nodes"]), "edgeCount": edge_count}


# Every graph.json / graph.<name>.json under .localcoder, alphabetical.
def list_graph_maps(cwd: Path) -> list[dict]:
    folder = cwd / ".localcoder"
    if not folder.is_dir():
        return []
    names = []
    for f in folder.glob("graph*.json"):
        if f.name == "graph.json":
            names.append(DEFAULT_GRAPH_NAME)
        elif f.name.startswith("graph.") and f.name.endswith(".json"):
            names.append(f.name[len("graph."):-len(".json")])
    result = []
    for n in sorted(names):
        stats = graph_stats(cwd, n)
        if stats:
            result.append({"name": n, **stats})
    return result


def delete_graph(cwd: Path, name: str) -> bool:
    full = _graph_path(cwd, name)
    if not full.exists():
        return False
    full.unlink()
    if get_active_graph_name(cwd) == name and name != DEFAULT_GRAPH_NAME:
        set_active_graph_name(cwd, DEFAULT_GRAPH_NAME)
    return True
