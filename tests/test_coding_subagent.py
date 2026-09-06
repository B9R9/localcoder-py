import json
import subprocess

import pytest

from localcoder.coding_subagent import run_coding_subagents
from localcoder.tools import execute_tool, get_tools, needs_confirmation


def _init_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=path, check=True)
    return path


def _show(repo, ref, path):
    return subprocess.run(
        ["git", "show", f"{ref}:{path}"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


def _ctx(repo, **overrides):
    return {
        "cwd": repo,
        "host": "unused",
        "model": "devstral-small-2",
        "num_ctx": 8192,
        "temperature": 0.2,
        "embed_model": "nomic-embed-text",
        "index_name": "default",
        **overrides,
    }


def _filename_for(task: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in task) + ".txt"


def _fake_chat_writes_one_file(host, model, messages, tools, num_ctx, temperature, on_token=None, cancel_event=None, on_response=None):
    """Deterministic regardless of thread interleaving: decides what to do
    purely from the conversation it was handed, never from shared state —
    each parallel branch calls this independently and concurrently.
    """
    last = messages[-1]
    if last["role"] == "user":
        task = last["content"]
        args = json.dumps({"path": _filename_for(task), "content": task})
        return {
            "content": "",
            "tool_calls": [{"id": "c1", "function": {"name": "write_file", "arguments": args}}],
            "done_meta": None,
        }
    return {"content": "done", "tool_calls": None, "done_meta": None}


def _fake_chat_writes_shared_file(host, model, messages, tools, num_ctx, temperature, on_token=None, cancel_event=None, on_response=None):
    last = messages[-1]
    if last["role"] == "user":
        task = last["content"]
        args = json.dumps({"path": "shared.txt", "content": task})
        return {
            "content": "",
            "tool_calls": [{"id": "c1", "function": {"name": "write_file", "arguments": args}}],
            "done_meta": None,
        }
    return {"content": "done", "tool_calls": None, "done_meta": None}


def test_spawn_coding_subagents_is_offered_and_needs_confirmation(tmp_path):
    names = {t["function"]["name"] for t in get_tools(tmp_path)}
    assert "spawn_coding_subagents" in names
    assert needs_confirmation("spawn_coding_subagents") is True


def test_run_coding_subagents_requires_a_git_repo(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    result = run_coding_subagents(["do something"], _ctx(plain))
    assert "git repository" in result["error"]


def test_run_coding_subagents_requires_at_least_one_task(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    assert "error" in run_coding_subagents([], _ctx(repo))


def test_run_coding_subagents_merges_independent_branches(tmp_path, monkeypatch):
    monkeypatch.setattr("localcoder.coding_subagent.chat", _fake_chat_writes_one_file)
    repo = _init_repo(tmp_path / "repo")
    tasks = ["add a foo helper", "add a bar helper"]

    result = run_coding_subagents(tasks, _ctx(repo))

    assert result["conflicts"] == []
    assert len(result["merged_branches"]) == 2
    assert result["base_branch"] == "main"
    # The caller's own branch must be untouched — everything landed on the
    # work branch instead.
    assert subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip() == "main"
    for task in tasks:
        assert _show(repo, result["work_branch"], _filename_for(task)) == task


def test_run_coding_subagents_reports_conflicts_without_losing_the_branch(tmp_path, monkeypatch):
    monkeypatch.setattr("localcoder.coding_subagent.chat", _fake_chat_writes_shared_file)
    repo = _init_repo(tmp_path / "repo")

    result = run_coding_subagents(["write A", "write B"], _ctx(repo))

    assert len(result["merged_branches"]) == 1
    assert len(result["conflicts"]) == 1
    # The losing branch's commit must still exist for manual resolution —
    # not silently dropped.
    conflict_branch = result["conflicts"][0]["branch"]
    branches = subprocess.run(["git", "branch", "--list", conflict_branch], cwd=repo, capture_output=True, text=True).stdout
    assert conflict_branch in branches


def test_run_coding_subagents_respects_max_subagents(tmp_path, monkeypatch):
    monkeypatch.setattr("localcoder.coding_subagent.chat", _fake_chat_writes_one_file)
    repo = _init_repo(tmp_path / "repo")
    tasks = [f"task {i}" for i in range(5)]

    result = run_coding_subagents(tasks, _ctx(repo, max_subagents=2))

    assert len(result["results"]) == 2


def test_execute_tool_dispatches_spawn_coding_subagents(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    result = execute_tool("spawn_coding_subagents", {"tasks": ["x"]}, _ctx(plain))
    assert "error" in result
