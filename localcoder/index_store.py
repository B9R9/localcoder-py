"""Optional semantic (RAG) index — only built when the user runs
`/index build`, only searched when the user (well, the model, via the
semantic_search tool) asks for it. Brute-force cosine similarity, no vector
DB — fine at personal-project scale. Mirrors src/index-store.mjs.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from localcoder.embeddings import embed

CHUNK_LINES = 40
CHUNK_OVERLAP = 8
MAX_FILES = 2000  # safety cap, not a tuning knob

IGNORE_DIRS = {"node_modules", ".git", "dist", "build", ".next", ".nuxt", "coverage", ".localcoder"}
INDEXABLE_EXT = {
    ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".vue",
    ".py", ".java", ".go", ".rb", ".php", ".rs", ".c", ".cpp", ".h", ".hpp",
    ".md", ".json", ".yaml", ".yml", ".css", ".scss", ".html",
}


def _index_path(cwd: Path) -> Path:
    return cwd / ".localcoder" / "index.json"


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


def load_index(cwd: Path) -> Optional[dict]:
    full = _index_path(cwd)
    if not full.exists():
        return None
    try:
        return json.loads(full.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save_index(cwd: Path, data: dict) -> None:
    full = _index_path(cwd)
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(json.dumps(data), encoding="utf-8")


# Rebuilds the index, reusing embeddings for files whose content hash hasn't
# changed since the last build. on_progress(path, i, total) is called per file.
def build_index(cwd: Path, host: str, model: str, on_progress: Optional[Callable[[str, int, int], None]] = None) -> dict:
    existing = load_index(cwd) or {"model": model, "files": {}}
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
    _save_index(cwd, data)
    return {"fileCount": len(paths), "embedded": embedded, "reused": reused}


def semantic_search(query: str, cwd: Path, host: str, model: str, top_k: int = 8) -> dict:
    idx = load_index(cwd)
    if not idx or not idx.get("files"):
        return {"error": "No index built yet. Run /index build first."}

    query_embedding = embed(host, idx["model"], [query])[0]

    scored = []
    for path, file in idx["files"].items():
        for chunk in file["chunks"]:
            scored.append(
                {
                    "path": path,
                    "startLine": chunk["startLine"],
                    "endLine": chunk["endLine"],
                    "text": chunk["text"],
                    "score": cosine_similarity(query_embedding, chunk["embedding"]),
                }
            )
    scored.sort(key=lambda r: r["score"], reverse=True)
    return {"results": scored[:top_k]}


def index_stats(cwd: Path) -> Optional[dict]:
    idx = load_index(cwd)
    if not idx:
        return None
    file_count = len(idx["files"])
    chunk_count = sum(len(f["chunks"]) for f in idx["files"].values())
    return {"model": idx["model"], "updatedAt": idx["updatedAt"], "fileCount": file_count, "chunkCount": chunk_count}
