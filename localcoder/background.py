"""Background shell tasks: fire off a long-running command (a dev server, a
test watch, a build) without blocking the turn loop, then poll its captured
output later. One BackgroundManager lives on the App for the life of the
process — tasks are not persisted across restarts (session save/load doesn't
touch them either), since a live subprocess can't be serialized anyway.
"""

from __future__ import annotations

import itertools
import subprocess
import threading
from pathlib import Path

# Background output can accumulate for a long time (a dev server's log), so
# unlike tools.MAX_OUTPUT_CHARS this keeps the *tail* — the most recent
# activity is almost always what you want when checking on a running task.
MAX_OUTPUT_CHARS = 4000


def _truncate_tail(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return f"...[truncated, {len(text) - MAX_OUTPUT_CHARS} earlier chars]\n" + text[-MAX_OUTPUT_CHARS:]


class BackgroundTask:
    def __init__(self, task_id: str, command: str, proc: subprocess.Popen):
        self.id = task_id
        self.command = command
        self.proc = proc
        self._stdout: list[str] = []
        self._stderr: list[str] = []
        self._lock = threading.Lock()
        self.exit_code: int | None = None

    def _pump(self, stream, sink: list[str]) -> None:
        for line in iter(stream.readline, ""):
            with self._lock:
                sink.append(line)
        stream.close()

    def _wait(self) -> None:
        self.exit_code = self.proc.wait()

    def start(self) -> None:
        threading.Thread(target=self._pump, args=(self.proc.stdout, self._stdout), daemon=True).start()
        threading.Thread(target=self._pump, args=(self.proc.stderr, self._stderr), daemon=True).start()
        threading.Thread(target=self._wait, daemon=True).start()

    @property
    def running(self) -> bool:
        return self.exit_code is None

    def snapshot(self) -> dict:
        with self._lock:
            stdout = "".join(self._stdout)
            stderr = "".join(self._stderr)
        return {
            "id": self.id,
            "command": self.command,
            "running": self.running,
            "exitCode": self.exit_code,
            "stdout": _truncate_tail(stdout),
            "stderr": _truncate_tail(stderr),
        }


class BackgroundManager:
    def __init__(self):
        self.tasks: dict[str, BackgroundTask] = {}
        self._ids = itertools.count(1)

    def start(self, command: str, cwd: Path) -> dict:
        task_id = f"bg{next(self._ids)}"
        try:
            proc = subprocess.Popen(
                command,
                shell=True,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as err:
            return {"error": str(err)}
        task = BackgroundTask(task_id, command, proc)
        task.start()
        self.tasks[task_id] = task
        return {"id": task_id, "command": command, "started": True}

    def list(self) -> list[dict]:
        return [
            {"id": t.id, "command": t.command, "running": t.running, "exitCode": t.exit_code}
            for t in self.tasks.values()
        ]

    def output(self, task_id: str) -> dict:
        task = self.tasks.get(task_id)
        if not task:
            return {"error": f"No background task '{task_id}'."}
        return task.snapshot()

    def stop(self, task_id: str) -> dict:
        task = self.tasks.get(task_id)
        if not task:
            return {"error": f"No background task '{task_id}'."}
        if not task.running:
            return {"ok": True, "note": "already finished"}
        task.proc.terminate()
        return {"ok": True}
