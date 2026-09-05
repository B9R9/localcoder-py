"""Thin client for Ollama's native /api/chat — deliberately not going
through the OpenAI-compat layer, so we control num_ctx and keep the request
lean. Stdlib only (urllib) — no requests/httpx dependency.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable, Optional


class OllamaError(Exception):
    pass


class OllamaCancelled(OllamaError):
    """Raised when `cancel_event` was set while a chat() call was streaming —
    lets callers (the full-screen UI's Ctrl+C handling) tell "the user
    cancelled this turn" apart from an actual network/model failure.
    """


class OllamaToolsUnsupported(OllamaError):
    """Raised when Ollama rejects a request specifically because the active
    model doesn't support tool-calling at all (a 400 whose body says so —
    plenty of models on the Hub are chat-only). Lets callers retry the same
    request without `tools` instead of just failing the turn outright, since
    the model itself is otherwise perfectly usable for plain conversation.
    """


# Substring Ollama's own error body uses for this case — matched
# case-insensitively; kept as one constant so the retry-without-tools logic
# in repl.py checks for exactly the string this module recognizes.
_TOOLS_UNSUPPORTED_MARKER = "does not support tools"


def force_close(resp) -> None:
    """Unblocks a chat() call that's currently sitting in a blocking read —
    e.g. waiting on a slow prompt-eval, or a stall between tokens — from
    another thread. Safe to call more than once, and safe to call on a
    response that has already finished normally.

    Just closing the response is not reliably enough to interrupt a
    concurrent blocking read on every platform, so this shuts the raw
    socket down first (which POSIX guarantees wakes up a thread blocked in
    recv() on it) and only then closes the response.
    """
    try:
        resp.fp.raw._sock.shutdown(__import__("socket").SHUT_RDWR)  # noqa: SLF001
    except Exception:
        pass
    try:
        resp.close()
    except Exception:
        pass


def chat(
    host: str,
    model: str,
    messages: list,
    tools: list,
    num_ctx: int,
    temperature: float,
    on_token: Optional[Callable[[str], None]] = None,
    cancel_event=None,
    on_response: Optional[Callable[[object], None]] = None,
) -> dict:
    """`cancel_event` (a threading.Event) and `on_response` are an optional
    pair used together for cancellation: the caller stashes the response
    object handed to `on_response` somewhere it can reach from another
    thread, and calls `force_close()` on it when the user hits Ctrl+C —
    that's what actually interrupts a blocked read, not `cancel_event`
    alone. `cancel_event` alone only catches the (much smaller) window
    between two already-arrived lines, and lets the caller skip opening a
    new connection at all if cancellation happened before this call started.
    Deliberately NOT implemented as a short socket timeout that gets polled:
    Ollama can easily take several seconds just to finish prompt-eval and
    send its first byte on an ordinary local request, and a short enough
    timeout to poll usefully there is short enough to misfire as a bogus
    "Could not reach Ollama" error on a perfectly healthy, if slow, reply.
    """
    url = f"{host}/api/chat"
    payload = {
        "model": model,
        "messages": messages,
        "tools": tools,
        "stream": True,
        "options": {"num_ctx": num_ctx, "temperature": temperature},
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    if cancel_event is not None and cancel_event.is_set():
        raise OllamaCancelled("Cancelled by user.")

    content = ""
    tool_calls = None
    done_meta = None

    try:
        with urllib.request.urlopen(req) as resp:
            if on_response is not None:
                on_response(resp)
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise OllamaCancelled("Cancelled by user.")
                try:
                    raw_line = resp.readline()
                except (OSError, ValueError) as err:
                    # force_close() from another thread surfaces here — treat
                    # it as a cancellation only if that's actually why we're
                    # here, otherwise it's a genuine dropped connection.
                    if cancel_event is not None and cancel_event.is_set():
                        raise OllamaCancelled("Cancelled by user.") from err
                    raise OllamaError(f"Lost connection to Ollama: {err}") from err
                if not raw_line:
                    break
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                chunk = json.loads(line)
                message = chunk.get("message") or {}

                piece = message.get("content")
                if piece:
                    content += piece
                    if on_token:
                        on_token(piece)

                calls = message.get("tool_calls")
                if calls:
                    tool_calls = calls

                if chunk.get("done"):
                    done_meta = {
                        "total_duration": chunk.get("total_duration"),
                        "load_duration": chunk.get("load_duration"),
                        "prompt_eval_count": chunk.get("prompt_eval_count"),
                        "prompt_eval_duration": chunk.get("prompt_eval_duration"),
                        "eval_count": chunk.get("eval_count"),
                        "eval_duration": chunk.get("eval_duration"),
                    }
    except OllamaCancelled:
        raise
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", errors="replace")
        if err.code == 400 and _TOOLS_UNSUPPORTED_MARKER in body.lower():
            raise OllamaToolsUnsupported(f"{model} does not support tools.") from err
        raise OllamaError(f"Ollama request failed ({err.code}): {body}") from err
    except urllib.error.URLError as err:
        raise OllamaError(f"Could not reach Ollama at {host}: {err.reason}") from err

    return {"content": content, "tool_calls": tool_calls, "done_meta": done_meta}


def warm_up(host: str, model: str, timeout: float = 300.0) -> None:
    """Asks Ollama to load `model` into memory without generating anything —
    an empty prompt to /api/generate does this per Ollama's own docs. Called
    once at startup so the model is already warm by the time the user sends
    their first real message, instead of eating that cold-start cost there.

    300s default: a large model's first cold load from disk (e.g. a 30B
    model on a laptop) can genuinely take minutes, especially the very
    first time before the OS has anything cached — a short timeout here
    would misreport that as a failure when Ollama is simply still loading.
    """
    url = f"{host}/api/generate"
    payload = {"model": model, "keep_alive": "5m"}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", errors="replace")
        raise OllamaError(f"Warm-up request failed ({err.code}): {body}") from err
    except urllib.error.URLError as err:
        raise OllamaError(f"Could not reach Ollama at {host}: {err.reason}") from err


def list_models(host: str, timeout: float = 10.0) -> list[str]:
    """Models Ollama already has pulled locally, via GET /api/tags — powers
    the /model use picker the same way roles/sessions/files are listed.
    """
    url = f"{host}/api/tags"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
        return []
    return sorted(m.get("name", "") for m in data.get("models", []) if m.get("name"))
