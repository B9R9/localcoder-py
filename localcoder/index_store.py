"""Optional semantic (RAG) index — only built when the user runs
`/index build`, only searched when the user (well, the model, via the
semantic_search tool) asks for it. Brute-force cosine similarity, no vector
DB — fine at personal-project scale. Mirrors src/index-store.mjs.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from localcoder.embeddings import embed
from localcoder.local_state import get_state, set_state_value

CHUNK_LINES = 40
CHUNK_OVERLAP = 8
MAX_FILES = 2000  # safety cap, not a tuning knob
DEFAULT_INDEX_NAME = "default"

# Prose/data files (docs, but also i18n locale files and JSON/YAML content
# fixtures) are written in the same natural language as a semantic query, so
# they often out-score the actual code that computes or uses them ("returns
# doc, not code"). Nudge these down rather than excluding them outright —
# still findable, just not ahead of equally-relevant code.
DOC_EXTENSIONS = {".md", ".json", ".yaml", ".yml"}
DOC_PENALTY = 0.85

# Score boost when a chunk defines a symbol (def/class/import) whose name
# matches a word in the query — this is what makes "where is the menu coded"
# return menu.py's `class MenuItem` instead of prose mentioning menus.
DEFINITION_BOOST = 0.15
IMPORT_BOOST = 0.08

# def/class at line start (Python, JS/TS, Java, Go, Rust, C-ish...) and
# import lines. Captures the defined/imported name in group 2.
_DEF_RE = re.compile(
    r"^\s*(?:async\s+)?(?:def|class|function|fn|func|struct|interface|type|const|let|var)\s+([A-Za-z_][\w]*)",
    re.MULTILINE,
)
# from/import are statements, so they're anchored to line start; require(...)
# is an expression that shows up mid-line too (`const x = require('./foo')`,
# `module.exports = require('./bar')`), so it isn't anchored — just word-
# bounded so it doesn't match inside a longer identifier like `myrequire(`.
_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))|\brequire\(['\"]([\w./-]+)",
    re.MULTILINE,
)
_WORD_RE = re.compile(r"[a-zA-Z_][\w]*")


def _query_terms(query: str) -> set[str]:
    """Lowercased words of the query, plus snake_case/camelCase splits so
    'menu items' matches 'menu_items' / 'menuItems'."""
    words = {w.lower() for w in _WORD_RE.findall(query)}
    # also keep split parts of any snake/camel tokens already present
    for w in list(words):
        words.update(p for p in re.split(r"[_\s]+", w) if p)
    return {w for w in words if len(w) >= 3}


def _defined_symbols(text: str) -> set[str]:
    names = {m.group(1).lower() for m in _DEF_RE.finditer(text)}
    # split snake_case names into parts too: compute_menu_items -> {compute, menu, items}
    parts: set[str] = set()
    for n in names:
        parts.update(p for p in n.split("_") if p)
    return names | parts


def _imported_modules(text: str) -> set[str]:
    mods: set[str] = set()
    for m in _IMPORT_RE.finditer(text):
        raw = next(g for g in m.groups() if g)
        mods.update(p.lower() for p in re.split(r"[./]", raw) if p)
    return mods

IGNORE_DIRS = {"node_modules", ".git", "dist", "build", ".next", ".nuxt", "coverage", ".localcoder"}
INDEXABLE_EXT = {
    ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".vue",
    ".py", ".java", ".go", ".rb", ".php", ".rs", ".c", ".cpp", ".h", ".hpp",
    ".md", ".json", ".yaml", ".yml", ".css", ".scss", ".html",
}


# "default" keeps the original unnamed-index filename so existing indexes
# (and the test suite) keep working untouched; any other name gets its own
# file so several indexes (e.g. one per embed model) can coexist.
def _index_path(cwd: Path, name: str = DEFAULT_INDEX_NAME) -> Path:
    if name == DEFAULT_INDEX_NAME:
        return cwd / ".localcoder" / "index.json"
    return cwd / ".localcoder" / f"index.{name}.json"


def get_active_index_name(cwd: Path) -> str:
    return get_state(cwd).get("activeIndex") or DEFAULT_INDEX_NAME


def set_active_index_name(cwd: Path, name: str) -> None:
    set_state_value(cwd, "activeIndex", name)


def _hash_content(content: str) -> str:
    return hashlib.sha1(content.encode("utf-8")).hexdigest()


def _walk_files(directory: Path, cwd: Path, out: list[str]) -> None:
    if len(out) >= MAX_FILES:
        return
    try:
        entries = sorted(directory.iterdir(), key=lambda p: p.name)
    except OSError:
        return
    for entry in entries:
        if len(out) >= MAX_FILES:
            return
        if entry.name in IGNORE_DIRS:
            continue
        if entry.is_dir():
            _walk_files(entry, cwd, out)
        elif entry.suffix in INDEXABLE_EXT:
            out.append(str(entry.relative_to(cwd)))


def _chunk_text(content: str) -> list[dict]:
    lines = content.split("\n")
    if len(lines) <= CHUNK_LINES:
        return [{"startLine": 1, "endLine": len(lines), "text": content}]
    chunks = []
    step = CHUNK_LINES - CHUNK_OVERLAP
    start = 0
    while start < len(lines):
        end = min(start + CHUNK_LINES, len(lines))
        chunks.append({"startLine": start + 1, "endLine": end, "text": "\n".join(lines[start:end])})
        if end == len(lines):
            break
        start += step
    return chunks


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def load_index(cwd: Path, name: str = DEFAULT_INDEX_NAME) -> Optional[dict]:
    full = _index_path(cwd, name)
    if not full.exists():
        return None
    try:
        return json.loads(full.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save_index(cwd: Path, data: dict, name: str = DEFAULT_INDEX_NAME) -> None:
    full = _index_path(cwd, name)
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(json.dumps(data), encoding="utf-8")


# Rebuilds the index, reusing embeddings for files whose content hash hasn't
# changed since the last build. on_progress(path, i, total) is called per file.
# `name` lets several indexes coexist (e.g. one per embed model) — each keeps
# its own file and its own embed model, chosen at build time.
def build_index(
    cwd: Path,
    host: str,
    model: str,
    on_progress: Optional[Callable[[str, int, int], None]] = None,
    name: str = DEFAULT_INDEX_NAME,
) -> dict:
    existing = load_index(cwd, name) or {"model": model, "files": {}}
    paths: list[str] = []
    _walk_files(cwd, cwd, paths)

    files: dict = {}
    embedded = 0
    reused = 0

    for i, rel_path in enumerate(paths):
        if on_progress:
            on_progress(rel_path, i + 1, len(paths))
        try:
            content = (cwd / rel_path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # unreadable (binary mistakenly matched, permissions...) — skip

        content_hash = _hash_content(content)
        prior = existing["files"].get(rel_path)

        if prior and prior.get("hash") == content_hash and prior.get("model") == model:
            files[rel_path] = prior
            reused += 1
            continue

        chunks = _chunk_text(content)
        embeddings = embed(host, model, [c["text"] for c in chunks])
        files[rel_path] = {
            "hash": content_hash,
            "model": model,
            "chunks": [
                {"startLine": c["startLine"], "endLine": c["endLine"], "text": c["text"], "embedding": embeddings[idx]}
                for idx, c in enumerate(chunks)
            ],
        }
        embedded += 1

    data = {"model": model, "updatedAt": datetime.now(timezone.utc).isoformat(), "files": files}
    _save_index(cwd, data, name)
    return {"fileCount": len(paths), "embedded": embedded, "reused": reused}


def semantic_search(query: str, cwd: Path, host: str, model: str, top_k: int = 8, name: str = DEFAULT_INDEX_NAME) -> dict:
    idx = load_index(cwd, name)
    if not idx or not idx.get("files"):
        return {"error": f'No index named "{name}" built yet. Run /index build.'}

    query_embedding = embed(host, idx["model"], [query])[0]
    terms = _query_terms(query)

    scored = []
    for path, file in idx["files"].items():
        penalty = DOC_PENALTY if Path(path).suffix in DOC_EXTENSIONS else 1.0
        # filename match: "menu" in the query should lift menu.py itself
        path_parts = {p.lower() for p in re.split(r"[./_\\-]", path) if p}
        path_boost = DEFINITION_BOOST if terms & path_parts else 0.0
        for chunk in file["chunks"]:
            score = cosine_similarity(query_embedding, chunk["embedding"]) * penalty
            text = chunk["text"]
            if terms:
                if terms & _defined_symbols(text):
                    score += DEFINITION_BOOST
                if terms & _imported_modules(text):
                    score += IMPORT_BOOST
            scored.append(
                {
                    "path": path,
                    "startLine": chunk["startLine"],
                    "endLine": chunk["endLine"],
                    "text": text,
                    "score": score + path_boost,
                }
            )
    scored.sort(key=lambda r: r["score"], reverse=True)
    return {"results": scored[:top_k]}


def index_stats(cwd: Path, name: str = DEFAULT_INDEX_NAME) -> Optional[dict]:
    idx = load_index(cwd, name)
    if not idx:
        return None
    file_count = len(idx["files"])
    chunk_count = sum(len(f["chunks"]) for f in idx["files"].values())
    return {"model": idx["model"], "updatedAt": idx["updatedAt"], "fileCount": file_count, "chunkCount": chunk_count}


# Every index.json / index.<name>.json under .localcoder, alphabetical.
def list_indexes(cwd: Path) -> list[dict]:
    folder = cwd / ".localcoder"
    if not folder.is_dir():
        return []
    names = []
    for f in folder.glob("index*.json"):
        if f.name == "index.json":
            names.append(DEFAULT_INDEX_NAME)
        elif f.name.startswith("index.") and f.name.endswith(".json"):
            names.append(f.name[len("index."):-len(".json")])
    result = []
    for name in sorted(names):
        stats = index_stats(cwd, name)
        if stats:
            result.append({"name": name, **stats})
    return result


def delete_index(cwd: Path, name: str) -> bool:
    full = _index_path(cwd, name)
    if not full.exists():
        return False
    full.unlink()
    if get_active_index_name(cwd) == name and name != DEFAULT_INDEX_NAME:
        set_active_index_name(cwd, DEFAULT_INDEX_NAME)
    return True
