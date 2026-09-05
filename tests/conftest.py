import contextlib
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with contextlib.suppress(OSError):
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        time.sleep(0.05)
    raise RuntimeError(f"mock server did not start on port {port}")


@pytest.fixture()
def mock_ollama(request, monkeypatch):
    """Spawns a fresh mock Ollama server per test (its turn counter is
    per-process state, so tests must not share one running instance).
    """
    import os

    port = _find_free_port()
    host = f"http://127.0.0.1:{port}"
    monkeypatch.setenv("OLLAMA_HOST", host)

    env = dict(os.environ)
    env["OLLAMA_PORT"] = str(port)
    env["OLLAMA_HOST"] = host
    param = getattr(request, "param", None)
    if param == "simple":
        env["MOCK_SIMPLE"] = "1"
    elif param == "no_tools":
        env["MOCK_NO_TOOLS"] = "1"

    proc = subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "tests" / "mock_ollama.py")],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    try:
        _wait_for_port(port)
        yield proc
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
