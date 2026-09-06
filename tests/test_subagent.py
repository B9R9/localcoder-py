import os

import pytest

from localcoder.subagent import run_subagent
from localcoder.tools import execute_tool, get_tools


def _ctx(tmp_path):
    return {
        "cwd": tmp_path,
        "host": os.environ["OLLAMA_HOST"],
        "model": "devstral-small-2",
        "num_ctx": 8192,
        "temperature": 0.2,
        "embed_model": "nomic-embed-text",
        "index_name": "default",
    }


def test_spawn_subagent_is_always_offered(tmp_path):
    names = {t["function"]["name"] for t in get_tools(tmp_path)}
    assert "spawn_subagent" in names


@pytest.mark.parametrize("mock_ollama", ["simple"], indirect=True)
def test_run_subagent_returns_final_answer_without_tool_calls(tmp_path, mock_ollama):
    result = run_subagent("what does this project do?", _ctx(tmp_path))
    assert result == {"result": "ok (2 messages)"}


def test_run_subagent_uses_read_only_tools_but_blocks_writes(tmp_path, mock_ollama):
    # The default mock scripts: turn 1 -> read_file, turn 2 -> write_file, turn 3 -> final text.
    (tmp_path / "package.json").write_text("{}")

    result = run_subagent("investigate the project", _ctx(tmp_path))

    assert result == {"result": "Done — I read package.json and wrote hello.txt."}
    # write_file is not in the sub-agent's read-only tool set, so the call
    # must never actually reach execute_tool — the file must not exist.
    assert not (tmp_path / "hello.txt").exists()


def test_execute_tool_dispatches_spawn_subagent(tmp_path):
    ctx = {
        "cwd": tmp_path,
        "host": "http://127.0.0.1:1",  # nothing listening — exercise the OllamaError path
        "model": "devstral-small-2",
        "num_ctx": 8192,
        "temperature": 0.2,
        "embed_model": "nomic-embed-text",
        "index_name": "default",
    }
    result = execute_tool("spawn_subagent", {"task": "anything"}, ctx)
    assert "error" in result
