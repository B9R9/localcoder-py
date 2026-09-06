import io
import json
import urllib.error

import pytest

from localcoder.nvidia_client import (
    NvidiaCancelled,
    NvidiaError,
    chat,
    list_models,
    warm_up,
)


class _FakeSSEResponse:
    """Mimics urlopen()'s response object for a text/event-stream body: one
    `data: {...}` line per chunk, terminated by `data: [DONE]` — same shape
    nvidia_client.chat() reads with resp.readline().
    """

    def __init__(self, chunks):
        lines = [f"data: {json.dumps(c)}".encode("utf-8") for c in chunks]
        lines.append(b"data: [DONE]")
        self._lines = [line + b"\n" for line in lines]
        self._i = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def readline(self):
        if self._i >= len(self._lines):
            return b""
        line = self._lines[self._i]
        self._i += 1
        return line


class _FakeJsonResponse:
    def __init__(self, body: dict):
        self._body = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def test_chat_accumulates_streamed_content(monkeypatch):
    chunks = [
        {"choices": [{"delta": {"content": "Hel"}}]},
        {"choices": [{"delta": {"content": "lo"}}]},
        {"usage": {"prompt_tokens": 5, "completion_tokens": 2}, "choices": []},
    ]
    monkeypatch.setattr("urllib.request.urlopen", lambda req: _FakeSSEResponse(chunks))

    tokens = []
    result = chat(
        base_url="http://fake/v1",
        api_key="key",
        model="m",
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        temperature=0.2,
        on_token=tokens.append,
    )
    assert result["content"] == "Hello"
    assert tokens == ["Hel", "lo"]
    assert result["done_meta"]["prompt_eval_count"] == 5
    assert result["done_meta"]["eval_count"] == 2


def test_chat_reassembles_streamed_tool_call_fragments(monkeypatch):
    """OpenAI-style streaming sends a tool call's name/arguments split across
    many chunks, keyed by index — unlike Ollama's native API, which sends a
    tool call whole in a single chunk.
    """
    chunks = [
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "read_", "arguments": ""}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "file", "arguments": '{"path"'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": ': "a.txt"}'}}]}}]},
    ]
    monkeypatch.setattr("urllib.request.urlopen", lambda req: _FakeSSEResponse(chunks))

    result = chat(base_url="http://fake/v1", api_key="key", model="m", messages=[], tools=[{"type": "function"}], temperature=0.2)
    call = result["tool_calls"][0]
    assert call["id"] == "call_1"
    assert call["function"]["name"] == "read_file"
    assert json.loads(call["function"]["arguments"]) == {"path": "a.txt"}


def test_chat_raises_without_api_key():
    with pytest.raises(NvidiaError):
        chat(base_url="http://fake/v1", api_key="", model="m", messages=[], tools=[], temperature=0.2)


def test_chat_raises_on_http_error(monkeypatch):
    def raise_http_error(req):
        raise urllib.error.HTTPError("http://fake/v1", 401, "unauthorized", {}, io.BytesIO(b"bad key"))

    monkeypatch.setattr("urllib.request.urlopen", raise_http_error)

    with pytest.raises(NvidiaError):
        chat(base_url="http://fake/v1", api_key="key", model="m", messages=[], tools=[], temperature=0.2)


def test_chat_raises_on_unreachable_host(monkeypatch):
    def raise_url_error(req):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", raise_url_error)

    with pytest.raises(NvidiaError):
        chat(base_url="http://nope/v1", api_key="key", model="m", messages=[], tools=[], temperature=0.2)


def test_chat_raises_ncancelled_if_cancelled_before_request(monkeypatch):
    import threading

    event = threading.Event()
    event.set()
    with pytest.raises(NvidiaCancelled):
        chat(base_url="http://fake/v1", api_key="key", model="m", messages=[], tools=[], temperature=0.2, cancel_event=event)


def test_warm_up_is_a_noop():
    # Should not raise, and shouldn't need a real endpoint.
    warm_up()


def test_list_models_returns_sorted_ids(monkeypatch):
    body = {"data": [{"id": "meta/llama-3.3-70b-instruct"}, {"id": "moonshotai/kimi-k3"}]}
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: _FakeJsonResponse(body))

    names = list_models("http://fake/v1", "key")
    assert names == ["meta/llama-3.3-70b-instruct", "moonshotai/kimi-k3"]


def test_list_models_without_api_key_returns_empty():
    assert list_models("http://fake/v1", "") == []


def test_list_models_fails_soft_on_error(monkeypatch):
    def raise_url_error(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", raise_url_error)
    assert list_models("http://nope/v1", "key") == []
