"""Skills: plain .md/.txt files like roles, but stackable — several can be
active at once (a "how to write a spreadsheet"-style playbook layered on top
of whatever role/persona is active), unlike role which is a single "mode"
that replaces itself. Mirrors roles.py's file layout and lookup order.
"""

from __future__ import annotations

from pathlib import Path

_EXTENSIONS = (".md", ".txt")


def _skill_dirs(cwd: Path) -> list[Path]:
    return [cwd / "skills", Path.home() / ".localcoder" / "skills"]


def load_skill(name: str, cwd: Path) -> dict:
    for directory in _skill_dirs(cwd):
        for ext in _EXTENSIONS:
            full = directory / f"{name}{ext}"
            if full.exists():
                try:
                    return {"name": name, "path": str(full), "content": full.read_text(encoding="utf-8").strip()}
                except OSError as err:
                    return {"name": name, "error": f'Could not read skill "{name}": {err}'}
    return {"name": name, "error": f'Skill "{name}" not found in skills/ or ~/.localcoder/skills/'}


def list_skills(cwd: Path) -> list[str]:
    names: set[str] = set()
    for directory in _skill_dirs(cwd):
        if not directory.exists():
            continue
        try:
            for entry in directory.iterdir():
                if entry.is_file() and entry.suffix in _EXTENSIONS:
                    names.add(entry.stem)
        except OSError:
            pass
    return sorted(names)


def format_skill(skill: dict) -> str:
    return f"[skill: {skill['name']}]\n{skill['content']}"


def create_skill(name: str, content: str, cwd: Path) -> dict:
    directory = cwd / "skills"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        full = directory / f"{name}.md"
        full.write_text(content.strip() + "\n", encoding="utf-8")
        return {"name": name, "path": str(full)}
    except OSError as err:
        return {"name": name, "error": f'Could not save skill "{name}": {err}'}
