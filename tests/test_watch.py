"""Tests for the dev-mode file watcher (localcoder.watch): snapshotting,
change diffing, CLI flag splitting, and the relaunch-on-change loop itself —
unit-level via snapshot/diff_changes/split_watch_args, plus an integration
test that drives run_with_watch with a tiny child script in a thread."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

from localcoder.watch import (
    diff_changes,
    run_with_watch,
    snapshot,
    split_watch_args,
)


def _wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_snapshot_tracks_py_files_and_direct_targets(tmp_path):
    (tmp_path / "a.py").write_text("print(1)\n")
    (tmp_path / "notes.txt").write_text("not code\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.py").write_text("import a\n")
    role = tmp_path / "roles" / "tdd.md"
    role.parent.mkdir()
    role.write_text("# TDD\n")

    snap = snapshot([tmp_path, role])
    # .py under a dir are tracked, .txt is not; a direct file arg is tracked
    # regardless of extension.
    assert {p.name for p in snap} == {"a.py", "c.py", "tdd.md"}
    assert all(isinstance(v, tuple) and len(v) == 2 for v in snap.values())


def test_diff_changes_reports_add_modify_delete(tmp_path):
    (tmp_path / "a.py").write_text("one\n")
    (tmp_path / "gone.py").write_text("gone\n")
    before = snapshot([tmp_path])

    (tmp_path / "a.py").write_text("two\n" * 100)  # new size too
    (tmp_path / "gone.py").unlink()
    (tmp_path / "new.py").write_text("new\n")
    after = snapshot([tmp_path])

    assert {p.name for p in diff_changes(before, after)} == {"a.py", "gone.py", "new.py"}
    assert diff_changes(after, snapshot([tmp_path])) == []  # quiet tree


def test_split_watch_args(capsys):
    rest, paths = split_watch_args(["--yolo", "--watch", "--session", "x"])
    assert rest == ["--yolo", "--session", "x"]
    assert paths == []

    rest, paths = split_watch_args(
        ["--watch-path", "roles", "--model", "m", "--watch-path", "tests/base.py"]
    )
    assert rest == ["--model", "m"]
    assert paths == ["roles", "tests/base.py"]

    split_watch_args(["--watch-path"])  # missing value warns, no crash
    assert "--watch-path without a value" in capsys.readouterr().err


def test_watch_passes_child_exit_code_through(tmp_path):
    """A child that exits 0 (the user quit) also ends the watcher, and only
    one child is ever started."""
    script = tmp_path / "exit_zero.py"
    script.write_text("import sys\nsys.exit(0)\n")
    started = []
    original_popen = subprocess.Popen

    def counting_popen(*args, **kwargs):
        started.append(args[0])
        return original_popen(*args, **kwargs)

    subprocess.Popen = counting_popen  # type: ignore[assignment]
    try:
        rc = run_with_watch(
            [],
            extra_paths=[str(tmp_path / "watched.py")],
            interval=0.02,
            child=[sys.executable, str(script)],
        )
    finally:
        subprocess.Popen = original_popen  # type: ignore[assignment]

    assert rc == 0
    assert len(started) == 1


def test_watch_terminates_child_on_stop(tmp_path):
    """stop.set() while a child runs must kill it — no orphaned process."""
    script = tmp_path / "loop.py"
    script.write_text("import time\nwhile True:\n    time.sleep(1)\n")
    watched = tmp_path / "watched.py"
    watched.write_text("v1")

    stop = threading.Event()
    child_procs = []

    def _run():
        return run_with_watch(
            [],
            extra_paths=[str(watched)],
            interval=0.02,
            child=[sys.executable, str(script)],
            stop=stop,
        )

    thread = threading.Thread(target=_run, daemon=True)
    original_popen = subprocess.Popen

    def tracking_popen(*args, **kwargs):
        proc = original_popen(*args, **kwargs)
        child_procs.append(proc)
        return proc

    subprocess.Popen = tracking_popen  # type: ignore[assignment]
    try:
        thread.start()
        assert _wait_until(lambda: bool(child_procs))
        stop.set()
        thread.join(timeout=5)
        assert _wait_until(lambda: child_procs[0].poll() is not None)
    finally:
        subprocess.Popen = original_popen  # type: ignore[assignment]
        for p in child_procs:
            if p.poll() is None:
                p.kill()


def test_watch_relaunches_child_on_file_change(tmp_path):
    """Editing a watched file kills the current child and spawns a fresh one;
    the child's start counter goes 1 -> 2."""
    watched = tmp_path / "watched.py"
    watched.write_text("v1")
    counter = tmp_path / "starts.txt"

    child_script = tmp_path / "child.py"
    child_script.write_text(
        "import sys, time\n"
        "from pathlib import Path\n"
        "counter = Path(sys.argv[1])\n"
        "n = int(counter.read_text()) if counter.exists() else 0\n"
        "counter.write_text(str(n + 1))\n"
        "while True:\n"
        "    time.sleep(1)\n"
    )

    stop = threading.Event()

    def _run():
        return run_with_watch(
            [str(counter)],
            extra_paths=[str(watched)],
            interval=0.03,
            child=[sys.executable, str(child_script)],
            stop=stop,
        )

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    try:
        assert _wait_until(lambda: counter.exists() and counter.read_text() == "1"), (
            "first child never started"
        )
        watched.write_text("v2")  # trigger a reload
        assert _wait_until(lambda: counter.read_text() == "2"), (
            "no relaunch after the change"
        )
    finally:
        stop.set()
        thread.join(timeout=5)