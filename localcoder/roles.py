"""Roles: plain .md/.txt files that set the active "mode" (code review, TDD,
a stack-specific persona...). At most one active at a time — switching
replaces it, it doesn't stack. Mirrors src/roles.mjs.
"""

from __future__ import annotations

from pathlib import Path

_EXTENSIONS = (".md", ".txt")


def _role_dirs(cwd: Path) -> list[Path]:
    # Project-local takes priority over global, so a project can override a
    # role name you also have globally.
    return [cwd / "roles", Path.home() / ".localcoder" / "roles"]


def load_role(name: str, cwd: Path) -> dict:
    for directory in _role_dirs(cwd):
        for ext in _EXTENSIONS:
            full = directory / f"{name}{ext}"
            if full.exists():
                try:
                    return {"name": name, "path": str(full), "content": full.read_text(encoding="utf-8").strip()}
                except OSError as err:
                    return {"name": name, "error": f'Could not read role "{name}": {err}'}
    return {"name": name, "error": f'Role "{name}" not found in roles/ or ~/.localcoder/roles/'}


def list_roles(cwd: Path) -> list[str]:
    names: set[str] = set()
    for directory in _role_dirs(cwd):
        if not directory.exists():
            continue
        try:
            for entry in directory.iterdir():
                if entry.is_file() and entry.suffix in _EXTENSIONS:
                    names.add(entry.stem)
        except OSError:
            pass  # ignore unreadable dirs
    return sorted(names)


def format_role(role: dict) -> str:
    return f"[role: {role['name']}]\n{role['content']}"


def create_role(name: str, content: str, cwd: Path) -> dict:
    """Writes roles/<name>.md in the project (creating the directory if
    needed) so a role can be authored from inside the CLI instead of by hand
    in an editor. Overwrites silently if the name already exists — the same
    "last write wins" behavior as /session save.
    """
    directory = cwd / "roles"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        full = directory / f"{name}.md"
        full.write_text(content.strip() + "\n", encoding="utf-8")
        return {"name": name, "path": str(full)}
    except OSError as err:
        return {"name": name, "error": f'Could not save role "{name}": {err}'}
