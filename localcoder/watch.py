"""Dev-mode file watcher: `localcoder --watch` runs the REPL as a child
process and restarts it whenever a watched file changes — think `nodemon`
or a server's `--reload`, but for this interactive REPL. It's the edit loop
you want while developing localcoder itself: edit a source file, and the
running session relaunches on the new code.

Standard library only — a short mtime/size poll instead of inotify/watchdog
dependencies, matching the project's zero-bloat rule.

The child process inherits the real terminal (stdin/stdout/stderr are
passed through, not captured), so the full-screen TUI in fullscreen.py
works untouched: the watcher never draws anything while a child is alive.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

# Seconds between two filesystem polls. Kept short: on a change we debounce
# for a few intervals so a save that touches the file in several chunks
# doesn't trigger several restarts in a row.
POLL_INTERVAL = 0.4

# How long we wait after the first detected change before (re)launching —
# lets the editor's writes settle.
DEBOUNCE_SECONDS = 0.8


def package_root() -> Path:
    """This package's directory — the code --watch rebuilds on by default."""
    return Path(__file__).resolve().parent


def default_watch_paths() -> list[Path]:
    """What --watch watches by default: localcoder's own source tree plus
    pyproject.toml / requirements.txt — i.e. everything that changes how the
    running code behaves. Anything else (a project's own files) must be
    added explicitly with --watch-path."""
    repo = Path(__file__).resolve().parent.parent
    return [package_root(), repo / "pyproject.toml", repo / "requirements.txt"]


def snapshot(paths: list[Path]) -> dict[Path, tuple[int, int]]:
    """Fingerprint of every watched file right now: path -> (mtime_ns, size).
    Only .py files under a watched directory are tracked; a file given
    directly (--watch-path roles/my-role.md) is always tracked."""
    result: dict[Path, tuple[int, int]] = {}
    for path in paths:
        if path.is_dir():
            candidates = path.rglob("*.py")
        elif path.is_file():
            candidates = [path]
        else:
            continue
        for file in candidates:
            try:
                stat = file.stat()
            except OSError:
                continue  # vanished mid-scan — it's a change, not a crash
            result[file.resolve()] = (stat.st_mtime_ns, stat.st_size)
    return result


def diff_changes(before: dict[Path, tuple[int, int]], after: dict[Path, tuple[int, int]]) -> list[Path]:
    """Paths added, modified (mtime and/or size) or deleted between two snapshots."""
    changed = [path for path, sig in after.items() if before.get(path) != sig]
    changed += [path for path in before if path not in after]  # deleted files
    return changed


def _await_change_or_exit(
    proc: subprocess.Popen,
    paths: list[Path],
    before: dict[Path, tuple[int, int]],
    interval: float,
    stop: threading.Event | None,
) -> tuple[list[Path] | None, int | None]:
    """Block until the watched tree changes or the child exits.

    Returns:
      (changes, None)   -> a file changed, the caller should relaunch
      (None, exit_code) -> the child exited on its own
      (None, None)      -> `stop` was set (used by tests)
    """
    while True:
        if stop is not None and stop.is_set():
            return None, None
        changed = diff_changes(before, snapshot(paths))
        if changed:
            return changed, None
        rc = proc.poll()
        if rc is not None:
            return None, rc
        time.sleep(interval)


def _terminate(proc: subprocess.Popen) -> None:
    """SIGTERM first, then SIGKILL if the child doesn't go within 3s."""
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def _notice(text: str) -> None:
    print(f"[localcoder] {text}", file=sys.stderr, flush=True)


def split_watch_args(argv: list[str]) -> tuple[list[str], list[str]]:
    """Pull the dev-mode flags --watch / --watch-path <path> out of argv so
    they are never passed down to the child process. Any --watch-path alone
    (repeatable) also enables watch mode. Returns (remaining_argv, paths)."""
    rest: list[str] = []
    paths: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--watch":
            pass
        elif arg == "--watch-path":
            if i + 1 < len(argv):
                i += 1
                paths.append(argv[i])
            else:
                _notice("warn: --watch-path without a value, ignored")
        else:
            rest.append(arg)
        i += 1
    return rest, paths


def run_with_watch(
    argv: list[str],
    extra_paths: list[str] | None = None,
    interval: float = POLL_INTERVAL,
    child: list[str] | None = None,
    stop: threading.Event | None = None,
) -> int:
    """The --watch loop. Runs `localcoder` (or `child` + argv, in tests) as a
    child process on the real terminal and restarts it on every change of a
    watched path.

    A child that exits 0 (the user quit) ends the watcher too; a child that
    crashes (bad edit, syntax error) does NOT respawn on its own — the
    watcher stays alive and relaunches on the next file change, like nodemon.
    """
    command = list(child) if child is not None else [sys.executable, "-m", "localcoder"]
    paths = default_watch_paths() + [Path(p) for p in (extra_paths or [])]
    existing = [p for p in paths if p.exists()]
    missing = [p for p in paths if not p.exists()]
    if missing:
        _notice("warn: watch path not found, ignored: " + ", ".join(str(p) for p in missing))

    current = snapshot(existing)
    _notice(f"--watch is on — watching {', '.join(str(p) for p in existing)}")

    proc: subprocess.Popen | None = None
    try:
        while True:
            if stop is not None and stop.is_set():
                return 0

            proc = subprocess.Popen([*command, *argv])
            try:
                changed, rc = _await_change_or_exit(proc, existing, current, interval, stop)

                if changed:
                    # File touched — debounce so a save in several chunks
                    # doesn't relaunch N times, then wait for the tree to
                    # settle before the next spawn.
                    time.sleep(DEBOUNCE_SECONDS)
                    current = snapshot(existing)
                    _notice("change detected — relaunching")
                    continue

                if rc == 0:
                    return 0
                if rc is None:
                    return 0  # stop requested while the child was running
                _notice(f"process exited with status {rc} — waiting for a change to relaunch")
                # A broken edit crashed the child: don't respawn on its own (that
                # would be a crash loop) — keep the watcher alive and relaunch as
                # soon as the user fixes the file.
                while not diff_changes(current, snapshot(existing)):
                    if stop is not None and stop.is_set():
                        return 0
                    time.sleep(interval)
                time.sleep(DEBOUNCE_SECONDS)
                current = snapshot(existing)
            finally:
                # Guaranteed cleanup on every path — /exit, crash, Ctrl+C or
                # `stop` requested while a child was running.
                _terminate(proc)
                proc = None
    except KeyboardInterrupt:
        return 130