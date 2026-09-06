"""Unit tests for App's session-state toggles (verbose/debug/socratic) and
message building — the same App instance both repl.py's plain loop and
fullscreen.py's UI drive, so testing it directly here covers both paths at
once without going through a subprocess.
"""

from __future__ import annotations

import json
import types

from localcoder.repl import SOCRATIC_PROMPT, SYSTEM_PROMPT, App


def _make_app(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return App(["--no-warm-up"])


def test_toggle_debug_flips_state_and_reports_it(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)
    messages = []
    app.out.ok = messages.append

    assert app.debug is False
    app.toggle_debug()
    assert app.debug is True
    assert "on" in messages[-1]
    app.toggle_debug()
    assert app.debug is False
    assert "off" in messages[-1]


def test_toggle_socratic_flips_state_and_reports_it(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)
    messages = []
    app.out.ok = messages.append

    assert app.socratic is False
    app.toggle_socratic()
    assert app.socratic is True
    assert "on" in messages[-1]
    app.toggle_socratic()
    assert app.socratic is False
    assert "off" in messages[-1]


def test_toggle_plan_mode_flips_state_and_reports_it(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)
    messages = []
    app.out.ok = messages.append

    assert app.plan_mode is False
    app.toggle_plan_mode()
    assert app.plan_mode is True
    assert "on" in messages[-1]
    app.toggle_plan_mode()
    assert app.plan_mode is False
    assert "off" in messages[-1]


def test_plan_mode_blocks_write_tools_without_asking_for_confirmation(tmp_path, monkeypatch):
    """While plan mode is active, a write tool call must be refused outright
    — the model gets a normal tool error back — and the user is never
    prompted, unlike the ordinary confirm/decline path."""
    from localcoder import repl as repl_module

    app = _make_app(tmp_path, monkeypatch)
    app.plan_mode = True
    app.conversation.append({"role": "user", "content": "create a new file"})

    calls = iter(
        [
            {
                "content": "",
                "tool_calls": [
                    {"id": "1", "function": {"name": "write_file", "arguments": {"path": "x.txt", "content": "hi"}}}
                ],
                "done_meta": None,
            },
            {"content": "Can't do that — plan mode is on.", "tool_calls": None, "done_meta": None},
        ]
    )
    monkeypatch.setattr(repl_module, "chat", lambda **kwargs: next(calls))

    confirm_calls = []

    def confirm_fn(question):
        confirm_calls.append(question)
        return True

    app.run_turn(confirm_fn=confirm_fn)

    assert confirm_calls == []
    tool_result = next(m for m in app.conversation if m.get("role") == "tool")
    assert "Plan mode is active" in tool_result["content"]


def test_toggle_loop_mode_flips_state_and_reports_it(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)
    messages = []
    app.out.ok = messages.append

    assert app.loop_mode is False
    app.toggle_loop_mode()
    assert app.loop_mode is True
    assert "on" in messages[-1]
    app.toggle_loop_mode()
    assert app.loop_mode is False
    assert "off" in messages[-1]


def test_toggle_graph_mode_flips_state_and_reports_it(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)
    messages = []
    app.out.ok = messages.append

    assert app.graph_mode is False
    app.toggle_graph_mode()
    assert app.graph_mode is True
    assert "on" in messages[-1]
    app.toggle_graph_mode()
    assert app.graph_mode is False
    assert "off" in messages[-1]


def test_loop_mode_and_graph_mode_are_mutually_exclusive(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)
    app.out.ok = lambda *_a, **_k: None

    app.toggle_loop_mode()
    assert app.loop_mode is True
    app.toggle_graph_mode()
    assert app.graph_mode is True
    assert app.loop_mode is False

    app.toggle_loop_mode()
    assert app.loop_mode is True
    assert app.graph_mode is False


def test_loop_mode_does_exactly_one_verify_pass(tmp_path, monkeypatch):
    """Once the model gives a final reply with no tool calls, loop mode must
    inject exactly one re-verify turn and stop after the model's next reply
    — not loop indefinitely."""
    from localcoder import repl as repl_module

    app = _make_app(tmp_path, monkeypatch)
    app.loop_mode = True
    app.conversation.append({"role": "user", "content": "do the thing"})

    calls = iter(
        [
            {"content": "Done.", "tool_calls": None, "done_meta": None},
            {"content": "Checked — everything's correct.", "tool_calls": None, "done_meta": None},
        ]
    )
    seen_messages = []

    def fake_chat(**kwargs):
        seen_messages.append(kwargs["messages"])
        return next(calls)

    monkeypatch.setattr(repl_module, "chat", fake_chat)

    app.run_turn(confirm_fn=lambda _q: True)

    assert len(seen_messages) == 2
    assert app.conversation[-1] == {"role": "assistant", "content": "Checked — everything's correct."}
    verify_messages = [m for m in app.conversation if m.get("role") == "user" and "Loop mode" in m.get("content", "")]
    assert len(verify_messages) == 1


def test_run_turn_reports_each_tool_calls_result_not_just_the_call(tmp_path, monkeypatch):
    """The transcript should show what a tool call actually returned, not
    just that it was invoked — otherwise the user has no way to tell what
    happened while a turn was in progress."""
    from localcoder import repl as repl_module

    app = _make_app(tmp_path, monkeypatch)
    app.conversation.append({"role": "user", "content": "list the project root"})

    calls = iter(
        [
            {
                "content": "",
                "tool_calls": [{"id": "1", "function": {"name": "list_dir", "arguments": {}}}],
                "done_meta": None,
            },
            {"content": "Done.", "tool_calls": None, "done_meta": None},
        ]
    )
    monkeypatch.setattr(repl_module, "chat", lambda **kwargs: next(calls))

    reported_calls = []
    reported_results = []
    app.out.tool_call = lambda name, args: reported_calls.append((name, args))
    app.out.tool_result = reported_results.append

    app.run_turn(confirm_fn=lambda _q: True)

    assert reported_calls == [("list_dir", {})]
    assert len(reported_results) == 1
    tool_message = next(m for m in app.conversation if m.get("role") == "tool")
    assert reported_results[0] == json.loads(tool_message["content"])


def test_socratic_mode_injects_an_extra_system_message(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)
    assert not any(m["content"] == SOCRATIC_PROMPT for m in app.build_messages())

    app.toggle_socratic()
    messages = app.build_messages()
    assert messages[0]["content"] == SYSTEM_PROMPT
    assert messages[1]["content"] == SOCRATIC_PROMPT


def test_format_error_includes_traceback_only_when_debug_is_on(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)

    try:
        raise ValueError("kaboom")
    except ValueError as err:
        without_debug = app.format_error(err)
        app.debug = True
        with_debug = app.format_error(err)

    assert without_debug == "kaboom"
    assert "kaboom" in with_debug
    assert "Traceback" in with_debug


def test_relaunch_reexecs_the_same_interpreter_module_and_args(monkeypatch):
    """`/restart` re-execs the current process in place: same interpreter,
    same `-m localcoder` module, same command-line arguments — so a restart
    never silently drops --session/--warm-up/etc., and under --watch the
    watcher keeps seeing the very same child PID."""
    from localcoder import repl as repl_module

    calls = []

    def fake_execv(exe, args):
        calls.append((exe, args))

    monkeypatch.setattr(repl_module, "os", types.SimpleNamespace(execv=fake_execv))
    monkeypatch.setattr(
        repl_module,
        "sys",
        types.SimpleNamespace(executable="/usr/bin/python3", argv=["localcoder", "--no-warm-up"]),
    )

    repl_module._relaunch()
    assert calls == [
        ("/usr/bin/python3", ["/usr/bin/python3", "-m", "localcoder", "--no-warm-up"])
    ]
