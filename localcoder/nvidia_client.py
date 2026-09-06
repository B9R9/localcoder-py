"""Thin client for NVIDIA's OpenAI-compatible chat completions endpoint
(build.nvidia.com / integrate.api.nvidia.com) — a second provider alongside
ollama_client.py so /model can point at a hosted NIM model instead of a
local Ollama one. Stdlib only, same urllib-based streaming approach as
ollama_client.py (see its chat() docstring for why force_close() rather
than a socket timeout is what actually interrupts a Ctrl+C mid-stream —
that helper is provider-agnostic and reused as-is from there).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable, Optional

from localcoder.provider_errors import ProviderCancelled, ProviderError

# Output tokens per turn — matches the NVIDIA sample script this mirrors.
# Not exposed as a config option to keep the surface small; num_ctx (the
# Ollama equivalent) has no meaning against a hosted API.
_MAX_TOKENS = 16384


class NvidiaError(ProviderError):
    pass


class NvidiaCancelled(NvidiaError, ProviderCancelled):
    """Raised when `cancel_event` was set while streaming an SSE response —
    same meaning as ollama_client.OllamaCancelled.
    """


def chat(
    base_url: str,
    api_key: str,
    model: str,
    messages: list,
    tools: list,
    temperature: float,
    on_token: Optional[Callable[[str], None]] = None,
    cancel_event=None,
    on_response: Optional[Callable[[object], None]] = None,
) -> dict:
    if not api_key:
        raise NvidiaError(
            'No NVIDIA API key configured. Set the NVIDIA_API_KEY environment '
            'variable, or "api_key" in localcoder.json.'
        )

    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "temperature": temperature,
        "max_tokens": _MAX_TOKENS,
        # Asks the API to emit one extra chunk at the end carrying token
        # counts, so /stats and /verbose have something to show — mirrors
        # what Ollama sends unconditionally in its final ndjson line.
        "stream_options": {"include_usage": True},
    }
    if tools:
        payload["tools"] = tools

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    if cancel_event is not None and cancel_event.is_set():
        raise NvidiaCancelled("Cancelled by user.")

    content = ""
    # Streamed tool calls arrive as fragments keyed by index — name and
    # arguments each get appended to across many chunks, unlike Ollama's
    # native API which sends a tool call whole in one chunk.
    tool_call_parts: dict[int, dict] = {}
    done_meta = None

    try:
        with urllib.request.urlopen(req) as resp:
            if on_response is not None:
                on_response(resp)
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise NvidiaCancelled("Cancelled by user.")
                try:
                    raw_line = resp.readline()
                except (OSError, ValueError) as err:
                    if cancel_event is not None and cancel_event.is_set():
                        raise NvidiaCancelled("Cancelled by user.") from err
                    raise NvidiaError(f"Lost connection to the NVIDIA API: {err}") from err
                if not raw_line:
                    break
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                chunk = json.loads(data)

                usage = chunk.get("usage")
                if usage:
                    done_meta = {
                        "total_duration": None,
                        "load_duration": None,
                        "prompt_eval_count": usage.get("prompt_tokens"),
                        "prompt_eval_duration": None,
                        "eval_count": usage.get("completion_tokens"),
                        "eval_duration": None,
                    }

                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}

                piece = delta.get("content")
                if piece:
                    content += piece
                    if on_token:
                        on_token(piece)

                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    part = tool_call_parts.setdefault(idx, {"id": None, "name": "", "arguments": ""})
                    if tc.get("id"):
                        part["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        part["name"] += fn["name"]
                    if fn.get("arguments"):
                        part["arguments"] += fn["arguments"]
    except NvidiaCancelled:
        raise
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", errors="replace")
        raise NvidiaError(f"NVIDIA API request failed ({err.code}): {body}") from err
    except urllib.error.URLError as err:
        raise NvidiaError(f"Could not reach the NVIDIA API: {err.reason}") from err

    tool_calls = None
    if tool_call_parts:
        tool_calls = [
            {
                "id": part["id"] or f"call_{idx}",
                "type": "function",
                "function": {"name": part["name"], "arguments": part["arguments"]},
            }
            for idx, part in sorted(tool_call_parts.items())
        ]

    return {"content": content, "tool_calls": tool_calls, "done_meta": done_meta}


def warm_up(*_args, **_kwargs) -> None:
    """No-op: unlike a local Ollama model there's nothing to preload into
    memory on a hosted API. Exists (rather than being skipped entirely) so
    providers.warm_up() can call either client the same way.
    """


def list_models(base_url: str, api_key: str, timeout: float = 10.0) -> list[str]:
    """Models available on the NVIDIA endpoint, via GET /models — the same
    OpenAI-compatible listing endpoint that sits next to chat/completions.
    Powers /model list the same way ollama_client.list_models() does for a
    local Ollama host; fails soft to [] just like that one does.
    """
    if not api_key:
        return []
    url = f"{base_url.rstrip('/')}/models"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, ValueError):
        return []
    return sorted(m.get("id", "") for m in data.get("data", []) if m.get("id"))
