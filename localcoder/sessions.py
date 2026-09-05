"""Named, persistent sessions — a thread's history + active role + context
paths, saved per-project under .localcoder/sessions/<name>.json. Naming a
session is what opts you into persistence; an unnamed run stays ephemeral,
nothing written to disk. Mirrors src/sessions.mjs.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def _sessions_dir(cwd: Path) -> Path:
    return cwd / ".localcoder" / "sessions"


def _session_path(name: str, cwd: Path) -> Path:
    return _sessions_dir(cwd) / f"{name}.json"


def save_session(
    name: str,
    role: str | None,
    context_paths: list[str],
    conversation: list,
    cwd: Path,
    skills: list[str] | None = None,
) -> None:
    directory = _sessions_dir(cwd)
    directory.mkdir(parents=True, exist_ok=True)
    data = {
        "name": name,
        "role": role or None,
        "contextPaths": context_paths or [],
        "skills": skills or [],
        "conversation": conversation,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
    _session_path(name, cwd).write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_session(name: str, cwd: Path) -> dict | None:
    full = _session_path(name, cwd)
    if not full.exists():
        return None
    try:
        return json.loads(full.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        return {"error": f'Could not read session "{name}": {err}'}


def list_sessions(cwd: Path) -> list[str]:
    directory = _sessions_dir(cwd)
    if not directory.exists():
        return []
    return sorted(p.stem for p in directory.iterdir() if p.suffix == ".json")
