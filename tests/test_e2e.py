"""End-to-end tests driving `python -m localcoder` as a real subprocess with
piped (non-TTY) stdin — this exercises the plain input() fallback path,
which is what a script or a piped test always gets since prompt_toolkit's
interactive menu needs a real terminal. Mirrors the Node version's
test/run-*.mjs suite.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run(cwd: Path, stdin_text: str, extra_env: dict | None = None, timeout: float = 20) -> str:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    if extra_env:
        env.update(extra_env)
    # Forward OLLAMA_HOST if mock_ollama set it, so the subprocess connects to the mock server
    if "OLLAMA_HOST" in os.environ and "OLLAMA_HOST" not in env:
        env["OLLAMA_HOST"] = os.environ["OLLAMA_HOST"]
    result = subprocess.run(
        [sys.executable, "-m", "localcoder"],
        cwd=cwd,
        input=stdin_text,
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )
    return result.stdout + result.stderr


def test_tool_loop_with_confirmation(tmp_path, mock_ollama):
    (tmp_path / "package.json").write_text("{}")
    output = _run(tmp_path, "hello\ny\n/exit\n")

    assert "read_file" in output
    assert "write_file" in output
    assert "Approve this action" in output
    assert (tmp_path / "hello.txt").read_text() == "hello from localcoder\n"
    assert "Done" in output


def test_declining_a_write_blocks_it(tmp_path, mock_ollama):
    (tmp_path / "package.json").write_text("{}")
    output = _run(tmp_path, "hello\nn\n/exit\n")

    assert "Approve this action" in output
    # The write itself must never have happened — the mock's scripted reply
    # text ("...wrote hello.txt.") is static and says nothing about whether
    # it actually did; the filesystem is the real assertion.
    assert not (tmp_path / "hello.txt").exists()


def test_role_command(tmp_path, mock_ollama):
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    (roles_dir / "tdd.md").write_text("Write a failing test first.")
    output = _run(tmp_path, "/role use tdd\n/role list\n/exit\n")

    assert 'Now using "tdd"' in output
    assert "tdd" in output


def test_context_add_and_list(tmp_path, mock_ollama):
    (tmp_path / "README.md").write_text("hello project")
    output = _run(tmp_path, "/context add README.md\n/context list\n/exit\n")

    assert "Added README.md" in output
    assert "README.md (file)" in output


@pytest.mark.parametrize("mock_ollama", ["simple"], indirect=True)
def test_session_persists_across_runs(tmp_path, mock_ollama):
    output1 = _run(tmp_path, "hello\n/exit\n")
    assert "ok" in output1

    session_file = tmp_path / ".localcoder" / "sessions" / "auth-bug.json"
    # First run had no session configured, so nothing should have been persisted yet.
    assert not session_file.exists()

    # A fresh process, same session name via localcoder.json, should resume
    # with the prior conversation loaded.
    (tmp_path / "localcoder.json").write_text(json.dumps({"session": "auth-bug"}))
    output2 = _run(tmp_path, "hello\n/exit\n")
    assert "ok" in output2
    # Second run (with the session configured) should have created the file.
    assert session_file.exists()
    data = json.loads(session_file.read_text())
    assert len(data["conversation"]) >= 2


def test_index_build_and_status(tmp_path, mock_ollama):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("def add(a, b):\n    return a + b\n")

    output = _run(tmp_path, "/index status\n/index build\n/index status\n/exit\n", timeout=30)

    assert "No index built yet" in output
    assert "Done —" in output
    assert "1 files" in output or "1 chunks" in output
    assert (tmp_path / ".localcoder" / "index.json").exists()


def test_warm_up_runs_at_startup(tmp_path, mock_ollama):
    output = _run(tmp_path, "/exit\n")
    assert "Warming up the model" in output


def test_warm_up_can_be_disabled(tmp_path, mock_ollama):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    result = subprocess.run(
        [sys.executable, "-m", "localcoder", "--no-warm-up"],
        cwd=tmp_path,
        input="/exit\n",
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
    )
    combined = result.stdout + result.stderr
    assert "Warming up the model" not in combined


def test_model_list_and_use(tmp_path, mock_ollama):
    output = _run(tmp_path, "/model list\n/model use qwen3-coder:30b\n/exit\n")
    assert "devstral-small-2" in output
    assert "qwen3-coder:30b" in output
    assert 'Now using "qwen3-coder:30b"' in output


def test_set_temperature_and_num_ctx(tmp_path, mock_ollama):
    output = _run(tmp_path, "/set temperature 0.9\n/set num_ctx 4096\n/set temperature nope\n/exit\n")
    assert "temperature = 0.9" in output
    assert "num_ctx = 4096" in output
    assert "not a valid number" in output


def test_stats_command(tmp_path, mock_ollama):
    output = _run(tmp_path, "hello\ny\n/stats\n/exit\n")
    assert "[stats] model:" in output
    assert "turns:" in output
    assert "verbose per-turn output: off" in output


@pytest.mark.parametrize("mock_ollama", ["simple"], indirect=True)
def test_verbose_toggle_shows_stats_on_next_turn(tmp_path, mock_ollama):
    output = _run(tmp_path, "/verbose\nhello\n/exit\n")
    assert "[verbose] on" in output
    assert "eval rate" in output or "tokens/s" in output.lower() or "eval duration" in output.lower()


@pytest.mark.parametrize("mock_ollama", ["simple"], indirect=True)
def test_summary_printed_to_terminal(tmp_path, mock_ollama):
    output = _run(tmp_path, "hello\n/summary\n/exit\n")
    assert "Asking the model for a recap" in output


@pytest.mark.parametrize("mock_ollama", ["simple"], indirect=True)
def test_summary_written_to_file(tmp_path, mock_ollama):
    output = _run(tmp_path, "hello\n/summary recap.md\n/exit\n")
    assert "Written to recap.md" in output
    assert (tmp_path / "recap.md").exists()
    assert (tmp_path / "recap.md").read_text().strip() != ""


def test_socratic_toggle_message(tmp_path, mock_ollama):
    output = _run(tmp_path, "/socratic\n/socratic\n/exit\n")
    assert "[socratic] on" in output
    assert "[socratic] off" in output


def test_debug_toggle_adds_traceback_to_errors(tmp_path):
    # No mock_ollama here on purpose: an unreachable host is exactly the
    # "j'ai une erreur timeout"-shaped failure /debug is meant to help with —
    # port 1 refuses immediately, so this doesn't hang or wait on a timeout.
    (tmp_path / "localcoder.json").write_text(json.dumps({"host": "http://127.0.0.1:1", "warm_up": False}))

    off = _run(tmp_path, "hello\n/exit\n")
    assert "Could not reach Ollama" in off
    assert "Traceback" not in off

    on = _run(tmp_path, "/debug\nhello\n/exit\n")
    assert "[debug] on" in on
    assert "Could not reach Ollama" in on
    assert "Traceback" in on


@pytest.mark.parametrize("mock_ollama", ["no_tools"], indirect=True)
def test_chat_only_model_falls_back_without_tools_and_warns(tmp_path, mock_ollama):
    # "je dois pouvoir utiliser les modeles meme sans tools, il faut
    # prevenir l'user que les tools seront desactiver" — a model that
    # rejects tool-calling shouldn't fail the turn outright; it should warn
    # once and keep working without tools.
    output = _run(tmp_path, "hello\nhello again\n/exit\n")
    assert "doesn't support tool-calling" in output
    assert "ok without tools" in output
    # Only warned once — the second turn already knows not to send tools.
    assert output.count("doesn't support tool-calling") == 1


def test_find_rejects_a_description_instead_of_a_symbol_name(tmp_path, mock_ollama):
    output = _run(tmp_path, "/find a function that adds\n/exit\n")
    assert "looks like a description, not a symbol name" in output
    assert "/search" in output


def test_search_command_direct_no_model_roundtrip(tmp_path, mock_ollama):
    (tmp_path / "a.py").write_text("def add(a, b):\n    return a + b\n")
    output = _run(tmp_path, "/search def add\n/exit\n")
    assert "a.py" in output


def test_search_falls_back_to_semantic_search_when_no_exact_match(tmp_path, mock_ollama):
    # "search trouve quand je mets le nom de la fonction mais pas avec du
    # texte libre" — /search is exact text/regex, so free-text like a
    # description won't literally appear in the code and used to just come
    # back empty. With an index built, it should now fall back to
    # meaning-based search instead of leaving the user stuck.
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("def add(a, b):\n    return a + b\n")
    output = _run(tmp_path, "/index build\n/search this text does not appear anywhere in the code\n/exit\n", timeout=30)

    assert "No exact matches" in output
    assert "trying meaning-based search" in output
    assert "a.py" in output


def test_search_suggests_building_an_index_when_none_exists(tmp_path, mock_ollama):
    (tmp_path / "a.py").write_text("def add(a, b):\n    return a + b\n")
    output = _run(tmp_path, "/search this text does not appear anywhere in the code\n/exit\n")

    assert "no exact matches" in output.lower()
    assert "/index build" in output


def test_find_command_direct(tmp_path, mock_ollama):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("def add(a, b):\n    return a + b\n\nadd(1, 2)\n")
    output = _run(tmp_path, "/find add\n/exit\n")
    assert "Definition" in output or "No definition found" in output
    assert "References" in output


@pytest.mark.parametrize("mock_ollama", ["simple"], indirect=True)
def test_at_mention_adds_context(tmp_path, mock_ollama):
    (tmp_path / "README.md").write_text("hello project")
    output = _run(tmp_path, "check @README.md please\n/context list\n/exit\n")
    assert "Added README.md via @mention" in output
    assert "README.md (file)" in output


def test_role_create_from_the_interface(tmp_path, mock_ollama):
    output = _run(
        tmp_path,
        "/role create pair-programmer\nThink out loud before every change.\nAsk before big refactors.\n.\n"
        "/role use pair-programmer\n/exit\n",
    )
    assert 'Saved "pair-programmer"' in output
    assert (tmp_path / "roles" / "pair-programmer.md").exists()
    content = (tmp_path / "roles" / "pair-programmer.md").read_text()
    assert "Think out loud" in content
    assert "Ask before big refactors" in content
    assert 'Now using "pair-programmer"' in output


def test_role_create_cancelled_saves_nothing(tmp_path, mock_ollama):
    output = _run(tmp_path, "/role create scrapped\nsome content\n!\n/exit\n")
    assert "Cancelled" in output
    assert not (tmp_path / "roles" / "scrapped.md").exists()


def test_skill_create_use_list_clear(tmp_path, mock_ollama):
    output = _run(
        tmp_path,
        "/skill create write-tests\nAlways add a failing test first.\n.\n"
        "/skill use write-tests\n/skill list\n/skill clear\n/exit\n",
    )
    assert 'Saved "write-tests"' in output
    assert (tmp_path / "skills" / "write-tests.md").exists()
    assert 'Activated "write-tests"' in output
    assert "write-tests (active)" in output
    assert "All skills deactivated" in output


@pytest.mark.parametrize("mock_ollama", ["simple"], indirect=True)
def test_user_turns_are_separated_by_a_rule(tmp_path, mock_ollama):
    output = _run(tmp_path, "hello\nhello again\n/exit\n")
    assert "─" in output
