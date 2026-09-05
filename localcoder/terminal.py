"""Opens a new session in its own terminal window instead of reusing the
current process, so `/session new` can run side by side with whatever is
already active in this terminal. macOS only for now (osascript driving
Terminal.app) — other platforms raise TerminalError so callers can fall
back to the old in-process behavior.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path


class TerminalError(RuntimeError):
    """Raised when no supported terminal could be opened."""


def open_new_terminal(cwd: Path, command: list[str]) -> None:
    if sys.platform != "darwin":
        raise TerminalError(f"opening a new terminal isn't supported on {sys.platform} yet")

    script = f"cd {shlex.quote(str(cwd))} && {shlex.join(command)}"
    applescript = (
        'tell application "Terminal"\n'
        f"  do script {_applescript_quote(script)}\n"
        "  activate\n"
        "end tell"
    )
    try:
        subprocess.run(["osascript", "-e", applescript], check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as err:
        raise TerminalError(str(err)) from err


def _applescript_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def argv_with_session(argv: list[str], name: str) -> list[str]:
    """argv with any existing --session <value> swapped for `name`, or the
    flag appended if the current run didn't have one."""
    result = list(argv)
    for i, arg in enumerate(result):
        if arg == "--session" and i + 1 < len(result):
            result[i + 1] = name
            return result
    return [*result, "--session", name]
