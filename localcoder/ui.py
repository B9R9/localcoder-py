"""Branding + all colored/styled terminal output. One place for the theme so
the REPL stays readable — no ANSI codes scattered through repl.py.

Every piece of output is built as a small HTML-tagged fragment string first
(the `*_fragment`/`*_fragments` functions — pure, no IO) and only then
printed. That split exists so the exact same styling can be reused by two
very different renderers: the plain print()-based non-interactive path below,
and fullscreen.py's full-screen transcript pane, which can't call print()
(it owns the terminal) but wants identical colors/wording.

Mascot: Lazzy the panda (🐼) — kept as an emoji rather than hand-drawn ASCII
art so it always renders correctly regardless of terminal font, and kept
OUTSIDE any bordered box below so emoji double-width quirks never throw off
box alignment.
"""

from __future__ import annotations

import html
import json
import re

from prompt_toolkit import print_formatted_text
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.styles import Style

THEME = Style.from_dict(
    {
        "logo.mark": "bold fg:#ff6b6b",
        "logo": "bold fg:#61dafb",
        "tagline": "italic fg:#888888",
        "box": "fg:#61dafb",
        "label": "fg:#888888",
        "value": "bold fg:#eeeeee",
        "dim": "fg:#666666",
        "ok": "fg:#98c379",
        "warn": "fg:#e5c07b",
        "err": "bold fg:#e06c75",
        "tool": "fg:#c678dd",
        "tool.name": "bold fg:#e5c07b",
        "prompt.user": "bold fg:#61dafb",
        "prompt.assistant": "bold fg:#c792ea",
        "prompt.assistant.mark": "bold fg:#ff6b6b",
        "separator": "fg:#3a3a3a",
        "role": "fg:#56b6c2",
        "skill": "fg:#98c379",
        "context": "fg:#d19a66",
        "session": "fg:#c678dd",
        "spinner": "bold fg:#e5c07b",
        "stat.label": "fg:#61dafb",
        "stat.value": "bold fg:#eeeeee",
        "completion-menu.completion": "bg:#2b2b2b fg:#cccccc",
        "completion-menu.completion.current": "bg:#61dafb fg:#1e1e1e bold",
        "completion-menu.meta.completion": "bg:#2b2b2b fg:#888888",
        "completion-menu.meta.completion.current": "bg:#61dafb fg:#1e1e1e",
        "menu.cmd": "bold fg:#61dafb",
        "menu.cmd.current": "bold fg:#1e1e1e bg:#61dafb",
        "menu.desc": "fg:#888888",
        "menu.desc.current": "fg:#1e1e1e bg:#61dafb",
        "menu.hint": "fg:#666666",
        "bottom-toolbar": "bg:#2b2b2b fg:#aaaaaa",
        # The "/" menu's popup border (prompt_toolkit's Frame widget) —
        # matches the boxed look of the reference screenshot's /help and
        # Settings panels.
        "frame.border": "fg:#61dafb",
        "frame.label": "bold fg:#61dafb",
        # Streamed-reply code formatting — a fenced block gets its own dark
        # panel so it reads as a distinct block of code, inline `code` just
        # a distinct color inline with the surrounding prose.
        "code": "bg:#2b2b2b fg:#abb2bf",
        "code.inline": "fg:#61afef",
    }
)

MASCOT = "🐼"
MASCOT_NAME = "Lazzy"

# A little left-to-right "roll" while the model warms up — no rotated-panda
# glyph exists in Unicode, so the illusion is built by moving the emoji
# across a fixed-width track instead, like a loading bar.
_SPINNER_TRACK_WIDTH = 12


def spinner_frame(tick: int) -> str:
    """Pure function: which frame of the rolling-panda animation to show at
    animation step `tick` (any non-negative int — it wraps). Kept separate
    from any actual printing/timer so it's unit-testable.
    """
    span = _SPINNER_TRACK_WIDTH - 1
    period = span * 2  # bounce there and back
    pos = tick % period
    if pos > span:
        pos = period - pos
    return "[" + " " * pos + MASCOT + " " * (span - pos) + "]"


def separator(width: int = 60) -> str:
    return "─" * width


def _esc(value) -> str:
    return html.escape(str(value))


def _print(fragment: str) -> None:
    print_formatted_text(HTML(fragment), style=THEME)


def _box(lines: list[str]) -> str:
    """A plain-ASCII bordered box (no emoji inside — see module docstring).
    Width fits the longest line."""
    width = max(len(line) for line in lines)
    top = "┌" + "─" * (width + 2) + "┐"
    bottom = "└" + "─" * (width + 2) + "┘"
    middle = [f"│ {line.ljust(width)} │" for line in lines]
    return "\n".join([top, *middle, bottom])


def clear_screen() -> None:
    """Wipes the terminal before the banner, so every launch starts from a
    clean screen instead of scrolling on below whatever was there before —
    same as Vibe. Uses the real ANSI clear+home sequence rather than the
    `clear` binary so it works with no subprocess and no PATH dependency.
    """
    print("\033[2J\033[H", end="")


# ---- fragment builders (pure — no printing) --------------------------------

def assistant_label_fragment() -> str:
    return f"<prompt.assistant.mark>{MASCOT}</prompt.assistant.mark> <prompt.assistant>{MASCOT_NAME}&gt;</prompt.assistant> "


def user_separator_fragment() -> str:
    return f"<separator>{separator()}</separator>"


_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")


def _render_inline_code(text: str) -> str:
    """Escapes `text` for HTML display, styling `inline code` spans
    distinctly from surrounding prose."""
    pieces = []
    last = 0
    for m in _INLINE_CODE_RE.finditer(text):
        pieces.append(_esc(text[last : m.start()]))
        pieces.append(f"<code.inline>{_esc(m.group(1))}</code.inline>")
        last = m.end()
    pieces.append(_esc(text[last:]))
    return "".join(pieces)


def render_streamed_reply(raw: str) -> str:
    """Turns raw streamed assistant markdown into an HTML-tagged fragment for
    the transcript, styling ```fenced code blocks``` and `inline code`
    distinctly from prose so code is easy to pick out at a glance instead of
    blurring into one plain stream of text ("quand tu code est affiche on le
    fomat pour une meilleure visibilite"). Safe to call on partial text —
    the reply is re-rendered from scratch on every token, so an unclosed
    fence at the end of what's arrived so far is simply shown as code
    already and settles into its final form as more of it streams in.
    """
    segments = raw.split("```")
    rendered = []
    for i, segment in enumerate(segments):
        if i % 2 == 1:  # inside a fenced code block
            # Drop a leading ```lang hint line, if there is one — it's
            # metadata for a markdown renderer, not something worth
            # displaying inline in a terminal transcript.
            lines = segment.split("\n", 1)
            body = lines[1] if len(lines) == 2 else segment
            rendered.append(f"<code>{_esc(body)}</code>")
        else:
            rendered.append(_render_inline_code(segment))
    return "".join(rendered)


def tool_call_fragment(name: str, args: dict) -> str:
    try:
        rendered = json.dumps(args)
    except (TypeError, ValueError):
        rendered = str(args)
    return f"<tool>[tool] <tool.name>{_esc(name)}</tool.name>({_esc(rendered)})</tool>"


def info_fragment(text: str) -> str:
    return f"<dim>{_esc(text)}</dim>"


def ok_fragment(text: str) -> str:
    return f"<ok>{_esc(text)}</ok>"


def warn_fragment(text: str) -> str:
    return f"<warn>{_esc(text)}</warn>"


def err_fragment(text: str) -> str:
    return f"<err>[error] {_esc(text)}</err>"


def _fmt_duration(nanoseconds) -> str:
    if not nanoseconds:
        return "0ms"
    seconds = nanoseconds / 1e9
    if seconds >= 1:
        return f"{seconds:.2f}s"
    return f"{seconds * 1000:.1f}ms"


def _fmt_rate(count, nanoseconds) -> str:
    if not count or not nanoseconds:
        return "n/a"
    seconds = nanoseconds / 1e9
    if seconds <= 0:
        return "n/a"
    return f"{count / seconds:.2f} tokens/s"


def verbose_stats_lines(meta: dict) -> list[tuple[str, str]]:
    """Pure: the (label, value) pairs of a per-turn breakdown in the same
    shape as `ollama run --verbose`.
    """
    total = meta.get("total_duration")
    load = meta.get("load_duration")
    p_count = meta.get("prompt_eval_count")
    p_dur = meta.get("prompt_eval_duration")
    e_count = meta.get("eval_count")
    e_dur = meta.get("eval_duration")

    return [
        ("total duration", _fmt_duration(total)),
        ("load duration", _fmt_duration(load)),
        ("prompt eval count", f"{p_count or 0} token(s)"),
        ("prompt eval duration", _fmt_duration(p_dur)),
        ("prompt eval rate", _fmt_rate(p_count, p_dur)),
        ("eval count", f"{e_count or 0} token(s)"),
        ("eval duration", _fmt_duration(e_dur)),
        ("eval rate", _fmt_rate(e_count, e_dur)),
    ]


def verbose_stats_fragments(meta: dict) -> list[str]:
    return [
        f"<stat.label>{_esc(f'{label:<21}')}</stat.label> <stat.value>{_esc(value)}</stat.value>"
        for label, value in verbose_stats_lines(meta)
    ]


def banner_fragments(config, cwd, session_name, role, context_entries, idx_stats, interactive: bool, skills=None) -> list[str]:
    box = _box(
        [
            f"{MASCOT_NAME} says hi — local-first coding agent",
            "Explicit context, zero bloat. Type /help to get going.",
        ]
    )
    lines = [f"\n<logo.mark>{MASCOT}</logo.mark>  <logo>localcoder</logo>"]
    lines += [f"<box>{_esc(line)}</box>" for line in box.split("\n")]
    lines.append("")

    lines.append(
        f"<label>model</label> <value>{_esc(config.model)}</value>"
        f"   <label>num_ctx</label> <value>{config.num_ctx}</value>"
        f"   <label>temperature</label> <value>{config.temperature}</value>"
        f"   <label>host</label> <value>{_esc(config.host)}</value>"
    )
    lines.append(f"<label>project</label> <value>{_esc(cwd)}</value>")

    if session_name:
        lines.append(f"<label>session</label> <session>{_esc(session_name)}</session>")
    if role:
        if role.get("error"):
            lines.append(f"<warn>role: {_esc(role['error'])}</warn>")
        else:
            lines.append(f"<label>role</label> <role>{_esc(role['name'])}</role>")
    if skills:
        names = ", ".join(_esc(s["name"]) for s in skills if not s.get("error"))
        if names:
            lines.append(f"<label>skills</label> <skill>{names}</skill>")
    if context_entries:
        paths = ", ".join(_esc(e["path"]) for e in context_entries)
        lines.append(f"<label>context</label> <context>{paths}</context>")
    if idx_stats:
        lines.append(
            f"<label>index</label> <value>{idx_stats['fileCount']} files, {idx_stats['chunkCount']} chunks</value> "
            f"<tagline>(built {_esc(idx_stats['updatedAt'])})</tagline>"
        )

    lines.append(
        "<dim>Commands: /index build|use|list|status  /session save|load|new|list  /role use|list|create|clear  "
        "/skill use|list|create|clear  /context add|list|clear|save|load|sets  /model use|list  /set temperature|num_ctx  "
        "/stats  /verbose  /debug  /socratic  /summary  /search  /find  /reset  /restart  /help  /exit</dim>"
    )
    if interactive:
        lines.append(
            '<dim>Type "/" for a centered menu — ↑/↓ to move, Enter/Tab to pick, Esc to clear. '
            "@path in a message loads that file/dir into context. Ctrl+C cancels a reply in progress; "
            "at an empty prompt, Ctrl+C or Ctrl+D quits. PageUp/PageDown scroll this pane (native "
            "copy/paste works normally, so the mouse is left alone).</dim>"
        )
    lines.append("")
    return lines


def banner(config, cwd, session_name, role, context_entries, idx_stats, interactive: bool, skills=None) -> None:
    for fragment in banner_fragments(config, cwd, session_name, role, context_entries, idx_stats, interactive, skills):
        if fragment == "":
            print()
        else:
            _print(fragment)


def bottom_toolbar(state: dict):
    """state: {model, role, context_count, session, num_ctx, last_prompt_tokens}.
    Returns an HTML fragment for prompt_toolkit's bottom_toolbar — a persistent
    status line (model / role / context / session / rough context usage), the
    same idea as Vibe's bottom bar.
    """
    parts = [f"{MASCOT} {_esc(state.get('model') or '?')}"]
    if state.get("role"):
        parts.append(f"role: {_esc(state['role'])}")
    if state.get("skill_count"):
        parts.append(f"skills: {state['skill_count']}")
    if state.get("context_count"):
        parts.append(f"context: {state['context_count']}")
    if state.get("session"):
        parts.append(f"session: {_esc(state['session'])}")
    if state.get("socratic"):
        parts.append("socratic")
    if state.get("debug"):
        parts.append("debug")
    if state.get("tools_disabled"):
        parts.append("tools: off")
    num_ctx = state.get("num_ctx")
    last_tokens = state.get("last_prompt_tokens")
    if num_ctx:
        shown = f"~{last_tokens}/{num_ctx} tok" if last_tokens else f"{num_ctx} tok ctx"
        parts.append(shown)
    return HTML(" · ".join(parts))


def assistant_label() -> None:
    _print(assistant_label_fragment())


def user_separator() -> None:
    """A thin rule printed before each user turn, purely so a scrollback full
    of exchanges is easy to tell apart at a glance."""
    _print(f"\n{user_separator_fragment()}")


def tool_call(name: str, args: dict) -> None:
    _print(f"\n{tool_call_fragment(name, args)}")


def info(text: str) -> None:
    _print(info_fragment(text))


def ok(text: str) -> None:
    _print(ok_fragment(text))


def warn(text: str) -> None:
    _print(warn_fragment(text))


def err(text: str) -> None:
    _print(err_fragment(text))


def verbose_stats(meta: dict) -> None:
    """Prints a per-turn breakdown in the same shape as `ollama run --verbose`."""
    for fragment in verbose_stats_fragments(meta):
        _print(fragment)
