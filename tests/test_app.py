"""Unit tests for App's session-state toggles (verbose/debug/socratic) and
message building — the same App instance both repl.py's plain loop and
fullscreen.py's UI drive, so testing it directly here covers both paths at
once without going through a subprocess.
"""

from __future__ import annotations

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


def test_run_turn_warns_when_model_fakes_a_tool_call_as_plain_text(tmp_path, monkeypatch):
    """A weak model that doesn't use real tool-calling can hallucinate the
    tool-call JSON shape directly into `content` instead. Nothing runs in
    that case (no `tool_calls` on the response), so the user should be told
    the turn actually failed rather than seeing the raw JSON silently
    presented as a normal reply."""
    from localcoder import repl as repl_module

    app = _make_app(tmp_path, monkeypatch)
    app.conversation.append({"role": "user", "content": "change the vault icon to a lock"})
    warnings = []
    app.out.warn = warnings.append

    fake_reply = '{"name": "search_code", "arguments": {"pattern": "vault icon"}}'
    monkeypatch.setattr(
        repl_module,
        "chat",
        lambda **kwargs: {"content": fake_reply, "tool_calls": None, "done_meta": None},
    )

    app.run_turn(confirm_fn=lambda _q: True)

    assert len(warnings) == 1
    assert "tried to call a tool as plain text" in warnings[0]
    assert app.conversation[-1] == {"role": "assistant", "content": fake_reply}


def test_run_turn_does_not_warn_on_an_ordinary_prose_reply(tmp_path, monkeypatch):
    from localcoder import repl as repl_module

    app = _make_app(tmp_path, monkeypatch)
    app.conversation.append({"role": "user", "content": "what does this function do?"})
    warnings = []
    app.out.warn = warnings.append

    monkeypatch.setattr(
        repl_module,
        "chat",
        lambda **kwargs: {"content": "It reads the file and returns its contents.", "tool_calls": None, "done_meta": None},
    )

    app.run_turn(confirm_fn=lambda _q: True)

    assert warnings == []
