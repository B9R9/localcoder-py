"""Pure logic for the interactive "/" menu — no terminal/IO here on purpose,
so it's unit-testable without a real TTY. fullscreen.py wires this into the
centered popup menu; repl.py's non-interactive fallback doesn't use it at
all (piped stdin has no menu, just plain typed commands).

Descriptions are deliberately a bit more explicit than a bare command name —
the menu is the main place someone discovers what a command does, so each
one spells out the effect, not just repeats the command.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

# arg: None = nothing more to type, selecting submits immediately.
#      "free" = needs a name the picker can't know in advance (a new session
#               name) — selecting fills the prefix and keeps editing.
#      "role" | "session" | "file" | "model" | "skill" = a dynamic list
#               exists — see DYNAMIC below.
TOP_COMMANDS = [
    {"cmd": "/help", "desc": "Show every command below, with descriptions", "arg": None},
    {"cmd": "/index build", "desc": "Embed the project so semantic_search can find code by meaning, not just text (optionally: a name, then an embed model)", "arg": "free"},
    {"cmd": "/index use", "desc": "Switch which built index is active, if you've built more than one", "arg": "index"},
    {"cmd": "/index list", "desc": "List every index built for this project, and which one is active", "arg": None},
    {"cmd": "/index status", "desc": "Show how many files/chunks are indexed and when it was last built", "arg": None},
    {"cmd": "/session save", "desc": "Name (if needed) and save the current thread so it can be resumed later", "arg": "free"},
    {"cmd": "/session load", "desc": "Resume a saved thread — its history, role, context and skills", "arg": "session"},
    {"cmd": "/session new", "desc": "Open a fresh named thread in a new terminal window, alongside this one", "arg": "free"},
    {"cmd": "/session list", "desc": "List every thread saved for this project", "arg": None},
    {"cmd": "/role use", "desc": "Switch the active role (persona) — replaces any role already in use", "arg": "role"},
    {"cmd": "/role list", "desc": "List roles available in roles/ and ~/.localcoder/roles/", "arg": None},
    {"cmd": "/role create", "desc": "Write a new role .md file from inside the CLI (type it, end with a line of just \".\")", "arg": "free"},
    {"cmd": "/role clear", "desc": "Deactivate the current role without picking another one", "arg": None},
    {"cmd": "/skill use", "desc": "Activate a skill — unlike role, several skills can be active at once", "arg": "skill"},
    {"cmd": "/skill list", "desc": "List skills available in skills/ and ~/.localcoder/skills/, marking active ones", "arg": None},
    {"cmd": "/skill create", "desc": "Write a new skill .md file from inside the CLI, same flow as /role create", "arg": "free"},
    {"cmd": "/skill clear", "desc": "Deactivate every currently active skill", "arg": None},
    {"cmd": "/context add", "desc": "Add a file, folder or glob to context sent with every message from now on", "arg": "file"},
    {"cmd": "/context list", "desc": "Show what's currently loaded as context", "arg": None},
    {"cmd": "/context clear", "desc": "Drop everything loaded as context (independent of /reset)", "arg": None},
    {"cmd": "/context save", "desc": "Save the current context (paths/globs) as a named set you can switch back to later", "arg": "free"},
    {"cmd": "/context load", "desc": "Switch to a previously saved named context set, replacing what's loaded now", "arg": "context_set"},
    {"cmd": "/context sets", "desc": "List every named context set saved for this project", "arg": None},
    {"cmd": "/model use", "desc": "Switch model for this session — lists what's already pulled in Ollama", "arg": "model"},
    {"cmd": "/model list", "desc": "List models already pulled in Ollama (same data as `ollama list`)", "arg": None},
    {"cmd": "/set temperature", "desc": "Change sampling temperature for the rest of this session", "arg": "free"},
    {"cmd": "/set num_ctx", "desc": "Change the context-window size (tokens) for the rest of this session", "arg": "free"},
    {"cmd": "/set embed_model", "desc": "Change which embed model the next /index build uses", "arg": "model"},
    {"cmd": "/bg run", "desc": "Run a shell command in the background — it keeps going while you keep working; check on it with /bg list and /bg output", "arg": "free"},
    {"cmd": "/bg list", "desc": "List background tasks (started here or by the model), with running/exit status", "arg": None},
    {"cmd": "/bg output", "desc": "Show the stdout/stderr captured so far for a background task", "arg": "free"},
    {"cmd": "/bg stop", "desc": "Terminate a running background task", "arg": "free"},
    {"cmd": "/stats", "desc": "Cumulative session stats: turns, tokens, generation time, context usage", "arg": None},
    {"cmd": "/verbose", "desc": "Toggle a per-turn timing breakdown, like `ollama run --verbose`", "arg": None},
    {"cmd": "/debug", "desc": "Toggle full tracebacks on errors instead of a short message", "arg": None},
    {"cmd": "/socratic", "desc": "Toggle Socratic mode — guided questions instead of direct answers, to keep learning", "arg": None},
    {"cmd": "/summary", "desc": "Ask the model to recap the conversation — prints it, or saves it to a file", "arg": None},
    {"cmd": "/search", "desc": "Search the whole project for exact text/regex — works with no context added, no model round-trip", "arg": "free"},
    {"cmd": "/find", "desc": "Find an exact symbol's definition/references across the whole project — no context needed", "arg": "free"},
    {"cmd": "/reset", "desc": "Clear conversation history — role, skills and context stay loaded", "arg": None},
    {"cmd": "/restart", "desc": "Relaunch localcoder cleanly — same folder and options, fresh startup (session autosaved first)", "arg": None},
    {"cmd": "/exit", "desc": "Quit localcoder", "arg": None},
]

# Once the buffer matches one of these prefixes exactly, the menu switches
# from "pick a command" to "pick an argument" using a live list (roles on
# disk, saved sessions, project files, models Ollama already has pulled,
# skills on disk) instead of a static command name.
_DYNAMIC = [
    ("/role use ", "roles"),
    ("/session load ", "sessions"),
    ("/context add ", "files"),
    ("/context load ", "context_set"),
    ("/index use ", "index"),
    ("/model use ", "models"),
    ("/set embed_model ", "models"),
    ("/skill use ", "skills"),
]

# "files" is special: it's a directory browser (see browse.py), so its
# getter needs the partial text to know which folder to list rather than
# returning one flat list to substring-filter like the others.
_PARTIAL_AWARE_KEYS = {"files"}


@dataclass
class MenuItem:
    display: str
    value: str
    submit: bool


# lists: {"roles": () -> list[str], "sessions": () -> list[str],
#         "models": () -> list[str], "skills": () -> list[str],
#         "files": (partial: str) -> list[str]}
# Each getter is only called when actually needed.
def compute_menu_items(buffer: str, lists: Optional[dict[str, Callable]] = None) -> list[MenuItem]:
    lists = lists or {}

    for prefix, key in _DYNAMIC:
        if not buffer.startswith(prefix):
            continue
        partial = buffer[len(prefix):]
        getter = lists.get(key)
        if not callable(getter):
            return []

        if key in _PARTIAL_AWARE_KEYS:
            names = getter(partial) or []
            # The getter already applied its own directory-aware filtering.
            return [MenuItem(display=name, value=prefix + name, submit=True) for name in names]

        names = getter() or []
        partial_lower = partial.lower()
        filtered = [n for n in names if partial_lower in n.lower()] if partial else names
        # No match (e.g. a hand-typed glob like "docs/*.md") — fall back to
        # free typing rather than showing an empty/misleading dropdown.
        return [MenuItem(display=name, value=prefix + name, submit=True) for name in filtered]

    if buffer.startswith("/"):
        return [
            MenuItem(
                display=f"{c['cmd']}  — {c['desc']}",
                value=f"{c['cmd']} " if c["arg"] else c["cmd"],
                submit=c["arg"] is None,
            )
            for c in TOP_COMMANDS
            if c["cmd"].startswith(buffer)
        ]

    return []
