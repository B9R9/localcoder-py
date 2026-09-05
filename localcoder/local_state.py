"""Tiny persisted key-value state for this project — currently just which
named index and which named context set are active. Separate from
index.json/session files since it's a couple of pointers, not content.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _state_path(cwd: Path) -> Path:
    return cwd / ".localcoder" / "state.json"


def get_state(cwd: Path) -> dict:
    full = _state_path(cwd)
    if not full.exists():
        return {}
    try:
        return json.loads(full.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def set_state_value(cwd: Path, key: str, value: Any) -> None:
    full = _state_path(cwd)
    data = get_state(cwd)
    data[key] = value
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(json.dumps(data), encoding="utf-8")
