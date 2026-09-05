"""Main entry point: the REPL loop. Mirrors src/index.mjs feature-for-feature
(same commands, same session/role/context/index semantics) plus a few
Python-only additions: a full-screen interactive UI on a real TTY (see
fullscreen.py, skipped automatically on piped/non-interactive stdin), a
warm-up call so the model is already loaded before the user's first message,
live session tuning (/model, /set), direct search (/search, /find, no model
round-trip), @-mention context loading, stackable skills alongside the single
active role, creating roles/skills from inside the CLI, and Ctrl+C that
cancels a turn instead of the whole app.

All the business logic below (App: run_turn, slash-command handlers, state)
is shared verbatim between the plain non-interactive path (_run_loop, at the
bottom of this file — what a script, a pipe, or the test suite always gets)
and the full-screen interactive path (fullscreen.py). The only thing that
differs between them is where output goes: App never calls print() or
ui.<verb>() directly — everything goes through self.out, an OutputSink (see
below), so fullscreen.py can redirect it into its transcript pane instead.
"""

from __future__ import annotations

import json
import os
import re
import sys
import traceback
from pathlib import Path

from localcoder import ui
from localcoder.background import BackgroundManager
from localcoder.config import load_config
from localcoder.context import (
    format_context_entry,
    list_context_sets,
    load_context,
    load_context_path,
    load_context_set_paths,
    save_context_set,
)
from localcoder.index_store import (
    build_index,
    get_active_index_name,
    index_stats,
    list_indexes,
    semantic_search,
    set_active_index_name,
)
from localcoder.menu import TOP_COMMANDS
from localcoder.ollama_client import OllamaCancelled, OllamaError, OllamaToolsUnsupported, chat, list_models, warm_up
from localcoder.roles import create_role, format_role, list_roles, load_role
from localcoder.sessions import list_sessions, load_session, save_session
from localcoder.skills import create_skill, format_skill, list_skills, load_skill
from localcoder.symbols import find_definition, find_references
from localcoder.terminal import TerminalError, argv_with_session, open_new_terminal
from localcoder.tools import execute_tool, get_tools, needs_confirmation

# Deliberately short — every extra sentence here is tokens on every request.
SYSTEM_PROMPT = (
    "You are a terminal coding assistant working in the user's project directory. "
    "Use search_code and list_dir to locate relevant code before reading it. "
    "Prefer edit_file (targeted replace) over write_file for changes to existing files — "
    "only use write_file for new files or full rewrites. Keep replies short. "
    "Before a destructive action (editing/overwriting a file, running a command that changes state), "
    "briefly say what you're about to do."
)

# Appended on top of SYSTEM_PROMPT when /socratic is on — a teaching mode
# aimed at someone who wants to keep learning rather than just get an answer.
SOCRATIC_PROMPT = (
    "Socratic mode is on: guide the user with targeted questions and small hints instead of "
    "giving direct answers. You must NOT display, print, or write any code snippets or code blocks. "
    "Guide the user step-by-step to find and write the code themselves, revealing code only if they explicitly ask to just show it. "
    "Stay collaborative and encouraging, not a quiz."
)

MAX_TOOL_ROUNDS = 8

# @path references in a typed message — a lightweight shortcut for
# /context add that works inline. Stops at whitespace or trailing
# punctuation so "check @src/auth.js." doesn't swallow the period.
_AT_MENTION = re.compile(r"@([^\s@]+)")

# Sentinels for the /role create and /skill create inline capture flow: type
# content line by line, "." on its own line saves it, "!" on its own line
# cancels. Works identically whether stdin is a pipe or a real terminal.
_CAPTURE_SAVE = "."
_CAPTURE_CANCEL = "!"


class OutputSink:
    """Where App sends everything it would otherwise print. The default
    (PrintSink, below) prints to real stdout — what the non-interactive path
    always uses. fullscreen.py provides a different implementation that
    appends into its scrollable transcript pane instead, since a full-screen
    prompt_toolkit Application owns the terminal and a stray print() would
    corrupt the display.
    """

    def assistant_label(self) -> None: ...
    def token(self, piece: str) -> None: ...
    def newline(self) -> None: ...
    def tool_call(self, name: str, args: dict) -> None: ...
    def verbose_stats(self, meta: dict) -> None: ...
    def info(self, text: str) -> None: ...
    def ok(self, text: str) -> None: ...
    def warn(self, text: str) -> None: ...
    def err(self, text: str) -> None: ...


class PrintSink(OutputSink):
    def assistant_label(self) -> None:
        ui.assistant_label()

    def token(self, piece: str) -> None:
        sys.stdout.write(piece)
        sys.stdout.flush()

    def newline(self) -> None:
        print()

    def tool_call(self, name: str, args: dict) -> None:
        ui.tool_call(name, args)

    def verbose_stats(self, meta: dict) -> None:
        ui.verbose_stats(meta)

    def info(self, text: str) -> None:
        ui.info(text)

    def ok(self, text: str) -> None:
        ui.ok(text)

    def warn(self, text: str) -> None:
        ui.warn(text)

    def err(self, text: str) -> None:
        ui.err(text)


class App:
    def __init__(self, argv: list[str], out: OutputSink | None = None):
        self.argv = list(argv)
        self.config = load_config(argv)
        self.cwd = Path.cwd()
        self.out = out or PrintSink()

        # active_role: at most one at a time — the "mode" for this session
        # (code review, TDD, a stack-specific persona...). Switching replaces
        # it; it doesn't stack.
        self.active_role = load_role(self.config.role, self.cwd) if self.config.role else None

        # active_skills: unlike role, several can be active at once — each
        # one a reusable playbook layered on top of whatever role is active.
        self.active_skills: list[dict] = []

        # context_entries: user-supplied "always relevant" files/dirs/globs
        # (via config, --context, /context add, or @mention) — project facts
        # and guardrails like ADRs, not a behavioral mode.
        self.context_entries = load_context(self.config.context, self.cwd)

        # context_paths: the raw path/glob strings behind context_entries —
        # kept alongside so /context save can persist "what to load" as a
        # named set (.localcoder/contexts/<name>.json) separately from the
        # loaded content itself, and independently of /session.
        self.context_paths: list[str] = list(self.config.context)

        # conversation: the actual back-and-forth. No system prompt in here —
        # that's assembled fresh on every call in build_messages().
        self.conversation: list = []

        # current_session_name: naming a session (via --session, /session
        # save, or /session new) opts this thread into persistence under
        # .localcoder/sessions/<name>.json. Left None, nothing is written to
        # disk — same ephemeral behavior as before.
        self.current_session_name = self.config.session

        # verbose: per-turn ollama-style stats block (total/load/prompt-eval/
        # eval duration + rate) — off by default, toggled with /verbose or
        # started on with --verbose.
        self.verbose = self.config.verbose

        # debug: off by default — when on, caught errors show a full
        # traceback (via format_error()) instead of just the message, for
        # tracking down "why did that fail" without restarting with -v flags.
        self.debug = False

        # socratic: off by default — when on, an extra system-prompt
        # instruction (SOCRATIC_PROMPT) nudges the model to teach via guided
        # questions rather than just handing over the answer/code.
        self.socratic = False

        # tools_supported: not every model on the Hub does tool-calling
        # (plenty are chat-only) — flips to False the first time Ollama
        # rejects a request specifically for that reason, so later turns
        # stop re-sending tools (and re-hitting the same error) until
        # /model use switches to a different model.
        self.tools_supported = True

        # Cumulative session stats for /stats — separate from `verbose`,
        # which is about per-turn output.
        self.stats = {"turns": 0, "prompt_tokens": 0, "eval_tokens": 0, "total_ns": 0}
        self.last_prompt_tokens: int | None = None

        # capture: while set, the next typed lines are being collected for
        # /role create or /skill create instead of being treated as commands
        # or chat messages — see begin_capture/feed_capture_line.
        self.capture: dict | None = None

        # background: shell tasks started with /bg run or the model's
        # run_shell_background tool, kept alive for the life of this process.
        # Not persisted by session save/load — a live subprocess can't be
        # serialized, and would be meaningless after a restart anyway.
        self.background = BackgroundManager()

        if self.current_session_name:
            saved = load_session(self.current_session_name, self.cwd)
            if saved and saved.get("error"):
                ui.warn(f"[session] {saved['error']}")
            elif saved:
                self.active_role = load_role(saved["role"], self.cwd) if saved.get("role") else None
                self.active_skills = [load_skill(n, self.cwd) for n in (saved.get("skills") or [])]
                self.context_paths = list(saved.get("contextPaths") or [])
                self.context_entries = load_context(self.context_paths, self.cwd)
                self.conversation = list(saved.get("conversation") or [])
            # else: no saved session under this name yet — starts fresh, will
            # be created on the first autosave.

    def autosave(self) -> None:
        if not self.current_session_name:
            return
        role_name = self.active_role["name"] if self.active_role and not self.active_role.get("error") else None
        skill_names = [s["name"] for s in self.active_skills if not s.get("error")]
        save_session(
            self.current_session_name,
            role_name,
            [e["path"] for e in self.context_entries],
            self.conversation,
            self.cwd,
            skills=skill_names,
        )

    def format_error(self, err: BaseException) -> str:
        """/debug on: the full traceback, for tracking down a real failure.
        /debug off (default): just the message, like before.
        """
        if self.debug:
            return f"{err}\n{traceback.format_exc()}"
        return str(err)

    def build_messages(self) -> list:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        if self.socratic:
            messages.append({"role": "system", "content": SOCRATIC_PROMPT})
        if self.active_role and not self.active_role.get("error"):
            messages.append({"role": "system", "content": format_role(self.active_role)})
        for skill in self.active_skills:
            if not skill.get("error"):
                messages.append({"role": "system", "content": format_skill(skill)})
        for entry in self.context_entries:
            messages.append({"role": "system", "content": format_context_entry(entry)})
        messages.extend(self.conversation)
        return messages

    def tool_ctx(self) -> dict:
        return {
            "cwd": self.cwd,
            "host": self.config.host,
            "embed_model": self.config.embed_model,
            "index_name": get_active_index_name(self.cwd),
            "background": self.background,
        }

    def toolbar_state(self) -> dict:
        return {
            "model": self.config.model,
            "role": self.active_role["name"] if self.active_role and not self.active_role.get("error") else None,
            "skill_count": len([s for s in self.active_skills if not s.get("error")]) or None,
            "context_count": len(self.context_entries) or None,
            "session": self.current_session_name,
            "num_ctx": self.config.num_ctx,
            "last_prompt_tokens": self.last_prompt_tokens,
            "debug": self.debug or None,
            "socratic": self.socratic or None,
            "tools_disabled": (not self.tools_supported) or None,
        }

    # ---- the tool-call loop for one user turn ----------------------------
    def run_turn(self, confirm_fn, cancel_event=None, on_response=None) -> None:
        rounds = 0
        while True:
            rounds += 1
            if rounds > MAX_TOOL_ROUNDS:
                self.out.warn(f"[localcoder] Stopping after {MAX_TOOL_ROUNDS} tool rounds to avoid a runaway loop.")
                return

            self.out.assistant_label()

            def attempt(tools):
                return chat(
                    host=self.config.host,
                    model=self.config.model,
                    messages=self.build_messages(),
                    tools=tools,
                    num_ctx=self.config.num_ctx,
                    temperature=self.config.temperature,
                    on_token=self.out.token,
                    cancel_event=cancel_event,
                    on_response=on_response,
                )

            try:
                result = attempt(get_tools(self.cwd, get_active_index_name(self.cwd)) if self.tools_supported else [])
            except OllamaToolsUnsupported:
                # The model itself is fine for plain conversation — only its
                # lack of tool-calling support caused the failure — so retry
                # immediately without tools rather than losing the turn.
                self.tools_supported = False
                self.out.warn(
                    f"[localcoder] {self.config.model} doesn't support tool-calling — continuing "
                    "without tools for this model (no file read/write, search, etc. from here on). "
                    "Switch models with /model use if you need them."
                )
                try:
                    result = attempt([])
                except OllamaCancelled:
                    self.out.newline()
                    self.out.warn("[localcoder] Generation interrupted.")
                    self.conversation.append({"role": "assistant", "content": "[interrupted by user]"})
                    return
                except OllamaError as e:
                    self.out.newline()
                    self.out.err(self.format_error(e))
                    return
            except OllamaCancelled:
                self.out.newline()
                self.out.warn("[localcoder] Generation interrupted.")
                self.conversation.append({"role": "assistant", "content": "[interrupted by user]"})
                return
            except OllamaError as e:
                self.out.newline()
                self.out.err(self.format_error(e))
                return
            self.out.newline()

            meta = result.get("done_meta")
            if meta:
                self.stats["turns"] += 1
                self.stats["prompt_tokens"] += meta.get("prompt_eval_count") or 0
                self.stats["eval_tokens"] += meta.get("eval_count") or 0
                self.stats["total_ns"] += meta.get("total_duration") or 0
                self.last_prompt_tokens = meta.get("prompt_eval_count") or self.last_prompt_tokens
                if self.verbose:
                    self.out.verbose_stats(meta)

            tool_calls = result.get("tool_calls")
            if not tool_calls:
                self.conversation.append({"role": "assistant", "content": result["content"]})
                return

            self.conversation.append({"role": "assistant", "content": result["content"], "tool_calls": tool_calls})

            for i, call in enumerate(tool_calls):
                fn = call.get("function") or {}
                name = fn.get("name")
                raw_args = fn.get("arguments")
                if isinstance(raw_args, str):
                    try:
                        args = json.loads(raw_args)
                    except json.JSONDecodeError:
                        args = {}
                else:
                    args = raw_args or {}
                tool_call_id = call.get("id") or f"{name}_{rounds}_{i}"

                self.out.tool_call(name, args)

                if needs_confirmation(name):
                    approved = confirm_fn("[localcoder] Approve this action?")
                    result_payload = (
                        execute_tool(name, args, self.tool_ctx()) if approved else {"error": "User declined this action."}
                    )
                else:
                    result_payload = execute_tool(name, args, self.tool_ctx())

                self.conversation.append(
                    {"role": "tool", "tool_call_id": tool_call_id, "content": json.dumps(result_payload)}
                )
            # loop again so the model sees the tool results and can respond or call another tool

    # ---- creating roles/skills from inside the CLI -------------------------
    def begin_capture(self, kind: str, name: str) -> None:
        self.capture = {"kind": kind, "name": name, "lines": []}
        self.out.info(
            f'[{kind}] Type the {kind} content below. A line with just "{_CAPTURE_SAVE}" saves it, '
            f'a line with just "{_CAPTURE_CANCEL}" cancels.'
        )

    def feed_capture_line(self, raw_line: str) -> None:
        """Call for every line typed while self.capture is set, instead of
        treating it as a command or chat message."""
        cap = self.capture
        stripped = raw_line.strip()

        if stripped == _CAPTURE_SAVE:
            content = "\n".join(cap["lines"])
            if not content.strip():
                self.out.warn(f"[{cap['kind']}] Empty — nothing saved.")
            else:
                result = create_role(cap["name"], content, self.cwd) if cap["kind"] == "role" else create_skill(cap["name"], content, self.cwd)
                if result.get("error"):
                    self.out.warn(f"[{cap['kind']}] {result['error']}")
                else:
                    self.out.ok(f'[{cap["kind"]}] Saved "{cap["name"]}" to {result["path"]}.')
            self.capture = None
            return

        if stripped == _CAPTURE_CANCEL:
            self.out.warn(f"[{cap['kind']}] Cancelled — nothing saved.")
            self.capture = None
            return

        cap["lines"].append(raw_line)

    # ---- slash commands ----------------------------------------------------
    def handle_role_command(self, rest: str) -> None:
        parts = rest.strip().split()
        sub = parts[0] if parts else None
        name = parts[1] if len(parts) > 1 else None

        if sub == "use" and name:
            role = load_role(name, self.cwd)
            if role.get("error"):
                self.out.warn(f"[role] {role['error']}")
                return
            self.active_role = role
            self.out.ok(f'[role] Now using "{name}" (replaces any previous role).')
            self.autosave()
            return
        if sub == "list":
            names = list_roles(self.cwd)
            if names:
                self.out.info("\n".join(f"  - {n}" for n in names))
            else:
                self.out.warn("[role] No roles found in roles/ or ~/.localcoder/roles/.")
            return
        if sub == "create" and name:
            self.begin_capture("role", name)
            return
        if sub == "clear":
            self.active_role = None
            self.out.ok("[role] Cleared.")
            self.autosave()
            return
        self.out.info("[role] Usage: /role use <name> | /role list | /role create <name> | /role clear")

    def handle_skill_command(self, rest: str) -> None:
        parts = rest.strip().split()
        sub = parts[0] if parts else None
        name = parts[1] if len(parts) > 1 else None

        if sub == "use" and name:
            if any(s["name"] == name for s in self.active_skills if not s.get("error")):
                self.out.info(f'[skill] "{name}" is already active.')
                return
            skill = load_skill(name, self.cwd)
            if skill.get("error"):
                self.out.warn(f"[skill] {skill['error']}")
                return
            self.active_skills.append(skill)
            self.out.ok(f'[skill] Activated "{name}" (skills stack — /skill list shows every active one).')
            self.autosave()
            return
        if sub == "list":
            available = list_skills(self.cwd)
            active_names = {s["name"] for s in self.active_skills if not s.get("error")}
            if available:
                self.out.info("\n".join(f"  - {n}{' (active)' if n in active_names else ''}" for n in available))
            else:
                self.out.warn("[skill] No skills found in skills/ or ~/.localcoder/skills/.")
            return
        if sub == "create" and name:
            self.begin_capture("skill", name)
            return
        if sub == "clear":
            self.active_skills = []
            self.out.ok("[skill] All skills deactivated.")
            self.autosave()
            return
        self.out.info("[skill] Usage: /skill use <name> | /skill list | /skill create <name> | /skill clear")

    def handle_context_command(self, rest: str) -> None:
        parts = rest.strip().split()
        sub = parts[0] if parts else None
        path = " ".join(parts[1:])

        if sub == "add" and path:
            entries = load_context_path(path, self.cwd)
            self.context_entries.extend(entries)
            self.context_paths.append(path)
            for entry in entries:
                if entry["kind"] == "error":
                    self.out.warn(f"[context] {entry['content']}")
                else:
                    self.out.ok(f"[context] Added {entry['path']} ({entry['kind']}, {len(entry['content'])} chars).")
            self.autosave()
            return
        if sub == "list":
            if not self.context_entries:
                self.out.info("[context] Nothing loaded. Use /context add <path>.")
            else:
                self.out.info("\n".join(f"  - {e['path']} ({e['kind']})" for e in self.context_entries))
            return
        if sub == "clear":
            self.context_entries = []
            self.context_paths = []
            self.out.ok("[context] Cleared.")
            self.autosave()
            return
        # save/load/sets: named context sets — a saved list of paths/globs you
        # can switch between with /context load <name>, independent of
        # /session (which bundles context with role/skills/history together).
        if sub == "save" and path:
            save_context_set(self.cwd, path, self.context_paths)
            self.out.ok(f'[context] Saved current context as "{path}" ({len(self.context_paths)} path(s)).')
            return
        if sub == "load" and path:
            paths = load_context_set_paths(self.cwd, path)
            if paths is None:
                self.out.warn(f'[context] No context set named "{path}". Use /context save {path} to create one.')
                return
            self.context_paths = paths
            self.context_entries = load_context(paths, self.cwd)
            self.out.ok(f'[context] Loaded "{path}" ({len(self.context_entries)} entrie(s)).')
            self.autosave()
            return
        if sub == "sets":
            names = list_context_sets(self.cwd)
            if names:
                self.out.info("\n".join(f"  - {n}" for n in names))
            else:
                self.out.info("[context] No saved context sets yet. Use /context save <name>.")
            return
        self.out.info(
            "[context] Usage: /context add <path|glob> | /context list | /context clear | "
            "/context save <name> | /context load <name> | /context sets"
        )

    def handle_session_command(self, rest: str) -> None:
        parts = rest.strip().split()
        sub = parts[0] if parts else None
        name = parts[1] if len(parts) > 1 else None

        if sub == "save":
            target_name = name or self.current_session_name
            if not target_name:
                self.out.warn("[session] Usage: /session save <name> (no active session to save to)")
                return
            self.current_session_name = target_name
            self.autosave()
            self.out.ok(f'[session] Saved as "{target_name}".')
            return
        if sub == "load" and name:
            saved = load_session(name, self.cwd)
            if not saved:
                self.out.warn(f'[session] No session named "{name}". Use /session new {name} to start one.')
                return
            if saved.get("error"):
                self.out.warn(f"[session] {saved['error']}")
                return
            self.current_session_name = name
            self.active_role = load_role(saved["role"], self.cwd) if saved.get("role") else None
            self.active_skills = [load_skill(n, self.cwd) for n in (saved.get("skills") or [])]
            self.context_paths = list(saved.get("contextPaths") or [])
            self.context_entries = load_context(self.context_paths, self.cwd)
            self.conversation = list(saved.get("conversation") or [])
            self.out.ok(f'[session] Loaded "{name}" ({len(self.conversation)} messages, role: {saved.get("role") or "none"}).')
            return
        if sub == "new" and name:
            command = [sys.executable, "-m", "localcoder", *argv_with_session(self.argv, name)]
            try:
                open_new_terminal(self.cwd, command)
            except TerminalError as err:
                self.out.warn(f"[session] Couldn't open a new terminal ({err}) — starting here instead.")
                self.current_session_name = name
                self.conversation = []
                self.autosave()
                self.out.ok(f'[session] Started new session "{name}".')
                return
            self.out.ok(f'[session] Opened new session "{name}" in a new terminal.')
            return
        if sub == "list":
            names = list_sessions(self.cwd)
            if names:
                self.out.info("\n".join(f"  - {n}{' (active)' if n == self.current_session_name else ''}" for n in names))
            else:
                self.out.info("[session] No saved sessions in this project yet.")
            return
        self.out.info("[session] Usage: /session save [name] | /session load <name> | /session new <name> | /session list")

    def handle_index_command(self, rest: str) -> None:
        parts = rest.strip().split()
        sub = parts[0] if parts else None

        if sub == "build":
            # /index build [name] [model] — name defaults to whatever index
            # is currently active ("default" if none yet), model defaults to
            # the configured embed_model. Naming lets several indexes coexist
            # (e.g. one per embed model, to compare quality) since each is
            # its own file; building always makes that name the active one.
            name = parts[1] if len(parts) > 1 else get_active_index_name(self.cwd)
            model = parts[2] if len(parts) > 2 else self.config.embed_model
            self.out.info(f'[index] Building "{name}" with {model} (this embeds every changed file — may take a while on first run)...')

            # A live-updating progress line only makes sense when we own real
            # stdout — the full-screen sink just gets the final summary.
            if isinstance(self.out, PrintSink):
                def on_progress(path, i, total):
                    sys.stdout.write(f"\r[index] {i}/{total} {path}".ljust(80))
                    sys.stdout.flush()
            else:
                def on_progress(path, i, total):
                    return None

            try:
                result = build_index(self.cwd, self.config.host, model, on_progress, name=name)
                if isinstance(self.out, PrintSink):
                    print()
                set_active_index_name(self.cwd, name)
                self.out.ok(f"[index] Done — {result['fileCount']} files ({result['embedded']} embedded, {result['reused']} unchanged/reused).")
            except Exception as err:  # noqa: BLE001 — surface any embedding/network failure to the user
                if isinstance(self.out, PrintSink):
                    print()
                self.out.err(f"[index] Failed: {err}")
            return
        if sub == "use" and len(parts) > 1:
            name = parts[1]
            if not index_stats(self.cwd, name):
                self.out.warn(f'[index] No index named "{name}". Use /index build {name} to create one.')
                return
            set_active_index_name(self.cwd, name)
            self.out.ok(f'[index] Now using "{name}".')
            return
        if sub == "list":
            indexes = list_indexes(self.cwd)
            if not indexes:
                self.out.info("[index] No index built yet. Run /index build.")
                return
            active = get_active_index_name(self.cwd)
            self.out.info(
                "\n".join(
                    f"  - {i['name']}{' (active)' if i['name'] == active else ''}: "
                    f"{i['fileCount']} files, {i['chunkCount']} chunks, model {i['model']}"
                    for i in indexes
                )
            )
            return
        if sub == "status":
            name = get_active_index_name(self.cwd)
            stats = index_stats(self.cwd, name)
            if stats:
                label = "" if name == "default" else f'"{name}": '
                self.out.info(f"[index] {label}{stats['fileCount']} files, {stats['chunkCount']} chunks, model {stats['model']}, built {stats['updatedAt']}")
            else:
                self.out.info("[index] No index built yet. Run /index build.")
            return
        self.out.info("[index] Usage: /index build [name] [model] | /index use <name> | /index list | /index status")

    def handle_bg_command(self, rest: str) -> None:
        parts = rest.strip().split(maxsplit=1)
        sub = parts[0] if parts else None
        arg = parts[1] if len(parts) > 1 else None

        if sub == "run" and arg:
            result = self.background.start(arg, self.cwd)
            if result.get("error"):
                self.out.err(f"[bg] {result['error']}")
            else:
                self.out.ok(f"[bg] Started {result['id']}: {arg}")
            return
        if sub == "list":
            tasks = self.background.list()
            if not tasks:
                self.out.info("[bg] No background tasks yet — start one with /bg run <command>.")
            else:
                for t in tasks:
                    state = "running" if t["running"] else f"exited {t['exitCode']}"
                    self.out.info(f"  {t['id']}  [{state}]  {t['command']}")
            return
        if sub == "output" and arg:
            result = self.background.output(arg)
            if result.get("error"):
                self.out.warn(f"[bg] {result['error']}")
                return
            state = "running" if result["running"] else f"exited {result['exitCode']}"
            self.out.info(f"[bg] {result['id']} [{state}] {result['command']}")
            if result["stdout"]:
                self.out.info(result["stdout"])
            if result["stderr"]:
                self.out.warn(result["stderr"])
            return
        if sub == "stop" and arg:
            result = self.background.stop(arg)
            if result.get("error"):
                self.out.warn(f"[bg] {result['error']}")
            elif result.get("note"):
                self.out.info(f"[bg] {arg} {result['note']}.")
            else:
                self.out.ok(f"[bg] Stopped {arg}.")
            return
        self.out.info("[bg] Usage: /bg run <command> | /bg list | /bg output <id> | /bg stop <id>")

    def handle_model_command(self, rest: str) -> None:
        parts = rest.strip().split()
        sub = parts[0] if parts else None
        name = parts[1] if len(parts) > 1 else None

        if sub == "use" and name:
            self.config.model = name
            self.tools_supported = True  # give the new model a fresh chance
            self.out.ok(f'[model] Now using "{name}".')
            return
        if sub == "list":
            names = list_models(self.config.host)
            if names:
                self.out.info("\n".join(f"  - {n}" for n in names))
            else:
                self.out.warn("[model] Could not reach Ollama, or no models pulled yet.")
            return
        self.out.info("[model] Usage: /model use <name> | /model list")

    def handle_set_command(self, rest: str) -> None:
        parts = rest.strip().split()
        sub = parts[0] if parts else None
        value = parts[1] if len(parts) > 1 else None

        if sub == "temperature" and value is not None:
            try:
                self.config.temperature = float(value)
                self.out.ok(f"[set] temperature = {self.config.temperature}")
            except ValueError:
                self.out.warn(f'[set] "{value}" is not a valid number.')
            return
        if sub == "num_ctx" and value is not None:
            try:
                self.config.num_ctx = int(value)
                self.out.ok(f"[set] num_ctx = {self.config.num_ctx}")
            except ValueError:
                self.out.warn(f'[set] "{value}" is not a valid integer.')
            return
        if sub == "embed_model" and value is not None:
            self.config.embed_model = value
            self.out.ok(f'[set] embed_model = "{value}" (used by the next /index build).')
            return
        self.out.info("[set] Usage: /set temperature <value> | /set num_ctx <value> | /set embed_model <name>")

    def handle_stats_command(self) -> None:
        s = self.stats
        self.out.info(f"[stats] model: {self.config.model}   num_ctx: {self.config.num_ctx}   temperature: {self.config.temperature}")
        self.out.info(f"[stats] turns: {s['turns']}   messages in history: {len(self.conversation)}")
        self.out.info(f"[stats] prompt tokens (cumulative): {s['prompt_tokens']}   eval tokens (cumulative): {s['eval_tokens']}")
        total_s = (s["total_ns"] or 0) / 1e9
        self.out.info(f"[stats] total generation time: {total_s:.1f}s")
        if self.last_prompt_tokens:
            pct = round(100 * self.last_prompt_tokens / self.config.num_ctx)
            self.out.info(f"[stats] last turn used ~{self.last_prompt_tokens}/{self.config.num_ctx} tokens of context (~{pct}%)")
        self.out.info(f"[stats] verbose per-turn output: {'on' if self.verbose else 'off'} (toggle with /verbose)")

    def toggle_verbose(self) -> None:
        self.verbose = not self.verbose
        state = "on" if self.verbose else "off"
        self.out.ok(f"[verbose] {state} — per-turn stats (like `ollama --verbose`) will {'show' if self.verbose else 'stay hidden'} after each reply.")

    def toggle_debug(self) -> None:
        self.debug = not self.debug
        state = "on" if self.debug else "off"
        self.out.ok(f"[debug] {state} — errors will {'include a full traceback' if self.debug else 'show a short message'} from now on.")

    def toggle_socratic(self) -> None:
        self.socratic = not self.socratic
        state = "on" if self.socratic else "off"
        self.out.ok(
            f"[socratic] {state} — {ui.MASCOT_NAME} will "
            f"{'guide you with questions instead of giving direct answers' if self.socratic else 'answer directly again'}."
        )

    def handle_summary_command(self, rest: str, cancel_event=None, on_response=None) -> None:
        path = rest.strip() or None
        if not self.conversation:
            self.out.warn("[summary] Nothing to summarize yet.")
            return
        recap_request = (
            "Summarize this conversation so far for someone resuming it later: what was asked, "
            "what was done, key decisions, and the current state. Be concise, use bullet points."
        )
        messages = self.build_messages() + [{"role": "user", "content": recap_request}]
        self.out.info("[summary] Asking the model for a recap...")
        try:
            result = chat(
                host=self.config.host,
                model=self.config.model,
                messages=messages,
                tools=[],
                num_ctx=self.config.num_ctx,
                temperature=self.config.temperature,
                cancel_event=cancel_event,
                on_response=on_response,
            )
        except OllamaCancelled:
            self.out.warn("[summary] Cancelled.")
            return
        except OllamaError as e:
            self.out.err(self.format_error(e))
            return
        text = result["content"]
        if path:
            full = (self.cwd / path).resolve()
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(text, encoding="utf-8")
            self.out.ok(f"[summary] Written to {path}.")
        else:
            self.out.newline()
            self.out.info(text)
            self.out.newline()

    def handle_search_command(self, rest: str) -> None:
        pattern = rest.strip()
        if not pattern:
            self.out.info("[search] Usage: /search <pattern>")
            return
        result = execute_tool("search_code", {"pattern": pattern}, {"cwd": self.cwd})
        if "error" in result:
            self.out.err(result["error"])
            return
        if result["matches"] != "(no matches)":
            self.out.info(result["matches"])
            return

        # "/search trouve quand je mets le nom de la fonction mais pas avec
        # du texte libre" — expected: /search is an exact text/regex search,
        # so a description like "une fonction qui additionne" only matches
        # if those exact words appear in the code. When nothing turned up,
        # fall back to meaning-based search over the built index (if there
        # is one) instead of just reporting failure — this is exactly what
        # semantic_search gives the model, now reachable directly too.
        active_index = get_active_index_name(self.cwd)
        if not index_stats(self.cwd, active_index):
            self.out.info(
                "[search] (no exact matches). That's expected for a description rather than "
                'exact wording — run "/index build" once to also enable meaning-based search here.'
            )
            return

        self.out.info("[search] No exact matches — trying meaning-based search over the index instead...")
        semantic = semantic_search(pattern, cwd=self.cwd, host=self.config.host, model=self.config.embed_model, name=active_index)
        if "error" in semantic:
            self.out.warn(f"[search] {semantic['error']}")
            return
        results = semantic.get("results") or []
        if not results:
            self.out.info("[search] (no matches, even by meaning)")
            return
        for r in results:
            # Show several lines of the actual chunk, not just its first line —
            # a single line is often a module docstring/comment, which reads as
            # "just doc, not code" even though the code follows right after it.
            lines = r["text"].strip("\n").splitlines()[:6]
            snippet = "\n".join(f"    {line}" for line in lines)
            self.out.info(f"  {r['path']}:{r['startLine']}-{r['endLine']} ({r['score']:.2f})\n{snippet}")

    def handle_find_command(self, rest: str) -> None:
        symbol = rest.strip()
        if not symbol:
            self.out.info("[find] Usage: /find <symbol>")
            return
        if " " in symbol:
            # A common mix-up: /find wants the exact identifier (a function,
            # class or variable name), not a description of what it does —
            # "/find a function that adds" will never match anything because
            # no symbol is literally named that.
            self.out.warn(
                f'[find] "{symbol}" looks like a description, not a symbol name — /find needs the exact '
                "identifier (e.g. `/find add`). Use /search for free-text or regex search instead."
            )
            return

        definition = find_definition(symbol, self.cwd)
        if definition.get("error"):
            self.out.warn(f"[find] {definition['error']}")
        else:
            matches = definition.get("matches") or []
            if matches:
                self.out.info(f'[find] Definition(s) of "{symbol}":')
                for m in matches:
                    self.out.info(f"  {m['path']}:{m['line']} ({m['kind']}) — {m['preview']}")
            else:
                self.out.info(definition.get("note") or f'[find] No definition found for "{symbol}".')

        refs = find_references(symbol, self.cwd)
        if refs.get("error"):
            self.out.warn(f"[find] {refs['error']}")
            return
        self.out.newline()
        self.out.info(f'[find] References to "{symbol}":')
        self.out.info(refs["matches"])

    def apply_at_mentions(self, text: str) -> None:
        existing_paths = {e["path"] for e in self.context_entries}
        for match in _AT_MENTION.finditer(text):
            candidate = match.group(1).rstrip(".,;:!?)")
            if not candidate or candidate in existing_paths:
                continue
            if not (self.cwd / candidate).exists():
                continue
            entries = load_context_path(candidate, self.cwd)
            self.context_entries.extend(entries)
            existing_paths.add(candidate)
            for entry in entries:
                if entry["kind"] != "error":
                    self.out.ok(f"[context] Added {entry['path']} via @mention ({entry['kind']}, {len(entry['content'])} chars).")


def _dispatch_command(app: App, trimmed: str, cancel_event=None, on_response=None) -> bool:
    """Handles one already-trimmed non-empty line that isn't a chat message
    or a pending role/skill capture. Returns True if it matched a command
    (caller should move on to the next input line) or False if it should be
    treated as a normal chat message. Shared between the plain and
    full-screen loops so slash-command behavior can never drift between them.
    Callers must check for "/exit" themselves before calling this — it's not
    handled here since exiting means breaking the *caller's* loop.

    `cancel_event`/`on_response` only matter to the one command that itself
    talks to Ollama (/summary) — every other branch ignores them.
    """
    if trimmed == "/help":
        app.out.info("Commands:")
        for c in TOP_COMMANDS:
            app.out.info(f"  {c['cmd']:<16} {c['desc']}")
        app.out.newline()
        return True
    if trimmed == "/reset":
        app.conversation = []  # role/skills/context are untouched on purpose
        app.out.ok("[localcoder] Conversation reset (role/skills/context kept — use /role clear, /skill clear or /context clear to drop them too).")
        app.autosave()
        return True
    if trimmed == "/stats":
        app.handle_stats_command()
        return True
    if trimmed == "/verbose":
        app.toggle_verbose()
        return True
    if trimmed == "/debug":
        app.toggle_debug()
        return True
    if trimmed == "/socratic":
        app.toggle_socratic()
        return True
    if trimmed.startswith("/summary"):
        app.handle_summary_command(trimmed[len("/summary"):], cancel_event=cancel_event, on_response=on_response)
        return True
    if trimmed.startswith("/search"):
        app.handle_search_command(trimmed[len("/search"):])
        return True
    if trimmed.startswith("/find"):
        app.handle_find_command(trimmed[len("/find"):])
        return True
    if trimmed.startswith("/index"):
        app.handle_index_command(trimmed[len("/index"):])
        return True
    if trimmed.startswith("/session"):
        app.handle_session_command(trimmed[len("/session"):])
        return True
    if trimmed.startswith("/skill"):
        app.handle_skill_command(trimmed[len("/skill"):])
        return True
    if trimmed.startswith("/role"):
        app.handle_role_command(trimmed[len("/role"):])
        return True
    if trimmed.startswith("/context"):
        app.handle_context_command(trimmed[len("/context"):])
        return True
    if trimmed.startswith("/bg"):
        app.handle_bg_command(trimmed[len("/bg"):])
        return True
    if trimmed.startswith("/model"):
        app.handle_model_command(trimmed[len("/model"):])
        return True
    if trimmed.startswith("/set"):
        app.handle_set_command(trimmed[len("/set"):])
        return True
    return False


def _relaunch() -> None:
    """Re-exec the current process with the same interpreter, module and
    command-line arguments — a clean "/restart": the new process runs the full
    startup again (config, banner, warm-up) from the same directory. Anything
    that must survive the relaunch lives on disk — `/restart` autosaves first,
    exactly like `/session save` or `/reset` do. os.execv replaces the process
    image in place, so under `--watch` the watcher still sees the very same
    child (same PID): no double spawn, no missed cleanup.
    """
    os.execv(
        sys.executable,
        [sys.executable, "-m", "localcoder", *sys.argv[1:]],
    )


def _run_loop(app: App) -> None:
    """The plain, non-interactive loop: blocking input()/print(), no menu, no
    full-screen layout. Always used when stdin isn't a real TTY (scripts,
    pipes, the test suite) — see main() below for the interactive branch.
    """

    def ask_line() -> str:
        return input("you> ")

    def confirm(question: str) -> bool:
        if app.config.auto_approve:
            return True
        try:
            answer = input(f"{question} [y/N] ")
        except EOFError:
            return False
        return answer.strip().lower() == "y"

    while True:
        try:
            raw = ask_line()
        except (EOFError, KeyboardInterrupt):
            # stdin closed (Ctrl+D, pipe ended) or Ctrl+C at the idle prompt — exit cleanly.
            break

        if app.capture is not None:
            app.feed_capture_line(raw)
            print()
            continue

        trimmed = raw.strip()

        if trimmed == "/exit":
            break
        if trimmed == "/restart":
            app.autosave()
            print()
            try:
                _relaunch()
            except OSError as err:  # execv barely ever fails — but stay in the REPL if it does
                ui.err(f"[localcoder] Restart failed: {err}")
                continue
        if not trimmed:
            continue

        if _dispatch_command(app, trimmed):
            print()
            continue

        ui.user_separator()
        app.apply_at_mentions(trimmed)
        app.conversation.append({"role": "user", "content": trimmed})
        try:
            app.run_turn(confirm)
            app.autosave()
        except KeyboardInterrupt:
            # Cancel just this turn — not the whole app. A synthetic note
            # keeps the transcript well-formed (a user message always
            # followed by something) for the next call to the model.
            print()
            ui.warn("[localcoder] Generation interrupted.")
            app.conversation.append({"role": "assistant", "content": "[interrupted by user]"})
            app.autosave()
        except Exception as err:  # noqa: BLE001 — never let one bad turn kill the REPL
            ui.err(app.format_error(err))
        print()


def main() -> None:
    argv = sys.argv[1:]

    # Dev mode: --watch (or --watch-path <chemin>) relaunches the whole REPL
    # as a child process whenever a watched file changes. The flags are
    # stripped first — the child must never see them.
    from localcoder.watch import run_with_watch, split_watch_args

    watch_argv, watch_paths = split_watch_args(argv)
    if watch_argv != argv:
        sys.exit(run_with_watch(watch_argv, watch_paths))

    app = App(argv)
    interactive = sys.stdin.isatty()

    if interactive:
        from localcoder.fullscreen import run_fullscreen

        run_fullscreen(app)
        return

    ui.banner(
        app.config,
        app.cwd,
        app.current_session_name,
        app.active_role,
        app.context_entries,
        index_stats(app.cwd),
        interactive,
        skills=app.active_skills,
    )

    if app.config.warm_up:
        ui.info("[localcoder] Warming up the model...")
        try:
            warm_up(app.config.host, app.config.model)
        except OllamaError as err:
            ui.warn(f"[localcoder] Warm-up skipped: {err}")
        print()

    _run_loop(app)


if __name__ == "__main__":
    main()
