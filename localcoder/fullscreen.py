"""Full-screen interactive UI — Vibe-style layout: a scrollable transcript
pane on top, a status/spinner line, and an input box pinned at the bottom,
all inside one prompt_toolkit Application running on the terminal's
alternate screen. Only used when stdin is a real TTY (see repl.py's main());
piped/non-interactive stdin (scripts, the test suite) always gets the plain
linear print()/input() loop in repl.py instead, which this module never
touches.

Design: App (repl.py) owns all the business logic — run_turn, slash-command
handlers, session/role/skill/context state — completely unchanged from the
non-interactive path. The only two things this module adds are:

  1. BufferSink: an OutputSink (see repl.py) that appends into the
     transcript pane instead of printing to real stdout.
  2. Concurrency: a turn runs in a single-worker background thread (via
     ThreadPoolExecutor) so the UI (spinner, Ctrl+C) stays responsive while
     Ollama is streaming a reply — something a plain blocking input() loop
     doesn't need to worry about. Ctrl+C sets a threading.Event that
     ollama_client.chat() polls between reads; a tool confirmation crosses
     back the other way via a second Event, since the worker thread blocks
     waiting for the answer the same way input() used to.

The centered "/" menu, the rolling-panda warm-up spinner, the separator
rule between turns, and the Lazzy reply label all live here rather than in
ui.py's print-based helpers, but reuse the exact same fragment builders
(ui.*_fragment / ui.*_fragments) so colors and wording never drift between
the two UIs.
"""

from __future__ import annotations

import asyncio
import html
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor

from prompt_toolkit.application import Application, get_app
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import HTML, to_formatted_text
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.key_binding.bindings.scroll import scroll_page_down, scroll_page_up
from prompt_toolkit.key_binding.defaults import load_key_bindings
from prompt_toolkit.layout import ConditionalContainer, Float, FloatContainer, HSplit, Layout, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.margins import ScrollbarMargin
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.lexers import Lexer
from prompt_toolkit.widgets import Frame

from localcoder import ui
from localcoder.browse import browse_entries
from localcoder.context import list_context_sets
from localcoder.index_store import get_active_index_name, index_stats, list_indexes
from localcoder.menu import compute_menu_items
from localcoder.ollama_client import OllamaError, force_close, list_models, warm_up
from localcoder.repl import App, OutputSink, _dispatch_command, _relaunch
from localcoder.roles import list_roles
from localcoder.sessions import list_sessions
from localcoder.skills import list_skills

_MENU_MAX_ROWS = 20
_SPINNER_INTERVAL = 0.12


def _scroll_one_line_up(event) -> None:
    """Like prompt_toolkit's own scroll_one_line_up, but safe for a wrapped
    transcript. The stock version (see its TODO: "not entirely correct yet,
    in case of line wrapping") advances the cursor by document lines while
    reasoning about *screen* rows, so on a long wrapped reply it can jump the
    cursor further than the one line of scroll it just applied — the next
    render's keep-cursor-visible clamp then snaps vertical_scroll back down,
    which reads as the transcript refusing to scroll past a certain point.
    Anchoring on first_visible_line() (a document-line index, exactly what
    vertical_scroll itself is) instead of the cursor sidesteps the mismatch —
    the same trick scroll_page_up/scroll_page_down already use below.
    """
    w = event.app.layout.current_window
    b = event.app.current_buffer
    if w and w.render_info:
        line_index = max(0, w.render_info.first_visible_line() - 1)
        w.vertical_scroll = line_index
        b.cursor_position = b.document.translate_row_col_to_index(line_index, 0)


def _scroll_one_line_down(event) -> None:
    """Down-scroll counterpart to _scroll_one_line_up — see its docstring."""
    w = event.app.layout.current_window
    b = event.app.current_buffer
    if w and w.render_info:
        line_index = w.render_info.first_visible_line() + 1
        w.vertical_scroll = line_index
        b.cursor_position = b.document.translate_row_col_to_index(line_index, 0)


def _split_into_lines(fragments):
    """[(style, text), ...] with embedded "\\n"s -> one styled-fragment list
    per visual line, newlines stripped. Used to turn a blob of HTML-derived
    formatted text into the per-line lookup table `_TranscriptLexer` needs.
    """
    lines: list[list[tuple[str, str]]] = [[]]
    for style, text in fragments:
        parts = text.split("\n")
        for i, part in enumerate(parts):
            if i > 0:
                lines.append([])
            if part:
                lines[-1].append((style, part))
    return lines


class _TranscriptLexer(Lexer):
    """Feeds prompt_toolkit's per-line styling from ScreenApp's already-parsed
    transcript instead of re-lexing the buffer's plain text — the read-only
    Buffer backing the transcript pane exists purely so we can drive scrolling
    with prompt_toolkit's own well-tested cursor-follows-scroll machinery
    (`buffer.cursor_position = len(text)` for auto-scroll-to-bottom, and
    `scroll_page_up`/`scroll_page_down` for PageUp/PageDown). A plain
    FormattedTextControl was tried first, but a manually-set
    `window.vertical_scroll` was found not to survive re-renders reliably for
    a cursor-less control — hence this Buffer+Lexer approach instead.
    """

    def __init__(self, screen: "ScreenApp"):
        self.screen = screen

    def lex_document(self, document):
        lines = self.screen._transcript_lines

        def get_line(lineno: int):
            if 0 <= lineno < len(lines):
                return lines[lineno]
            return []

        return get_line


class BufferSink(OutputSink):
    """OutputSink that appends into the full-screen transcript instead of
    printing to real stdout. Every method may be called from the worker
    thread that runs a turn — `screen.append_raw` marshals the actual
    mutation back onto the UI's asyncio loop, so nothing here touches shared
    state directly from another thread.
    """

    def __init__(self, screen: "ScreenApp"):
        self.screen = screen
        self._reply_raw = ""  # unescaped text of the reply currently streaming in
        self._reply_active = False  # True between assistant_label() and the reply's end

    def assistant_label(self) -> None:
        self._reply_raw = ""
        self._reply_active = True
        self.screen.append_raw(f"\n{ui.assistant_label_fragment()}")

    def token(self, piece: str) -> None:
        # The whole reply-so-far is re-rendered (not just this piece glued
        # on) because code-fence styling can only be decided once a ``` has
        # actually arrived — a fence, or the language hint after it, can
        # each land split across more than one token. replace_last swaps
        # the whole transcript entry (label + reply) in one go, still on
        # the one line a streamed reply is supposed to occupy — see
        # ui.render_streamed_reply and replace_last's docstring.
        self._reply_raw += piece
        rendered = f"\n{ui.assistant_label_fragment()}{ui.render_streamed_reply(self._reply_raw)}"
        if self._reply_active:
            self.screen.replace_last(rendered)
        else:
            # Defensive: a token arrived with no assistant_label() call first
            # (shouldn't happen in a real turn — see run_turn — but a caller
            # that skips it must not silently clobber whatever the last
            # transcript entry happened to be).
            self._reply_active = True
            self.screen.append_raw(rendered)

    def newline(self) -> None:
        self._reply_active = False
        self.screen.append_raw("\n")

    def tool_call(self, name: str, args: dict) -> None:
        self.screen.append_raw(f"\n{ui.tool_call_fragment(name, args)}")

    def verbose_stats(self, meta: dict) -> None:
        for fragment in ui.verbose_stats_fragments(meta):
            self.screen.append_raw(fragment + "\n")

    def info(self, text: str) -> None:
        self.screen.append_raw(ui.info_fragment(text))

    def ok(self, text: str) -> None:
        self.screen.append_raw(ui.ok_fragment(text))

    def warn(self, text: str) -> None:
        self.screen.append_raw(ui.warn_fragment(text))

    def err(self, text: str) -> None:
        self.screen.append_raw(ui.err_fragment(text))


class ScreenApp:
    def __init__(self, app: App, input=None, output=None):  # noqa: A002 — input/output: only ever set by tests, to inject a headless pipe
        self.app = app
        self.app.out = BufferSink(self)

        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="localcoder-turn")
        # Set once the app's own event loop is actually running (in
        # _on_pre_run) — append_raw needs this exact loop object, not
        # whatever asyncio.get_running_loop() returns at call time, since it
        # gets called from the worker thread just as often as from the UI
        # thread. See append_raw's docstring for why that distinction matters.
        self._loop: asyncio.AbstractEventLoop | None = None
        self.busy = False
        self.cancel_event: threading.Event | None = None
        self.response_holder: dict | None = None
        self.confirm_pending: dict | None = None
        self.warming_up = False
        self.spinner_tick = 0
        self.menu_index = 0
        self._menu_cache: tuple[str, list] = ("\0", [])  # text -> computed items, avoids re-hitting Ollama every keystroke
        self.restart_pending = False  # set by /restart; run() re-execs the process after a clean TUI shutdown

        # _transcript_parts: raw HTML-tagged fragment strings, one per
        # append_raw() call that started a new line (continuation appends —
        # streamed tokens — get merged into the last entry instead of adding
        # their own). _transcript_lines is the same content already parsed
        # and split per visual line — what the Buffer's Lexer actually reads;
        # kept in sync by _sync_transcript() every time _transcript_parts
        # changes. See _TranscriptLexer's docstring for why a Buffer backs
        # this pane instead of a plain FormattedTextControl.
        self._transcript_parts: list[str] = []
        self._transcript_lines: list[list[tuple[str, str]]] = [[]]
        self.transcript_buffer = Buffer(document=Document(""), read_only=True)

        self.input_buffer = Buffer(multiline=False)
        self.input_buffer.on_text_changed += lambda _buf: setattr(self, "menu_index", 0)

        self.transcript_window = Window(
            content=BufferControl(buffer=self.transcript_buffer, lexer=_TranscriptLexer(self), focusable=True),
            wrap_lines=True,
            always_hide_cursor=True,
            allow_scroll_beyond_bottom=True,
            # A thin vertical bar tracking how far through the transcript the
            # visible window currently sits — the "where am I" indicator that
            # PageUp/PageDown/↑/↓ scrolling otherwise gives no visual feedback
            # for. display_arrows=False: there's no room for ▲/▼ glyphs in a
            # single-column margin without eating into the bar itself.
            right_margins=[ScrollbarMargin(display_arrows=False)],
        )
        self.status_window = Window(height=1, content=FormattedTextControl(text=self._status_fragments))
        # A bordered popup (prompt_toolkit's Frame widget) with a filter-style
        # title and a key-hint footer underneath — matches the reference
        # screenshot's boxed "/help"/"Settings" look. It's a Float (see root
        # below), not a slot in the main HSplit: docked inline, it always
        # ended up hugging the bottom of the screen right above the input
        # box, with only a handful of rows to work with — a real floating
        # popup instead sits centered over the transcript, so it gets to be
        # as tall as it needs without being squeezed by the rest of the layout.
        self.menu_popup = ConditionalContainer(
            HSplit(
                [
                    Frame(
                        Window(
                            content=FormattedTextControl(text=self._menu_fragments),
                            # +1 for the item window, +2 more for the ▲/▼
                            # overflow indicators so they never squeeze items out.
                            height=Dimension(min=0, max=_MENU_MAX_ROWS + 2),
                            wrap_lines=True,  # long descriptions wrap instead of being cut off mid-word
                        ),
                        title=self._menu_title,
                        style="class:menu.frame",
                    ),
                    Window(height=1, content=FormattedTextControl(text=self._menu_hint_fragments)),
                ]
            ),
            filter=Condition(lambda: bool(self.current_menu_items())),
        )
        self.input_window = Window(
            height=Dimension(min=3, max=8),  # a few lines of visible room, not a single cramped line
            content=BufferControl(buffer=self.input_buffer, focusable=True),
            wrap_lines=True,
            get_line_prefix=lambda lineno, wrap_count: self._input_prompt(),
        )

        body = HSplit(
            [
                self.transcript_window,
                Window(height=1, char="─", style="class:separator"),
                self.status_window,
                Window(height=1, char="─", style="class:separator"),
                self.input_window,
            ]
        )
        # No top/bottom/left/right/xcursor/ycursor given -> prompt_toolkit
        # centers the float on both axes using its own preferred size — see
        # FloatContainer._draw_float. That's the actual fix for "le menu doit
        # etre au centre de l'ecran": vertical centering needs a float, an
        # inline HSplit slot can only ever be positioned by what's above and
        # below it.
        root = FloatContainer(content=body, floats=[Float(content=self.menu_popup, width=self._menu_box_width)])

        kb = self._build_key_bindings()
        # mouse_support is deliberately OFF. It was briefly turned on to get
        # real mouse-wheel scrolling (prompt_toolkit's built-in handling
        # turned out to cooperate fine with the cursor-driven auto-scroll —
        # see _sync_transcript), but that meant the app owns every click/
        # drag, which breaks the terminal's own text selection — there's a
        # modifier-key override for that on most terminals (Option on macOS
        # Terminal/iTerm2, Shift elsewhere), but in practice that wasn't
        # reliable enough across terminals to be worth losing plain,
        # always-works copy/paste over. PageUp/PageDown (bound below) are
        # the scroll story instead — keyboard-only, like the rest of the UI.
        self.application: Application = Application(
            layout=Layout(root, focused_element=self.input_window),
            key_bindings=merge_key_bindings([load_key_bindings(), kb]),
            style=ui.THEME,
            full_screen=True,
            mouse_support=False,
            input=input,
            output=output,
        )

    # ---- transcript -------------------------------------------------------
    def _threadsafe_mutate(self, mutator) -> None:
        """Runs `mutator` (a no-arg callable that edits self._transcript_parts
        in place), then re-syncs and repaints — always on the UI's own event
        loop, never directly on whatever thread called in. Shared by
        append_raw and replace_last; see append_raw's docstring for why this
        has to always go through call_soon_threadsafe rather than branching
        on the calling thread.
        """

        def _do():
            mutator()
            self._sync_transcript()
            try:
                get_app().invalidate()
            except Exception:
                pass

        if self._loop is not None:
            self._loop.call_soon_threadsafe(_do)
        else:
            # No application loop running at all yet (e.g. a unit test poking
            # the BufferSink directly, app.run()/run_async() never called) —
            # nothing else could possibly be racing this, so apply inline.
            _do()

    def append_raw(self, fragment: str, continued: bool = False) -> None:
        """Safe to call from any thread. Appends one HTML-tagged fragment
        (see ui.py's *_fragment builders) to the transcript and scrolls to
        the bottom; the actual mutation always happens on the UI loop.

        `continued=True` glues this fragment onto the end of the previous one
        with no separator, so text keeps streaming onto one line the way it
        would in a real terminal. Every other call site gets a fragment that
        starts its own line — matching how each of them already embeds its
        own leading/trailing "\\n" (see e.g. assistant_label_fragment,
        tool_call_fragment).

        Deliberately keys off `self._loop` (captured once in _on_pre_run)
        rather than `asyncio.get_running_loop()` at call time: this method is
        called from the worker thread just as often as from the UI thread
        (every ok()/warn()/token() a slash command or a turn produces), and
        get_running_loop() only succeeds on the UI thread — on the worker
        thread it raises, which used to fall through to applying the mutation
        immediately and unsynchronized. That let a fast command's output
        actually land in the transcript *before* the "you> ..." echo of the
        command that triggered it (that echo, appended from the UI thread,
        only gets *scheduled*, not applied immediately, so a worker-thread
        append racing ahead of it would jump the queue) — always going
        through call_soon_threadsafe on the one real loop fixes both that
        reordering and the unsynchronized mutation itself.
        """

        def mutator():
            if continued and self._transcript_parts:
                self._transcript_parts[-1] += fragment
            else:
                self._transcript_parts.append(fragment)

        self._threadsafe_mutate(mutator)

    def replace_last(self, fragment: str) -> None:
        """Like append_raw, but replaces the entire last transcript entry
        instead of appending to it. Used to re-render a streamed reply from
        scratch on every token (see BufferSink.token) so code-fence styling —
        which can only be decided once a ``` has actually arrived, and can
        land split across more than one token — stays correct throughout the
        stream instead of only once the message is complete.
        """

        def mutator():
            if self._transcript_parts:
                self._transcript_parts[-1] = fragment
            else:
                self._transcript_parts.append(fragment)

        self._threadsafe_mutate(mutator)

    def _sync_transcript(self) -> None:
        """Re-derives _transcript_lines (what the Lexer reads) and the
        transcript Buffer's plain-text document (what drives scrolling) from
        _transcript_parts. Moving the cursor to the very end on every sync is
        what makes the pane auto-scroll to the bottom on new output — it
        leans on prompt_toolkit's own cursor-follows-scroll logic rather than
        poking `vertical_scroll` directly (see _TranscriptLexer's docstring
        for why that approach didn't work).
        """
        joined = "\n".join(self._transcript_parts)
        fragments = to_formatted_text(HTML(joined)) if joined else []
        self._transcript_lines = _split_into_lines(fragments)
        plain = "".join(text for _style, text in fragments)
        self.transcript_buffer.set_document(Document(plain, cursor_position=len(plain)), bypass_readonly=True)

    # ---- status / spinner ---------------------------------------------------
    def _status_fragments(self):
        if self.warming_up:
            return [("class:spinner", f"{ui.spinner_frame(self.spinner_tick)} warming up {self.app.config.model}...")]
        if self.busy:
            return [("class:spinner", f"{ui.spinner_frame(self.spinner_tick)} {ui.MASCOT_NAME} is working... (Ctrl+C to stop)")]
        return ui.bottom_toolbar(self.app.toolbar_state())

    async def _animate_spinner(self) -> None:
        while True:
            await asyncio.sleep(_SPINNER_INTERVAL)
            if self.warming_up or self.busy:
                self.spinner_tick += 1
                try:
                    get_app().invalidate()
                except Exception:
                    pass

    # ---- startup: seed the banner, then warm up with the spinner running --
    def _on_pre_run(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.application.create_background_task(self._animate_spinner())
        self.application.create_background_task(self._startup())

    async def _startup(self) -> None:
        for fragment in ui.banner_fragments(
            self.app.config,
            self.app.cwd,
            self.app.current_session_name,
            self.app.active_role,
            self.app.context_entries,
            index_stats(self.app.cwd, get_active_index_name(self.app.cwd)),
            True,
            skills=self.app.active_skills,
        ):
            self.append_raw(fragment)

        if self.app.config.warm_up:
            self.warming_up = True
            loop = asyncio.get_running_loop()
            try:
                await loop.run_in_executor(self.executor, warm_up, self.app.config.host, self.app.config.model)
                self.append_raw(ui.ok_fragment(f"[localcoder] {ui.MASCOT_NAME} is warmed up and ready."))
            except OllamaError as err:
                self.append_raw(ui.warn_fragment(f"[localcoder] Warm-up skipped: {self.app.format_error(err)}"))
            finally:
                self.warming_up = False
                get_app().invalidate()

    # ---- the "/" menu -------------------------------------------------------
    def _menu_lists(self) -> dict:
        a = self.app
        return {
            "roles": lambda: list_roles(a.cwd),
            "sessions": lambda: list_sessions(a.cwd),
            "models": lambda: list_models(a.config.host),
            "skills": lambda: list_skills(a.cwd),
            "files": lambda partial: browse_entries(a.cwd, partial),
            "context_set": lambda: list_context_sets(a.cwd),
            "index": lambda: [i["name"] for i in list_indexes(a.cwd)],
        }

    def current_menu_items(self) -> list:
        if self.busy or self.confirm_pending is not None or self.app.capture is not None:
            return []
        text = self.input_buffer.text
        cached_text, cached_items = self._menu_cache
        if text == cached_text:
            return cached_items
        items = compute_menu_items(text, self._menu_lists())
        self._menu_cache = (text, items)
        return items

    def _menu_box_width(self) -> int:
        """Width of the floating menu popup itself — the Float that centers
        it on screen (see __init__) needs a concrete number, and the content
        below is laid out left-aligned against this same width rather than
        centered line-by-line against the full terminal width (that only
        made sense back when the menu was a plain unbordered strip docked at
        a fixed spot, not a real popup box that's centered as a whole).
        """
        term_width = shutil.get_terminal_size((80, 24)).columns
        return max(40, min(term_width - 6, 96))

    def _menu_fragments(self):
        items = self.current_menu_items()
        if not items:
            return []
        current_idx = self.menu_index % len(items)
        # Scrolling window instead of a fixed first-page slice. With the old
        # `items[:N]`, ↑/↓ could move the selection past row N but rows N+1..
        # were never rendered — and the highlight compared against visible
        # positions 0..N-1 that the selection had left behind, so it vanished
        # once you travelled more than one screenful. The window now follows
        # menu_index, and the ▲/▼ lines say how much is still hidden on each
        # side, so every item is reachable *and* visible from the keyboard.
        start = min(
            max(0, current_idx - _MENU_MAX_ROWS // 2),
            max(0, len(items) - _MENU_MAX_ROWS),
        )
        visible = items[start:start + _MENU_MAX_ROWS]
        frags: list = []
        if start > 0:
            frags.append(("class:menu.desc", f"▲ {start} more above\n"))
        for idx, item in enumerate(visible):
            current = (start + idx) == current_idx
            if current:
                # Marks where prompt_toolkit's Window should scroll to keep
                # this row visible. Without it the Window has no idea which
                # of these lines is "the selection" — if the popup ever
                # renders shorter than this whole slice (a short terminal, or
                # a long description wrapping onto extra lines), it just
                # clips from the top instead of scrolling to the highlight.
                frags.append(("[SetCursorPosition]", ""))
            cmd_style = "class:menu.cmd.current" if current else "class:menu.cmd"
            desc_style = "class:menu.desc.current" if current else "class:menu.desc"
            if "  — " in item.display:
                cmd_part, desc_part = item.display.split("  — ", 1)
                cmd_part = f"{cmd_part}  "
                desc_part = f"— {desc_part}"
            else:
                cmd_part, desc_part = item.display, ""
            frags.append((cmd_style, cmd_part))
            if desc_part:
                frags.append((desc_style, desc_part))
            frags.append(("", "\n"))
        below = len(items) - (start + len(visible))
        if below > 0:
            frags.append(("class:menu.desc", f"▼ {below} more below\n"))
        return frags

    def _menu_title(self):
        # Whatever's typed so far, e.g. "/role use" — same idea as the
        # reference screenshot's "Filter:" line, just folded into the
        # border's title instead of a separate row.
        text = self.input_buffer.text.strip()
        return f" {text} " if text else " Commands "

    def _menu_hint_fragments(self):
        width = self._menu_box_width()
        hint = "↑/↓ Move  PgUp/PgDn Page  Tab Complete  Enter Select  Esc Clear"
        pad = max(0, (width - len(hint)) // 2)
        return [("class:menu.hint", " " * pad + hint)]

    # ---- input prompt prefix -------------------------------------------------
    def _input_prompt(self):
        if self.confirm_pending is not None:
            return [("class:warn", "confirm [y/N]> ")]
        if self.app.capture is not None:
            return [("class:prompt.user", f"{self.app.capture['kind']}> ")]
        if self.busy:
            return [("class:dim", "···> ")]
        return [("class:prompt.user", "you> ")]

    # ---- submitting a line --------------------------------------------------
    async def submit_line(self, text: str) -> None:
        stripped = text.strip()

        if self.confirm_pending is not None:
            self.append_raw(f"<dim>{html.escape(text)}</dim>")
            pending = self.confirm_pending
            self.confirm_pending = None
            pending["answer"] = stripped.lower() == "y"
            pending["event"].set()
            return

        if self.app.capture is not None:
            self.append_raw(f"<dim>{html.escape(text)}</dim>")
            self.app.feed_capture_line(text)
            return

        if not stripped:
            return

        if stripped == "/exit":
            get_app().exit()
            return

        if stripped == "/restart":
            # A clean relaunch = exit the TUI first (prompt_toolkit restores
            # the alternate screen and termios in run()'s cleanup), then
            # re-exec the process — see run() below and repl._relaunch.
            # Doing the exec here from inside the event loop would replace
            # the process image while the terminal is still in raw mode.
            self.app.autosave()
            self.restart_pending = True
            get_app().exit()
            return

        if stripped.startswith("/"):
            self.append_raw(f"\n<prompt.user>you&gt;</prompt.user> {html.escape(text)}")

            def work(cancel_event, on_response):
                _dispatch_command(self.app, stripped, cancel_event=cancel_event, on_response=on_response)

            await self._run_busy(work)
            return

        # A genuine chat message starts a new turn — the separator marks
        # that boundary in the scrollback.
        self.append_raw(f"\n{ui.user_separator_fragment()}\n<prompt.user>you&gt;</prompt.user> {html.escape(text)}")
        self.app.apply_at_mentions(stripped)
        self.app.conversation.append({"role": "user", "content": stripped})
        await self._run_turn_async()

    async def _run_turn_async(self) -> None:
        loop = asyncio.get_running_loop()

        def confirm_fn(question: str) -> bool:
            event = threading.Event()
            pending = {"question": question, "event": event, "answer": False}

            def _show():
                self.confirm_pending = pending
                self.append_raw(f"<warn>{html.escape(question)}</warn>")
                get_app().invalidate()

            loop.call_soon_threadsafe(_show)
            event.wait()
            return pending["answer"]

        def work(cancel_event, on_response):
            self.app.run_turn(confirm_fn, cancel_event=cancel_event, on_response=on_response)

        await self._run_busy(work)

    async def _run_busy(self, work) -> None:
        """Runs `work(cancel_event, on_response)` on the single-worker
        executor while keeping the UI responsive (spinner, Ctrl+C) — shared
        by chat turns and slash commands. Slash commands used to be
        dispatched inline on the event-loop thread, which meant a slow one
        (/summary makes its own Ollama call) froze the whole UI — no spinner,
        no Ctrl+C — until it finished; routing them through here too fixes
        that.

        `on_response` receives the open HTTP response as soon as `chat()`
        connects — stashed in `self.response_holder` so a later Ctrl+C
        (running on the UI thread) can force-close it. That's what actually
        interrupts a blocked read; `cancel_event` alone only catches the
        (much smaller) window between two already-arrived lines. See
        ollama_client.chat()'s docstring for the full rationale.
        """
        self.busy = True
        self.cancel_event = threading.Event()
        cancel_event = self.cancel_event
        holder = {"resp": None}
        self.response_holder = holder

        def on_response(resp) -> None:
            holder["resp"] = resp

        def _work():
            work(cancel_event, on_response)

        try:
            await asyncio.get_running_loop().run_in_executor(self.executor, _work)
        except Exception as err:  # noqa: BLE001 — never let one bad turn/command kill the UI
            self.append_raw(ui.err_fragment(self.app.format_error(err)))
        finally:
            self.busy = False
            self.cancel_event = None
            self.response_holder = None
            self.app.autosave()
            try:
                get_app().invalidate()
            except Exception:
                pass

    # ---- key bindings ---------------------------------------------------------
    def _build_key_bindings(self) -> KeyBindings:
        kb = KeyBindings()
        screen = self

        @kb.add("enter")
        async def _enter(event):
            buf = event.current_buffer
            text = buf.text

            if screen.confirm_pending is not None or screen.app.capture is not None:
                buf.reset()
                await screen.submit_line(text)
                return

            if screen.busy:
                return

            items = screen.current_menu_items()
            if items:
                item = items[screen.menu_index % len(items)]
                buf.text = item.value
                buf.cursor_position = len(item.value)
                if not item.submit:
                    return
                text = item.value

            buf.reset()
            await screen.submit_line(text)

        @kb.add("tab")
        def _tab(event):
            items = screen.current_menu_items()
            if not items:
                return
            item = items[screen.menu_index % len(items)]
            buf = event.current_buffer
            buf.text = item.value
            buf.cursor_position = len(item.value)

        @kb.add("up")
        def _up(event):
            items = screen.current_menu_items()
            if items:
                screen.menu_index = (screen.menu_index - 1) % len(items)
                return
            # Menu closed and the input is a single line, so there's nothing
            # for auto_up() to do in the buffer itself — repurpose ↑ to walk
            # back up the transcript, one line at a time, the same
            # focus-hop-then-restore trick PageUp uses below.
            layout = event.app.layout
            layout.focus(screen.transcript_window)
            _scroll_one_line_up(event)
            layout.focus(screen.input_window)

        @kb.add("down")
        def _down(event):
            items = screen.current_menu_items()
            if items:
                screen.menu_index = (screen.menu_index + 1) % len(items)
                return
            layout = event.app.layout
            layout.focus(screen.transcript_window)
            _scroll_one_line_down(event)
            layout.focus(screen.input_window)

        @kb.add("escape")
        def _escape(event):
            event.current_buffer.reset()

        @kb.add("pageup")
        def _pageup(event):
            items = screen.current_menu_items()
            if items:
                # The menu is open: page the selection by one windowful so a
                # 30-entry command list stays navigable without 30 key taps.
                screen.menu_index = (screen.menu_index - _MENU_MAX_ROWS) % len(items)
                return
            # scroll_page_up/down expect to act on the currently focused
            # window/buffer, so we hop focus onto the transcript just long
            # enough to run the (ready-made, well-tested) scroll logic, then
            # hop straight back — the input box never visibly loses focus.
            layout = event.app.layout
            layout.focus(screen.transcript_window)
            scroll_page_up(event)
            layout.focus(screen.input_window)

        @kb.add("pagedown")
        def _pagedown(event):
            items = screen.current_menu_items()
            if items:
                # The menu is open: page the selection by one windowful so a
                # 30-entry command list stays navigable without 30 key taps.
                screen.menu_index = (screen.menu_index + _MENU_MAX_ROWS) % len(items)
                return
            layout = event.app.layout
            layout.focus(screen.transcript_window)
            scroll_page_down(event)
            layout.focus(screen.input_window)

        @kb.add("c-p")
        def _ctrl_p(event):
            screen.app.toggle_plan_mode()
            get_app().invalidate()

        @kb.add("c-c")
        def _ctrl_c(event):
            if screen.busy and screen.cancel_event is not None:
                screen.cancel_event.set()
                # Setting the event alone only stops things between two
                # already-arrived lines — force_close() is what actually
                # wakes up a call currently sitting in a blocking read
                # (mid prompt-eval, or a stall between tokens).
                if screen.response_holder is not None and screen.response_holder.get("resp") is not None:
                    force_close(screen.response_holder["resp"])
                if screen.confirm_pending is not None:
                    pending = screen.confirm_pending
                    screen.confirm_pending = None
                    pending["answer"] = False
                    pending["event"].set()
                return
            if not screen.busy:
                event.app.exit()

        @kb.add("c-d")
        def _ctrl_d(event):
            if not screen.busy and not event.current_buffer.text:
                event.app.exit()

        return kb

    def run(self) -> None:
        try:
            self.application.run(pre_run=self._on_pre_run)
        finally:
            self.executor.shutdown(wait=False)
        if self.restart_pending:
            # At this point prompt_toolkit has fully restored the terminal
            # (alternate screen left, cooked mode back) — safe to re-exec.
            _relaunch()


def run_fullscreen(app: App) -> None:
    ui.clear_screen()
    ScreenApp(app).run()
