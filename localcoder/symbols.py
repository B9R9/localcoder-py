"""Symbol-aware search: find_definition (Universal Ctags, language-aware)
and find_references (word-boundary grep/ripgrep). Mirrors src/symbols.mjs,
including the node_modules-exclusion fix that search_code also needed.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

MAX_RESULTS = 50
IGNORE_DIRS = ["node_modules", ".git", "dist", "build", ".next", ".nuxt", "coverage"]

# macOS ships an ancient BSD ctags at /usr/bin/ctags that doesn't understand
# --output-format=json or most modern flags. We need Universal Ctags
# specifically (brew install universal-ctags) — check for it once and cache
# the result, since the check itself spawns a process.
_ctags_available: bool | None = None


def has_ctags() -> bool:
    global _ctags_available
    if _ctags_available is not None:
        return _ctags_available
    if not shutil.which("ctags"):
        _ctags_available = False
        return False
    try:
        out = subprocess.run(["ctags", "--version"], capture_output=True, text=True, timeout=10)
        _ctags_available = "Universal Ctags" in out.stdout
    except (OSError, subprocess.TimeoutExpired):
        _ctags_available = False
    return _ctags_available


_rg_available: bool | None = None


def has_ripgrep() -> bool:
    global _rg_available
    if _rg_available is not None:
        return _rg_available
    _rg_available = shutil.which("rg") is not None
    return _rg_available


def find_definition(symbol: str, cwd: Path) -> dict:
    if not has_ctags():
        return {"error": "Universal Ctags not found. Install it with: brew install universal-ctags"}

    exclude_args = [f"--exclude={d}" for d in IGNORE_DIRS]
    try:
        result = subprocess.run(
            ["ctags", "--output-format=json", "-R", "--fields=+n", *exclude_args, "."],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as err:
        return {"error": f"ctags failed: {err}"}

    matches = []
    for line in result.stdout.split("\n"):
        if not line.strip():
            continue
        try:
            tag = json.loads(line)
        except json.JSONDecodeError:
            continue  # stray non-JSON line — skip rather than fail the whole search
        if tag.get("_type") != "tag" or tag.get("name") != symbol:
            continue
        pattern = (tag.get("pattern") or "").removeprefix("/^").removesuffix("$/").strip()
        matches.append({"path": tag.get("path"), "line": tag.get("line"), "kind": tag.get("kind"), "preview": pattern})
        if len(matches) >= MAX_RESULTS:
            break

    if not matches:
        return {"matches": [], "note": f'No definition found for "{symbol}".'}
    return {"matches": matches}


def find_references(symbol: str, cwd: Path) -> dict:
    pattern = rf"\b{re.escape(symbol)}\b"
    use_rg = has_ripgrep()

    try:
        if use_rg:
            rg_excludes = []
            for d in IGNORE_DIRS:
                rg_excludes += ["-g", f"!{d}/**"]
            result = subprocess.run(
                ["rg", "-n", "--no-heading", "-S", "--max-count", "200", *rg_excludes, pattern, "."],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=30,
            )
            # ripgrep exits 1 when there are simply no matches — not a real error.
            if result.returncode not in (0, 1):
                return {"error": result.stderr.strip() or f"rg exited {result.returncode}"}
            stdout = result.stdout
        else:
            exclude_args = []
            for d in IGNORE_DIRS:
                exclude_args += ["--exclude-dir", d]
            result = subprocess.run(
                ["grep", "-rn", "-I", "-w", *exclude_args, "--", symbol, "."],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode not in (0, 1):
                return {"error": result.stderr.strip() or f"grep exited {result.returncode}"}
            stdout = result.stdout

        trimmed = stdout.strip()
        return {"matches": trimmed or "(no references found)"}
    except (OSError, subprocess.TimeoutExpired) as err:
        return {"error": str(err)}
