"""Config loading — mirrors src/config.mjs exactly (same precedence, same
field names in snake_case).

Precedence (highest wins): CLI flags > ./localcoder.json > ~/.localcoder.json
> defaults. `context` is the one exception: it's additive across every
source (each one is telling you about relevant paths, not replacing the
others), deduplicated.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

DEFAULTS = {
    "host": os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
    "model": "devstral-small-2",
    "num_ctx": 8192,
    "temperature": 0.2,
    "auto_approve": False,
    "context": [],  # files/dirs/globs always loaded at startup
    "role": None,  # name of a roles/<name>.md file, active for the whole session
    "session": None,  # name of a persisted .localcoder/sessions/<name>.json thread
    "embed_model": "nomic-embed-text",  # used only by /index build + semantic_search
    "verbose": False,  # print an `ollama --verbose`-style breakdown after every turn
    "warm_up": True,  # preload the model at startup so the first real message isn't slow
    "max_subagents": 4,  # cap on parallel branches spawn_subagents fans out to
}


@dataclass
class Config:
    host: str = DEFAULTS["host"]
    model: str = DEFAULTS["model"]
    num_ctx: int = DEFAULTS["num_ctx"]
    temperature: float = DEFAULTS["temperature"]
    auto_approve: bool = DEFAULTS["auto_approve"]
    context: list = field(default_factory=list)
    role: str | None = None
    session: str | None = None
    embed_model: str = DEFAULTS["embed_model"]
    verbose: bool = DEFAULTS["verbose"]
    warm_up: bool = DEFAULTS["warm_up"]
    max_subagents: int = DEFAULTS["max_subagents"]


def _load_json_if_exists(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as err:
            print(f"[config] Failed to parse {path}: {err}", file=sys.stderr)
    return {}


def _parse_flags(argv: list[str]) -> tuple[dict, list[str]]:
    flags: dict = {}
    context_flags: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--model":
            i += 1
            flags["model"] = argv[i]
        elif arg == "--host":
            i += 1
            flags["host"] = argv[i]
        elif arg == "--num-ctx":
            i += 1
            flags["num_ctx"] = int(argv[i])
        elif arg == "--temperature":
            i += 1
            flags["temperature"] = float(argv[i])
        elif arg == "--yolo":
            flags["auto_approve"] = True
        elif arg == "--context":
            i += 1
            context_flags.append(argv[i])
        elif arg == "--role":
            i += 1
            flags["role"] = argv[i]
        elif arg == "--session":
            i += 1
            flags["session"] = argv[i]
        elif arg == "--embed-model":
            i += 1
            flags["embed_model"] = argv[i]
        elif arg in ("--verbose", "-v"):
            flags["verbose"] = True
        elif arg == "--no-warm-up":
            flags["warm_up"] = False
        elif arg == "--max-subagents":
            i += 1
            flags["max_subagents"] = int(argv[i])
        i += 1
    return flags, context_flags


def load_config(argv: list[str]) -> Config:
    global_config = _load_json_if_exists(Path.home() / ".localcoder.json")
    local_config = _load_json_if_exists(Path.cwd() / "localcoder.json")
    flags, context_flags = _parse_flags(argv)

    merged = {**DEFAULTS, **global_config, **local_config, **flags}

    combined_context = [
        *global_config.get("context", []),
        *local_config.get("context", []),
        *context_flags,
    ]
    # dict.fromkeys dedupes while preserving order (a plain set would not).
    merged["context"] = list(dict.fromkeys(combined_context))

    known_fields = {f for f in Config.__dataclass_fields__}
    kwargs = {k: v for k, v in merged.items() if k in known_fields}
    return Config(**kwargs)
