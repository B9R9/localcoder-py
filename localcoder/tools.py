"""Tool schemas + dispatcher. Kept deliberately terse — every word here is
tokens sent on every single turn. Mirrors src/tools.mjs, including the
conditional-advertisement pattern: semantic_search / find_definition /
find_references only appear in the list sent to the model when their
prerequisite (an index, or Universal Ctags) actually exists.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from localcoder.index_store import index_stats, semantic_search
from localcoder.symbols import find_definition, find_references, has_ctags

MAX_OUTPUT_CHARS = 4000  # keep tool results small — this is the whole point

IGNORE_DIRS = {"node_modules", ".git", "dist", "build", ".next", ".nuxt", "coverage"}

_rg_available: bool | None = None


def _has_ripgrep() -> bool:
    global _rg_available
    if _rg_available is not None:
        return _rg_available
    _rg_available = shutil.which("rg") is not None
    return _rg_available


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n...[truncated, {len(text) - MAX_OUTPUT_CHARS} more chars]"


def _resolve_within_cwd(cwd: Path, path: str) -> Path | None:
    """Resolve a model-supplied path and confirm it lands inside cwd. Every
    tool here is driven by the model's own tool calls (unlike /context or
    /browse, which take paths the human typed), so without this a prompt
    injected via file/web content could point read_file at ~/.ssh/id_rsa or
    write_file outside the project — silently for reads, since those need no
    confirmation. Returns None if the path escapes cwd.
    """
    full = (cwd / path).resolve()
    try:
        full.relative_to(cwd.resolve())
    except ValueError:
        return None
    return full


def _search_code_without_ripgrep(cwd: Path, search_path: str, pattern: str, max_matches: int = 200) -> dict:
    """Fallback for search_code when ripgrep isn't installed — a pure-Python
    walk-and-regex-search instead of shelling out to the system `grep`.
    Deliberately NOT `grep -rn --exclude-dir=...`: that combination silently
    breaks on macOS, whose bundled grep doesn't accept the same flags GNU
    grep (Linux) does, so "search doesn't work" only ever showed up for
    people not on Linux. Doing it in Python instead means one code path
    behaves identically everywhere, with no dependency on what `grep` binary
    happens to be on $PATH.
    """
    try:
        # Case-insensitive unless the pattern itself has an uppercase letter
        # — same "smart case" ripgrep's -S flag gives the other code path,
        # so results don't change depending on which one happens to run.
        flags = 0 if re.search(r"[A-Z]", pattern) else re.IGNORECASE
        regex = re.compile(pattern, flags)
    except re.error as err:
        return {"error": f"Invalid pattern: {err}"}

    root = (cwd / search_path).resolve()
    if root.is_file():
        targets = [root]
    else:
        targets = []
        stack = [root]
        while stack:
            current = stack.pop()
            try:
                entries = sorted(current.iterdir())
            except OSError:
                continue
            for entry in entries:
                if entry.name in IGNORE_DIRS or entry.name.startswith("."):
                    continue
                if entry.is_dir():
                    stack.append(entry)
                else:
                    targets.append(entry)

    matches: list[str] = []
    for path in targets:
        if len(matches) >= max_matches:
            break
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # unreadable or binary — grep -I would skip these too
        try:
            rel = path.relative_to(cwd)
        except ValueError:
            rel = path
        for lineno, line in enumerate(text.splitlines(), start=1):
            if len(matches) >= max_matches:
                break
            if regex.search(line):
                matches.append(f"{rel}:{lineno}:{line}")

    return {"matches": _truncate("\n".join(matches)) or "(no matches)"}


BASE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file. Path is relative to the project root.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List files and folders at a path (one level deep, not recursive). Path defaults to the project root.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": "Search the project for a text/regex pattern. Returns matching file:line and the line content. Use this to locate code before reading or editing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "description": "Optional subdirectory to restrict the search to."},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace one exact occurrence of old_string with new_string in a file. old_string must match exactly once — include enough surrounding context to make it unique. Prefer this over write_file for changes to existing files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create a new file, or fully overwrite an existing one, with the given content. Path is relative to the project root. For editing an existing file, prefer edit_file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "Run a shell command in the project root and return stdout/stderr/exit code. Blocks until it finishes (or times out after 120s) — use run_shell_background instead for anything long-running (dev servers, watchers, long builds).",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell_background",
            "description": "Start a shell command in the project root without waiting for it to finish — returns immediately with a task id. Use for dev servers, watchers, or anything long-running. Check on it with list_background_tasks / get_background_output, and stop it with stop_background_task.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_background_tasks",
            "description": "List background tasks started with run_shell_background, with their running/exit status.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_background_output",
            "description": "Get the stdout/stderr captured so far for a background task, plus whether it's still running.",
            "parameters": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stop_background_task",
            "description": "Terminate a running background task.",
            "parameters": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "spawn_subagent",
            "description": (
                "Delegate a self-contained, read-only investigation task to a sub-agent with its own "
                "fresh conversation (same Ollama model, no access to yours). Use it for broad exploration "
                "(e.g. tracing how something works across many files) so its intermediate search/read tool "
                "calls fill up its context instead of yours — you only get its final answer back. The "
                "sub-agent cannot write files or run shell commands."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "A clear, self-contained description of what to investigate and what the answer should cover — the sub-agent has no memory of this conversation.",
                    }
                },
                "required": ["task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "spawn_subagents",
            "description": (
                "Like spawn_subagent, but splits several INDEPENDENT investigation tasks across parallel "
                "sub-agents at once instead of one at a time — use this when a task naturally breaks into "
                "parts that don't depend on each other's findings (e.g. investigate module A and module B "
                "separately), since running them concurrently is faster than one after another. The number "
                "of branches actually run is capped (configurable, /set max_subagents) — if you pass more "
                "tasks than the cap, only the first ones run. You get back "
                "every branch's own answer (or error) to synthesize into one final answer yourself. Each "
                "sub-agent is read-only and has no access to the others' findings while running."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Two or more self-contained, independent task descriptions — one per parallel sub-agent.",
                    }
                },
                "required": ["tasks"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "spawn_coding_subagents",
            "description": (
                "Delegate one or more code-writing tasks to sub-agents that each work on their own isolated "
                "git branch and working copy (via `git worktree`), branched off a shared work branch that "
                "is itself branched off your current branch. Tasks run in parallel (capped, /set "
                "max_subagents) and each sub-agent may write files and run shell commands freely without "
                "further confirmation — safe because each is confined to its own disposable branch that "
                "nothing else touches. When every task finishes, its branch is committed and merged into "
                "the shared work branch (a conflicting merge is reported, not silently dropped) — but that "
                "work branch is NEVER merged into your current branch automatically. To bring the result "
                "in, review it and use run_shell yourself (e.g. `git merge <work_branch>`), which still "
                "requires the user's confirmation like any other write. Requires the project to already be "
                "a git repository, and only sees COMMITTED changes — commit or stash first if you have "
                "uncommitted edits you want the sub-agents to see."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "One or more self-contained code-change task descriptions — each becomes its own branch/worktree.",
                    }
                },
                "required": ["tasks"],
            },
        },
    },
]

SEMANTIC_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "semantic_search",
        "description": "Search the project by meaning/description rather than exact text — use this when search_code (exact/regex match) does not find it because you do not know the exact name or wording. Returns the most relevant code snippets.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "A natural-language description of what you are looking for."}},
            "required": ["query"],
        },
    },
}

SYMBOL_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "find_definition",
            "description": "Find where a symbol (function, class, variable...) is defined, using language-aware parsing — more precise than search_code for this because it understands code structure, not just text.",
            "parameters": {
                "type": "object",
                "properties": {"symbol": {"type": "string"}},
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_references",
            "description": 'Find every whole-word usage of a symbol across the project (not substring matches like "add" inside "address"). Use alongside find_definition.',
            "parameters": {
                "type": "object",
                "properties": {"symbol": {"type": "string"}},
                "required": ["symbol"],
            },
        },
    },
]

WRITE_TOOLS = {
    "write_file",
    "edit_file",
    "run_shell",
    "run_shell_background",
    "stop_background_task",
    # spawn_coding_subagents' own branches/worktrees are auto-approved once
    # it's running (see coding_subagent.py) — but launching the whole fan-out
    # is itself a write-capable action, so it goes through the same one-time
    # confirmation as write_file/edit_file/run_shell before it starts.
    "spawn_coding_subagents",
}


def get_tools(cwd: Path, index_name: str = "default") -> list[dict]:
    tools = list(BASE_TOOLS)
    if index_stats(cwd, index_name):
        tools.append(SEMANTIC_SEARCH_TOOL)
    if has_ctags():
        tools.extend(SYMBOL_TOOLS)
    return tools


def needs_confirmation(name: str) -> bool:
    return name in WRITE_TOOLS


def execute_tool(name: str, args: dict, ctx: dict) -> dict:
    cwd: Path = ctx["cwd"]

    if name == "read_file":
        full = _resolve_within_cwd(cwd, args["path"])
        if full is None:
            return {"error": f"Path escapes the project root: {args['path']}"}
        if not full.exists():
            return {"error": f"File not found: {args['path']}"}
        try:
            return {"content": _truncate(full.read_text(encoding="utf-8"))}
        except OSError as err:
            return {"error": str(err)}

    if name == "list_dir":
        rel = args.get("path") or "."
        full = _resolve_within_cwd(cwd, rel)
        if full is None:
            return {"error": f"Path escapes the project root: {rel}"}
        if not full.exists():
            return {"error": f"Path not found: {rel}"}
        try:
            entries = sorted(
                (f"{e.name}/" if e.is_dir() else e.name) for e in full.iterdir() if e.name not in IGNORE_DIRS
            )
            return {"entries": entries}
        except OSError as err:
            return {"error": str(err)}

    if name == "search_code":
        search_path = args.get("path") or "."
        if _resolve_within_cwd(cwd, search_path) is None:
            return {"error": f"Path escapes the project root: {search_path}"}
        if not (cwd / search_path).exists():
            return {"error": f"Path not found: {search_path}"}
        try:
            # Explicit globs, not reliance on .gitignore — rg only skips
            # node_modules et al. automatically when a .gitignore lists them.
            if _has_ripgrep():
                rg_excludes = []
                for d in IGNORE_DIRS:
                    rg_excludes += ["-g", f"!{d}/**"]
                result = subprocess.run(
                    ["rg", "-n", "--no-heading", "-S", "--max-count", "200", *rg_excludes, args["pattern"], search_path],
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode not in (0, 1):
                    return {"error": result.stderr.strip() or f"rg exited {result.returncode}"}
                return {"matches": _truncate(result.stdout.strip()) or "(no matches)"}

            # Fallback: pure-Python search — see _search_code_without_ripgrep
            # for why this isn't a `grep -rn --exclude-dir=...` subprocess.
            return _search_code_without_ripgrep(cwd, search_path, args["pattern"])
        except (OSError, subprocess.TimeoutExpired) as err:
            return {"error": str(err)}

    if name == "edit_file":
        full = _resolve_within_cwd(cwd, args["path"])
        if full is None:
            return {"error": f"Path escapes the project root: {args['path']}"}
        if not full.exists():
            return {"error": f"File not found: {args['path']}"}
        try:
            original = full.read_text(encoding="utf-8")
            count = original.count(args["old_string"])
            if count == 0:
                return {"error": "old_string not found in file. Re-check the exact text (whitespace matters)."}
            if count > 1:
                return {"error": f"old_string matches {count} times — widen it with more surrounding context so it matches exactly once."}
            updated = original.replace(args["old_string"], args["new_string"], 1)
            full.write_text(updated, encoding="utf-8")
            return {"ok": True}
        except OSError as err:
            return {"error": str(err)}

    if name == "write_file":
        full = _resolve_within_cwd(cwd, args["path"])
        if full is None:
            return {"error": f"Path escapes the project root: {args['path']}"}
        try:
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(args["content"], encoding="utf-8")
            return {"ok": True, "bytesWritten": len(args["content"].encode("utf-8"))}
        except OSError as err:
            return {"error": str(err)}

    if name == "semantic_search":
        result = semantic_search(
            args["query"], cwd=cwd, host=ctx["host"], model=ctx["embed_model"], name=ctx.get("index_name", "default")
        )
        if "error" in result:
            return result
        return {
            "results": [
                {
                    "path": r["path"],
                    "lines": f"{r['startLine']}-{r['endLine']}",
                    "score": round(r["score"], 3),
                    "snippet": _truncate(r["text"]),
                }
                for r in result["results"]
            ]
        }

    if name == "find_definition":
        return find_definition(args["symbol"], cwd)

    if name == "find_references":
        result = find_references(args["symbol"], cwd)
        if "error" in result:
            return result
        return {"matches": _truncate(result["matches"])}

    if name == "run_shell":
        try:
            result = subprocess.run(
                args["command"],
                shell=True,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=120,
            )
            return {
                "exitCode": result.returncode,
                "stdout": _truncate(result.stdout or ""),
                "stderr": _truncate(result.stderr or ""),
            }
        except subprocess.TimeoutExpired:
            return {"exitCode": 1, "stdout": "", "stderr": "Command timed out after 120s."}

    if name == "run_shell_background":
        return ctx["background"].start(args["command"], cwd)

    if name == "list_background_tasks":
        return {"tasks": ctx["background"].list()}

    if name == "get_background_output":
        return ctx["background"].output(args["id"])

    if name == "stop_background_task":
        return ctx["background"].stop(args["id"])

    if name == "spawn_subagent":
        from localcoder.subagent import run_subagent  # lazy: subagent.py imports from this module

        return run_subagent(args["task"], ctx, cancel_event=ctx.get("cancel_event"))

    if name == "spawn_subagents":
        from localcoder.subagent import run_subagents  # lazy: subagent.py imports from this module

        return run_subagents(args["tasks"], ctx, cancel_event=ctx.get("cancel_event"))

    if name == "spawn_coding_subagents":
        from localcoder.coding_subagent import run_coding_subagents  # lazy: imports from this module

        return run_coding_subagents(args["tasks"], ctx, cancel_event=ctx.get("cancel_event"))

    return {"error": f"Unknown tool: {name}"}
