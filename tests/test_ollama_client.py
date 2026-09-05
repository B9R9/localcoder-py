import json
import socket
import threading

import pytest

from localcoder.ollama_client import (
    OllamaCancelled,
    OllamaError,
    OllamaToolsUnsupported,
    chat,
    force_close,
    list_models,
    warm_up,
)


class _FakeResponse:
    def __init__(self, ndjson_objects):
        self._lines = [json.dumps(o).encode("utf-8") + b"\n" for o in ndjson_objects]
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
        {"message": {"role": "assistant", "content": "Hel"}, "done": False},
        {"message": {"role": "assistant", "content": "lo"}, "done": False},
        {"message": {"role": "assistant", "content": ""}, "done": True, "total_duration": 123, "prompt_eval_count": 5, "eval_count": 2},
    ]
    monkeypatch.setattr("urllib.request.urlopen", lambda req: _FakeResponse(chunks))

    tokens = []
    result = chat(
        host="http://fake",
        model="m",
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        num_ctx=8192,
        temperature=0.2,
        on_token=tokens.append,
    )
    assert result["content"] == "Hello"
    assert tokens == ["Hel", "lo"]
    assert result["done_meta"]["prompt_eval_count"] == 5


def test_chat_captures_tool_calls(monkeypatch):
    chunks = [
        {
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"function": {"name": "read_file", "arguments": {"path": "a.txt"}}}],
            },
            "done": False,
        },
        {"message": {"content": ""}, "done": True},
    ]
    monkeypatch.setattr("urllib.request.urlopen", lambda req: _FakeResponse(chunks))

    result = chat(host="http://fake", model="m", messages=[], tools=[], num_ctx=8192, temperature=0.2)
    assert result["tool_calls"][0]["function"]["name"] == "read_file"


def test_chat_raises_ollama_tools_unsupported_on_a_tools_rejecting_400(monkeypatch):
    """Some chat-only models on the Hub (e.g. deepseek-coder:33b) reject any
    request carrying a `tools` array with a 400 whose body says so. This
    must surface as the specific OllamaToolsUnsupported subclass — not a
    generic OllamaError — so repl.py can retry the same turn without tools
    instead of failing it outright.
    """
    import io
    import urllib.error

    def raise_http_error(req):
        body = json.dumps({"error": "model \"deepseek-coder:33b\" does not support tools"}).encode("utf-8")
        raise urllib.error.HTTPError("http://fake", 400, "Bad Request", {}, io.BytesIO(body))

    monkeypatch.setattr("urllib.request.urlopen", raise_http_error)

    with pytest.raises(OllamaToolsUnsupported):
        chat(host="http://fake", model="deepseek-coder:33b", messages=[], tools=[{"type": "function"}], num_ctx=8192, temperature=0.2)


def test_chat_raises_plain_ollama_error_on_an_unrelated_400(monkeypatch):
    """A 400 for some other reason must stay a generic OllamaError, not get
    misclassified as the tools-unsupported case just because it's a 400.
    """
    import io
    import urllib.error

    def raise_http_error(req):
        body = json.dumps({"error": "invalid options"}).encode("utf-8")
        raise urllib.error.HTTPError("http://fake", 400, "Bad Request", {}, io.BytesIO(body))

    monkeypatch.setattr("urllib.request.urlopen", raise_http_error)

    with pytest.raises(OllamaError) as excinfo:
        chat(host="http://fake", model="m", messages=[], tools=[], num_ctx=8192, temperature=0.2)
    assert not isinstance(excinfo.value, OllamaToolsUnsupported)


def test_chat_raises_on_unreachable_host(monkeypatch):
    import urllib.error

    def raise_url_error(req):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", raise_url_error)

    with pytest.raises(OllamaError):
        chat(host="http://nope", model="m", messages=[], tools=[], num_ctx=8192, temperature=0.2)


def test_chat_captures_full_done_meta(monkeypatch):
    chunks = [
        {"message": {"role": "assistant", "content": "hi"}, "done": False},
        {
            "message": {"content": ""},
            "done": True,
            "total_duration": 100,
            "load_duration": 10,
            "prompt_eval_count": 5,
            "prompt_eval_duration": 20,
            "eval_count": 3,
            "eval_duration": 30,
        },
    ]
    monkeypatch.setattr("urllib.request.urlopen", lambda req: _FakeResponse(chunks))

    result = chat(host="http://fake", model="m", messages=[], tools=[], num_ctx=8192, temperature=0.2)
    meta = result["done_meta"]
    assert meta["total_duration"] == 100
    assert meta["load_duration"] == 10
    assert meta["prompt_eval_count"] == 5
    assert meta["prompt_eval_duration"] == 20
    assert meta["eval_count"] == 3
    assert meta["eval_duration"] == 30


def test_warm_up_succeeds(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: _FakeJsonResponse({"done": True}))
    # Should not raise.
    warm_up("http://fake", "m")


def test_warm_up_raises_on_http_error(monkeypatch):
    import io
    import urllib.error

    def raise_http_error(req, timeout=None):
        raise urllib.error.HTTPError("http://fake", 500, "boom", {}, io.BytesIO(b"server exploded"))

    monkeypatch.setattr("urllib.request.urlopen", raise_http_error)

    with pytest.raises(OllamaError):
        warm_up("http://fake", "m")


def test_warm_up_raises_on_unreachable_host(monkeypatch):
    import urllib.error

    def raise_url_error(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", raise_url_error)

    with pytest.raises(OllamaError):
        warm_up("http://nope", "m")


def test_list_models_returns_sorted_names(monkeypatch):
    body = {"models": [{"name": "qwen3-coder:30b"}, {"name": "devstral-small-2"}]}
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: _FakeJsonResponse(body))

    assert list_models("http://fake") == ["devstral-small-2", "qwen3-coder:30b"]


def test_list_models_returns_empty_list_on_failure(monkeypatch):
    import urllib.error

    def raise_url_error(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", raise_url_error)

    assert list_models("http://nope") == []


def test_list_models_skips_entries_without_a_name(monkeypatch):
    body = {"models": [{"name": "devstral-small-2"}, {}]}
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: _FakeJsonResponse(body))

    assert list_models("http://fake") == ["devstral-small-2"]


class _FakeCancellableResponse:
    """readline() yields one real token, then flips `event` (simulating
    force_close() shutting the socket down from another thread) and raises
    an OSError — exactly the shape a real force_close()'d read produces.
    """

    def __init__(self, event: threading.Event):
        self._event = event
        self._calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def readline(self):
        self._calls += 1
        if self._calls == 1:
            return json.dumps({"message": {"role": "assistant", "content": "hi"}, "done": False}).encode("utf-8") + b"\n"
        self._event.set()
        raise OSError("socket shut down")


def test_chat_raises_ollama_cancelled_once_cancel_event_is_set(monkeypatch):
    """The window chat() itself polls: cancel_event flips (here, simulating
    force_close() waking up a blocked read with an OSError) while a read is
    in flight -> OllamaCancelled, not OllamaError.
    """
    event = threading.Event()
    monkeypatch.setattr("urllib.request.urlopen", lambda req: _FakeCancellableResponse(event))

    with pytest.raises(OllamaCancelled):
        chat(host="http://fake", model="m", messages=[], tools=[], num_ctx=8192, temperature=0.2, cancel_event=event)


def test_chat_raises_ollama_error_on_a_genuine_dropped_connection(monkeypatch):
    """The same OSError from readline(), but with no cancellation in play at
    all, must surface as a real OllamaError — the new cancel-detection must
    not swallow an actual dropped connection.
    """

    class _DropsConnection:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def readline(self):
            raise OSError("connection reset by peer")

    monkeypatch.setattr("urllib.request.urlopen", lambda req: _DropsConnection())

    with pytest.raises(OllamaError) as excinfo:
        chat(host="http://fake", model="m", messages=[], tools=[], num_ctx=8192, temperature=0.2)
    assert not isinstance(excinfo.value, OllamaCancelled)


def test_chat_returns_immediately_if_already_cancelled(monkeypatch):
    event = threading.Event()
    event.set()

    def fail_if_read(req):
        raise AssertionError("should never read from the socket once already cancelled")

    monkeypatch.setattr("urllib.request.urlopen", fail_if_read)

    with pytest.raises(OllamaCancelled):
        chat(host="http://fake", model="m", messages=[], tools=[], num_ctx=8192, temperature=0.2, cancel_event=event)


def test_chat_never_passes_a_timeout_kwarg(monkeypatch):
    """Regression guard for the original "timeout error" bug report: chat()
    must never put any timeout on the connection (a real Ollama reply can
    take much longer than any timeout short enough to poll usefully) —
    cancellation is on_response()/force_close() instead, see chat()'s
    docstring.
    """
    seen = {}

    def fake_urlopen(req):
        seen["called"] = True
        return _FakeResponse([{"message": {"content": "ok"}, "done": True}])

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    chat(host="http://fake", model="m", messages=[], tools=[], num_ctx=8192, temperature=0.2, cancel_event=threading.Event())
    assert seen.get("called") is True


def test_chat_calls_on_response_with_the_open_response(monkeypatch):
    chunks = [{"message": {"content": "ok"}, "done": True}]
    fake_resp = _FakeResponse(chunks)
    monkeypatch.setattr("urllib.request.urlopen", lambda req: fake_resp)

    seen = []
    chat(
        host="http://fake", model="m", messages=[], tools=[], num_ctx=8192, temperature=0.2,
        on_response=seen.append,
    )
    assert seen == [fake_resp]


def test_force_close_shuts_down_the_socket_then_closes_the_response():
    calls = []

    class _Sock:
        def shutdown(self, how):
            calls.append(("shutdown", how))

    class _Raw:
        _sock = _Sock()

    class _Fp:
        raw = _Raw()

    class _Resp:
        fp = _Fp()

        def close(self):
            calls.append(("close",))

    force_close(_Resp())
    assert calls == [("shutdown", socket.SHUT_RDWR), ("close",)]


def test_force_close_swallows_errors_from_an_already_closed_response():
    class _Resp:
        @property
        def fp(self):
            raise ValueError("already closed")

        def close(self):
            raise ValueError("already closed")

    force_close(_Resp())  # must not raise
