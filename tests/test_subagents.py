"""Unit tests for graph mode: the FileLockRegistry guard that stops two
sub-agents from writing the same file at once, and run_graph_turn's
decompose -> parallel sub-agents -> aggregate flow.
"""

from __future__ import annotations

import threading
import time

from localcoder.repl import App
from localcoder.subagents import FileLockRegistry, run_graph_turn


def _make_app(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return App(["--no-warm-up"])


def test_file_lock_registry_serializes_same_path():
    registry = FileLockRegistry()
    events = []

    def worker(tag):
        with registry.guard("same/path.py"):
            events.append(("start", tag))
            time.sleep(0.05)
            events.append(("end", tag))

    t1 = threading.Thread(target=worker, args=("a",))
    t2 = threading.Thread(target=worker, args=("b",))
    t1.start()
    time.sleep(0.01)  # make sure t1 grabs the lock first
    t2.start()
    t1.join()
    t2.join()

    assert events == [("start", "a"), ("end", "a"), ("start", "b"), ("end", "b")]


def test_file_lock_registry_allows_different_paths_concurrently():
    registry = FileLockRegistry()
    barrier = threading.Barrier(2)
    reached = []

    def worker(tag, path):
        with registry.guard(path):
            reached.append(barrier.wait(timeout=1))

    t1 = threading.Thread(target=worker, args=("a", "path/a.py"))
    t2 = threading.Thread(target=worker, args=("b", "path/b.py"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Both threads reached the barrier without timing out — i.e. neither was
    # blocked waiting on the other's (different-path) lock.
    assert len(reached) == 2


def test_run_graph_turn_falls_back_to_single_agent_when_decompose_yields_one_task(tmp_path, monkeypatch):
    from localcoder import subagents as subagents_module

    app = _make_app(tmp_path, monkeypatch)
    app.conversation.append({"role": "user", "content": "just fix this one typo"})

    monkeypatch.setattr(
        subagents_module,
        "chat",
        lambda **kwargs: {"content": '["fix the typo"]', "tool_calls": None, "done_meta": None},
    )

    fallback_calls = []
    app.run_turn = lambda confirm_fn, cancel_event=None, on_response=None: fallback_calls.append(confirm_fn)

    run_graph_turn(app, confirm_fn=lambda _q: True)

    assert len(fallback_calls) == 1


def test_run_graph_turn_decomposes_runs_subagents_and_aggregates(tmp_path, monkeypatch):
    from localcoder import subagents as subagents_module

    app = _make_app(tmp_path, monkeypatch)
    app.conversation.append({"role": "user", "content": "create foo.py and bar.py"})

    def fake_chat(*, config, messages, tools, on_token=None, cancel_event=None, on_response=None):
        system_msgs = [m["content"] for m in messages if m["role"] == "system"]
        user_msgs = [m["content"] for m in messages if m["role"] == "user"]
        if any("Split the user's request" in s for s in system_msgs):
            return {"content": '["write foo.py", "write bar.py"]', "tool_calls": None, "done_meta": None}
        if any("Combine them into one clear" in s for s in system_msgs):
            combined = user_msgs[0]
            assert "write foo.py" in combined and "write bar.py" in combined
            return {
                "content": "Both files are done.",
                "tool_calls": None,
                "done_meta": {"prompt_eval_count": 10, "eval_count": 5, "total_duration": 100},
            }
        # a sub-agent turn: finish immediately with a result mentioning its task
        task = user_msgs[0] if user_msgs else ""
        return {"content": f"completed: {task}", "tool_calls": None, "done_meta": None}

    monkeypatch.setattr(subagents_module, "chat", fake_chat)

    run_graph_turn(app, confirm_fn=lambda _q: True)

    assert app.conversation[-1] == {"role": "assistant", "content": "Both files are done."}
    assert app.stats["prompt_tokens"] == 10
    assert app.stats["eval_tokens"] == 5
