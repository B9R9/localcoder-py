"""Unit coverage for the parts of the full-screen UI that don't need a real
terminal: menu computation/caching, the input-prompt state machine, the
BufferSink → transcript pipeline, and submit_line's command/confirm/capture
routing. The actual rendered layout (centered popup, spinner, pinned input)
can only be driven with a real pty — see the project's existing note about
prompt_toolkit's interactive pieces not being covered by the automated
suite; this file covers everything below that layer instead.
"""

from __future__ import annotations

import asyncio
import threading

import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.layout.margins import ScrollbarMargin
from prompt_toolkit.output import DummyOutput

from localcoder.fullscreen import _MENU_MAX_ROWS, ScreenApp
from localcoder.repl import App


def _make_app(tmp_path, monkeypatch, argv=None):
    monkeypatch.chdir(tmp_path)
    return App((argv or []) + ["--no-warm-up"])


def test_screen_app_constructs_without_a_real_terminal(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)
    screen = ScreenApp(app)
    assert screen.application is not None
    assert app.out is screen.app.out  # BufferSink wired in


def test_menu_hidden_while_busy_confirming_or_capturing(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)
    screen = ScreenApp(app)
    screen.input_buffer.text = "/rol"
    assert screen.current_menu_items() != []

    screen.busy = True
    assert screen.current_menu_items() == []
    screen.busy = False

    screen.confirm_pending = {"question": "?", "event": None, "answer": False}
    assert screen.current_menu_items() == []
    screen.confirm_pending = None

    app.capture = {"kind": "role", "name": "x", "lines": []}
    assert screen.current_menu_items() == []


def test_menu_items_are_cached_per_buffer_text(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)
    screen = ScreenApp(app)

    calls = {"n": 0}

    def fake_list_roles(cwd):
        calls["n"] += 1
        return ["tdd"]

    monkeypatch.setattr("localcoder.fullscreen.list_roles", fake_list_roles)

    screen.input_buffer.text = "/role use "
    screen.current_menu_items()
    screen.current_menu_items()
    assert calls["n"] == 1  # second call reused the cache, same text

    screen.input_buffer.text = "/role use t"
    screen.current_menu_items()
    assert calls["n"] == 2  # text changed — recomputed


def test_menu_fragments_show_descriptions(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)
    screen = ScreenApp(app)
    screen.input_buffer.text = "/stats"

    frags = screen._menu_fragments()
    text = "".join(t for _, t in frags)
    assert "/stats" in text
    assert "Cumulative session stats" in text


def test_menu_fragments_are_left_aligned_inside_the_popup(tmp_path, monkeypatch):
    # Screen-centering is now the floating popup's job (see
    # _menu_box_width/the Float in __init__) — content inside it is just
    # left-aligned, not padded line-by-line against the full terminal width
    # like the old unbordered, docked-at-the-bottom menu was.
    app = _make_app(tmp_path, monkeypatch)
    screen = ScreenApp(app)
    screen.input_buffer.text = "/exit"

    frags = screen._menu_fragments()
    cmd_fragment = next(t for style, t in frags if "menu.cmd" in style)
    assert cmd_fragment.startswith("/exit")


def _assert_selected(screen, idx):
    """Asserts that the item at `idx` is the one rendered with the current
    highlight: its command text is visible and exactly one row carries the
    `menu.cmd.current` style."""
    items = screen.current_menu_items()
    cmd = items[idx].display.split("  — ", 1)[0]
    frags = screen._menu_fragments()
    highlighted = [t for s, t in frags if "menu.cmd.current" in s]
    assert len(highlighted) == 1, f"index {idx}: expected exactly one highlighted row"
    assert highlighted[0].strip() == cmd, f"index {idx}: wrong row highlighted"


def test_menu_window_overflow_indicators(tmp_path, monkeypatch):
    """The scrolling window must say how much is left on each side, and only
    on the side that actually has hidden items — no phantom arrows."""
    app = _make_app(tmp_path, monkeypatch)
    screen = ScreenApp(app)
    screen.input_buffer.text = "/"  # matches every top-level command
    items = screen.current_menu_items()
    assert len(items) > _MENU_MAX_ROWS

    screen.menu_index = 0  # top of the list: only items below are hidden
    text = "".join(t for _, t in screen._menu_fragments())
    assert "more below" in text
    assert "more above" not in text

    screen.menu_index = _MENU_MAX_ROWS  # mid-list: both sides hidden
    text = "".join(t for _, t in screen._menu_fragments())
    assert "more above" in text
    assert "more below" in text

    screen.menu_index = len(items) - 1  # bottom: only items above are hidden
    text = "".join(t for _, t in screen._menu_fragments())
    assert "more above" in text
    assert "more below" not in text
    _assert_selected(screen, len(items) - 1)


def test_menu_selection_is_always_rendered_and_highlighted(tmp_path, monkeypatch):
    """Reported regression: the centered \"/\" menu pinned rendering to the
    first _MENU_MAX_ROWS rows, so travelling past row 10 hid the remaining
    commands entirely and the highlight vanished (it only matched visible
    positions 0..N-1). Every selectable index must now stay on screen as the
    single highlighted row."""
    app = _make_app(tmp_path, monkeypatch)
    screen = ScreenApp(app)
    screen.input_buffer.text = "/"
    items = screen.current_menu_items()
    assert len(items) > _MENU_MAX_ROWS

    for idx in range(len(items)):
        screen.menu_index = idx
        _assert_selected(screen, idx)

    # menu_index is used modulo len(items), so out-of-range is fine too.
    screen.menu_index = len(items) + 7
    _assert_selected(screen, 7)


def test_page_keys_page_through_the_menu(tmp_path, monkeypatch):
    """When the \"/\" menu is open, PageUp/PageDown move the selection by a
    whole windowful (instead of scrolling the transcript), so the last of
    ~30 commands is reachable in a handful of keystrokes — and focus stays on
    the input box the whole time."""
    app = _make_app(tmp_path, monkeypatch)

    async def go():
        with create_pipe_input() as pipe_input:
            screen = ScreenApp(app, input=pipe_input, output=DummyOutput())
            screen.input_buffer.text = "/"
            screen._loop = asyncio.get_running_loop()  # normally set by _on_pre_run
            items = screen.current_menu_items()
            assert len(items) > _MENU_MAX_ROWS

            task = asyncio.ensure_future(screen.application.run_async())
            try:
                await asyncio.sleep(0.05)
                for _ in range(3):
                    pipe_input.send_text("\x1b[6~")  # PageDown
                    await asyncio.sleep(0.05)
                _assert_selected(screen, (3 * _MENU_MAX_ROWS) % len(items))
                assert screen.application.layout.current_window is screen.input_window

                for _ in range(2):
                    pipe_input.send_text("\x1b[5~")  # PageUp
                    await asyncio.sleep(0.05)
                _assert_selected(screen, _MENU_MAX_ROWS)
            finally:
                screen.application.exit()
                await task

    asyncio.run(go())


def test_menu_offers_restart(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)
    screen = ScreenApp(app)
    screen.input_buffer.text = "/rest"
    items = screen.current_menu_items()
    assert [i.value for i in items] == ["/restart"]
    assert items[0].submit is True


def test_restart_exits_the_app_and_requests_a_relaunch(tmp_path, monkeypatch):
    """Typing /restart in the full-screen UI must not exec mid-loop (the
    terminal would be left in raw/alternate mode) — instead it autosaves,
    asks prompt_toolkit to shut down cleanly, and flags run() to re-exec the
    process once the terminal has been restored."""
    app = _make_app(tmp_path, monkeypatch)

    async def go():
        with create_pipe_input() as pipe_input:
            screen = ScreenApp(app, input=pipe_input, output=DummyOutput())
            screen._loop = asyncio.get_running_loop()  # normally set by _on_pre_run
            run_future = asyncio.ensure_future(screen.application.run_async())
            try:
                await asyncio.sleep(0.05)
                await screen.submit_line("/restart")
                await asyncio.wait_for(asyncio.shield(run_future), timeout=2.0)
                assert screen.restart_pending is True
                assert run_future.done()  # the app actually exited
            finally:
                if not run_future.done():
                    screen.application.exit()
                    await run_future

    asyncio.run(go())


def test_menu_popup_is_a_float_centered_on_screen(tmp_path, monkeypatch):
    # This is the actual fix for "le menu doit etre au centre de l'ecran":
    # an inline HSplit slot can only ever sit wherever the surrounding rows
    # push it (in practice, hugging the bottom edge just above the input
    # box) — real vertical centering needs a Float, which prompt_toolkit
    # centers on both axes when given no top/bottom/left/right/cursor
    # anchor (see FloatContainer._draw_float).
    from prompt_toolkit.layout import Float, FloatContainer

    app = _make_app(tmp_path, monkeypatch)
    screen = ScreenApp(app)

    root = screen.application.layout.container
    assert isinstance(root, FloatContainer)
    assert len(root.floats) == 1
    popup_float = root.floats[0]
    assert isinstance(popup_float, Float)
    assert popup_float.content is screen.menu_popup
    assert popup_float.top is None and popup_float.bottom is None
    assert popup_float.left is None and popup_float.right is None
    assert not popup_float.xcursor and not popup_float.ycursor


def test_input_prompt_reflects_state(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)
    screen = ScreenApp(app)

    assert screen._input_prompt() == [("class:prompt.user", "you> ")]

    screen.busy = True
    assert screen._input_prompt()[0][1] == "···> "
    screen.busy = False

    app.capture = {"kind": "skill", "name": "x", "lines": []}
    assert screen._input_prompt() == [("class:prompt.user", "skill> ")]
    app.capture = None

    screen.confirm_pending = {"question": "?", "event": None, "answer": False}
    assert screen._input_prompt()[0][1].startswith("confirm")


def test_buffer_sink_appends_are_visible_without_a_running_loop(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)
    screen = ScreenApp(app)

    app.out.ok("saved it")
    app.out.assistant_label()  # real turns always call this before token()
    app.out.token("hel")
    app.out.token("lo")

    joined = "\n".join(screen._transcript_parts)
    assert "saved it" in joined
    assert "hel" in joined and "lo" in joined


def test_submit_line_dispatches_a_slash_command(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)
    screen = ScreenApp(app)

    async def go():
        await screen.submit_line("/stats")
        await asyncio.sleep(0)  # let the scheduled transcript append flush

    asyncio.run(go())
    joined = "\n".join(screen._transcript_parts)
    assert "you&gt;" in joined and "/stats" in joined
    assert "[stats] model:" in joined


def test_submit_line_answers_a_pending_confirmation(tmp_path, monkeypatch):
    import threading

    app = _make_app(tmp_path, monkeypatch)
    screen = ScreenApp(app)
    event = threading.Event()
    pending = {"question": "Approve?", "event": event, "answer": None}
    screen.confirm_pending = pending

    async def go():
        await screen.submit_line("y")
        await asyncio.sleep(0)

    asyncio.run(go())
    assert pending["answer"] is True
    assert event.is_set()
    assert screen.confirm_pending is None


def test_token_continuations_do_not_insert_newlines_between_pieces(tmp_path, monkeypatch):
    """Regression guard: streamed reply tokens must land on one line, not one
    per token — see append_raw's `continued` parameter.
    """
    app = _make_app(tmp_path, monkeypatch)
    screen = ScreenApp(app)

    app.out.assistant_label()
    for piece in ["Hel", "lo", " ", "world"]:
        app.out.token(piece)

    assert screen._transcript_lines[-1] is screen._transcript_lines[-1]  # sanity: line exists
    text = screen.transcript_buffer.text
    assert "Hello world" in text
    assert "Hel\nlo" not in text


def test_streamed_code_fence_is_styled_even_when_split_across_tokens(tmp_path, monkeypatch):
    """"qunad tu code est affiche on le fomat pour une meilleure visibilite"
    — a fenced code block must render with the distinct `code` style even
    though it's typed to token() piece by piece (the ``` fence itself often
    lands split across more than one token, since streaming has no notion
    of markdown structure).
    """
    app = _make_app(tmp_path, monkeypatch)
    screen = ScreenApp(app)

    app.out.assistant_label()
    for piece in ["Use `", "`", "add(a, b)", "`", "` like:\n``", "`py", "thon\ndef add", "(a, b):\n", "    return a + b\n``", "`\ndone"]:
        app.out.token(piece)

    fragments = [f for line in screen._transcript_lines for f in line]
    assert any(style == "class:code" and "def add" in text for style, text in fragments)
    assert any(style == "class:code.inline" and text == "add(a, b)" for style, text in fragments)
    # The ```python language hint itself must not show up in the rendered code.
    assert not any("python" in text for _style, text in fragments)


def test_transcript_lines_are_kept_in_sync_with_the_buffer_for_the_lexer(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)
    screen = ScreenApp(app)

    app.out.ok("first")
    app.out.warn("second")

    lines = screen._transcript_lines
    plain_lines = ["".join(t for _s, t in line) for line in lines]
    assert plain_lines == screen.transcript_buffer.text.split("\n")
    assert "first" in plain_lines[0]
    assert "second" in plain_lines[1]


def test_transcript_auto_scrolls_to_bottom_and_pageup_pagedown_scroll(tmp_path, monkeypatch):
    """End-to-end validation of the Buffer+Lexer transcript rewrite: new
    output should always land visible at the bottom, and PageUp/PageDown
    should actually move the visible window — the two things a plain
    FormattedTextControl with a manually-poked `vertical_scroll` was found
    not to do reliably.
    """
    app = _make_app(tmp_path, monkeypatch)

    async def go():
        with create_pipe_input() as pipe_input:
            screen = ScreenApp(app, input=pipe_input, output=DummyOutput())
            screen._loop = asyncio.get_running_loop()  # normally set by _on_pre_run

            task = asyncio.ensure_future(screen.application.run_async())
            try:
                await asyncio.sleep(0.05)

                for i in range(100):
                    screen.append_raw(f"line {i}")
                await asyncio.sleep(0.05)

                render_info = screen.transcript_window.render_info
                assert render_info is not None
                assert render_info.last_visible_line() == 99  # scrolled to the newest line

                pipe_input.send_text("\x1b[5~")  # PageUp
                await asyncio.sleep(0.1)
                after_pageup = screen.transcript_window.render_info.first_visible_line()
                assert after_pageup < render_info.first_visible_line()  # actually scrolled up
                assert screen.application.layout.current_window is screen.input_window  # focus returned

                pipe_input.send_text("\x1b[6~")  # PageDown
                await asyncio.sleep(0.1)
                after_pagedown = screen.transcript_window.render_info.first_visible_line()
                assert after_pagedown > after_pageup  # scrolled back down
            finally:
                screen.application.exit()
                await task

    asyncio.run(go())


def test_up_down_arrows_scroll_transcript_one_line_when_menu_is_closed(tmp_path, monkeypatch):
    """With no "/" menu open, ↑/↓ used to be dead keys against a single-line
    input buffer (auto_up/auto_down have nothing to do there). They now walk
    the transcript one line at a time, the same focus-hop-then-restore
    pattern PageUp/PageDown already use.
    """
    app = _make_app(tmp_path, monkeypatch)

    async def go():
        with create_pipe_input() as pipe_input:
            screen = ScreenApp(app, input=pipe_input, output=DummyOutput())
            screen._loop = asyncio.get_running_loop()

            task = asyncio.ensure_future(screen.application.run_async())
            try:
                await asyncio.sleep(0.05)

                for i in range(100):
                    screen.append_raw(f"line {i}")
                await asyncio.sleep(0.05)

                bottom = screen.transcript_window.render_info.first_visible_line()

                pipe_input.send_text("\x1b[A")  # Up
                await asyncio.sleep(0.1)
                after_up = screen.transcript_window.render_info.first_visible_line()
                assert after_up < bottom  # scrolled up by one line
                assert screen.application.layout.current_window is screen.input_window  # focus returned
                assert screen.input_buffer.text == ""  # never touched the input line

                pipe_input.send_text("\x1b[B")  # Down
                await asyncio.sleep(0.1)
                after_down = screen.transcript_window.render_info.first_visible_line()
                assert after_down > after_up  # scrolled back down
            finally:
                screen.application.exit()
                await task

    asyncio.run(go())


def test_up_arrow_reaches_the_top_of_a_transcript_with_wrapped_lines(tmp_path, monkeypatch):
    """Regression test: prompt_toolkit's own scroll_one_line_up has a known
    bug (its docstring TODO says as much) with wrapped content — it advances
    the buffer cursor by document lines while reasoning about *screen* rows,
    so on long wrapped lines the next render's keep-cursor-visible clamp can
    snap vertical_scroll back down before it ever reaches the top. That read
    as "scrolling up stops partway and older history becomes unreachable".
    Repeatedly pressing ↑ against a transcript full of long (wrapping) lines
    must still walk all the way to document line 0.
    """
    app = _make_app(tmp_path, monkeypatch)

    async def go():
        with create_pipe_input() as pipe_input:
            screen = ScreenApp(app, input=pipe_input, output=DummyOutput())
            screen._loop = asyncio.get_running_loop()

            task = asyncio.ensure_future(screen.application.run_async())
            try:
                await asyncio.sleep(0.05)

                # 80-column output (DummyOutput's default) — each of these
                # wraps across several screen rows, unlike the short lines in
                # test_up_down_arrows_scroll_transcript_one_line_when_menu_is_closed.
                for i in range(30):
                    screen.append_raw(f"line {i} " + "x" * 200)
                await asyncio.sleep(0.05)

                for _ in range(200):
                    pipe_input.send_text("\x1b[A")  # Up
                    await asyncio.sleep(0.01)
                await asyncio.sleep(0.05)

                assert screen.transcript_window.render_info.first_visible_line() == 0
                assert screen.application.layout.current_window is screen.input_window
            finally:
                screen.application.exit()
                await task

    asyncio.run(go())


def test_transcript_window_has_a_scrollbar_margin(tmp_path, monkeypatch):
    """The right-hand scrollbar is the visual "where am I in the transcript"
    indicator for PageUp/PageDown/↑/↓ scrolling — assert it's actually wired
    onto the transcript window's margins.
    """
    app = _make_app(tmp_path, monkeypatch)
    screen = ScreenApp(app)
    assert any(isinstance(m, ScrollbarMargin) for m in screen.transcript_window.right_margins)


def test_mouse_support_is_off_so_native_copy_paste_keeps_working(tmp_path, monkeypatch):
    """mouse_support was briefly turned on to get wheel-scroll working (it
    does cooperate fine with the cursor-driven auto-scroll — see
    _sync_transcript), but owning every click/drag breaks the terminal's own
    text selection, which matters more than wheel-scroll since PageUp/
    PageDown (tested above) already cover scrolling from the keyboard. Kept
    off so copy/paste always works with no modifier key needed.
    """
    app = _make_app(tmp_path, monkeypatch)
    with create_pipe_input() as pipe_input:
        screen = ScreenApp(app, input=pipe_input, output=DummyOutput())
        assert not screen.application.mouse_support()


def test_ctrl_c_force_closes_the_in_flight_response(tmp_path, monkeypatch):
    """This is the fix for the reported "erreur timeout": Ctrl+C must not
    just flip cancel_event (that only catches the gap between two already-
    arrived lines) — it has to force_close() whatever response is currently
    stashed in response_holder, since that's what actually wakes up a
    blocked read.
    """
    app = _make_app(tmp_path, monkeypatch)

    calls = []
    monkeypatch.setattr("localcoder.fullscreen.force_close", lambda resp: calls.append(resp))

    async def go():
        with create_pipe_input() as pipe_input:
            screen = ScreenApp(app, input=pipe_input, output=DummyOutput())
            task = asyncio.ensure_future(screen.application.run_async())
            try:
                await asyncio.sleep(0.05)

                fake_resp = object()
                screen.busy = True
                screen.cancel_event = threading.Event()
                screen.response_holder = {"resp": fake_resp}

                pipe_input.send_text("\x03")  # Ctrl+C
                await asyncio.sleep(0.05)

                assert screen.cancel_event.is_set()
                assert calls == [fake_resp]
            finally:
                screen.busy = False
                screen.application.exit()
                await task

    asyncio.run(go())


def test_ctrl_p_toggles_plan_mode(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)

    async def go():
        with create_pipe_input() as pipe_input:
            screen = ScreenApp(app, input=pipe_input, output=DummyOutput())
            task = asyncio.ensure_future(screen.application.run_async())
            try:
                await asyncio.sleep(0.05)

                assert app.plan_mode is False
                pipe_input.send_text("\x10")  # Ctrl+P
                await asyncio.sleep(0.05)
                assert app.plan_mode is True

                pipe_input.send_text("\x10")
                await asyncio.sleep(0.05)
                assert app.plan_mode is False
            finally:
                screen.application.exit()
                await task

    asyncio.run(go())


def test_ctrl_l_toggles_loop_mode(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)

    async def go():
        with create_pipe_input() as pipe_input:
            screen = ScreenApp(app, input=pipe_input, output=DummyOutput())
            task = asyncio.ensure_future(screen.application.run_async())
            try:
                await asyncio.sleep(0.05)

                assert app.loop_mode is False
                pipe_input.send_text("\x0c")  # Ctrl+L
                await asyncio.sleep(0.05)
                assert app.loop_mode is True

                pipe_input.send_text("\x0c")
                await asyncio.sleep(0.05)
                assert app.loop_mode is False
            finally:
                screen.application.exit()
                await task

    asyncio.run(go())


def test_ctrl_g_toggles_graph_mode(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)

    async def go():
        with create_pipe_input() as pipe_input:
            screen = ScreenApp(app, input=pipe_input, output=DummyOutput())
            task = asyncio.ensure_future(screen.application.run_async())
            try:
                await asyncio.sleep(0.05)

                assert app.graph_mode is False
                pipe_input.send_text("\x07")  # Ctrl+G
                await asyncio.sleep(0.05)
                assert app.graph_mode is True

                pipe_input.send_text("\x07")
                await asyncio.sleep(0.05)
                assert app.graph_mode is False
            finally:
                screen.application.exit()
                await task

    asyncio.run(go())


def test_worker_thread_output_never_jumps_ahead_of_its_own_echoed_command(tmp_path, monkeypatch):
    """Real bug report: "[model] Now using ..." showed up in the transcript
    *before* the "you> /model use ..." line that triggered it. Slash commands
    run on the worker thread (_run_busy), and append_raw used to apply a
    worker-thread call immediately/unsynchronized instead of scheduling it on
    the UI loop the way a main-thread call did — so a fast command's own
    output could race ahead of the echoed command line. append_raw now always
    goes through the one real loop (self._loop, set in _on_pre_run)
    regardless of which thread calls it, which makes the ordering
    deterministic rather than a timing race.
    """
    app = _make_app(tmp_path, monkeypatch)

    async def go():
        with create_pipe_input() as pipe_input:
            screen = ScreenApp(app, input=pipe_input, output=DummyOutput())
            screen._loop = asyncio.get_running_loop()  # normally set by _on_pre_run

            task = asyncio.ensure_future(screen.application.run_async())
            try:
                await asyncio.sleep(0.05)
                await screen.submit_line("/verbose")
                await asyncio.sleep(0.1)

                text = screen.transcript_buffer.text
                echo_pos = text.index("/verbose")
                result_pos = text.index("[verbose]")
                assert echo_pos < result_pos
            finally:
                screen.application.exit()
                await task

    asyncio.run(go())


def test_tab_fills_context_add_with_the_first_browsed_entry(tmp_path, monkeypatch):
    """"pour ajouter un contexte... je dois pouvoir naviguer et utiliser tab
    pour avoir un aperçu des fichiers et folder" — Tab while typing
    /context add should fill in a real entry from the browse root, and
    descending into a directory should re-narrow the list to its contents.
    """
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hi')")
    (tmp_path / "src" / "lib").mkdir()
    (tmp_path / "src" / "lib" / "utils.py").write_text("x = 1")

    app = _make_app(tmp_path, monkeypatch)
    screen = ScreenApp(app)

    screen.input_buffer.text = "/context add "
    items = screen.current_menu_items()
    assert any(i.value.endswith("app.py") for i in items)
    assert any(i.value.endswith("lib/") for i in items)

    screen.input_buffer.text = next(i.value for i in items if i.value.endswith("lib/"))
    deeper = screen.current_menu_items()
    assert any(i.value.endswith("utils.py") for i in deeper)


def test_submit_line_feeds_a_pending_capture(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)
    screen = ScreenApp(app)
    app.begin_capture("role", "pair-programmer")

    async def go():
        await screen.submit_line("Think out loud.")
        await screen.submit_line(".")
        await asyncio.sleep(0)

    asyncio.run(go())
    assert app.capture is None
    saved = (tmp_path / "roles" / "pair-programmer.md").read_text()
    assert "Think out loud." in saved
