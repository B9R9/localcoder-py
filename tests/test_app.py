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
