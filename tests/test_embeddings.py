import json

import pytest

from localcoder.embeddings import EmbeddingError, embed


class _FakeResponse:
    def __init__(self, body: dict):
        self._body = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def test_embed_returns_vectors(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req: _FakeResponse({"embeddings": [[0.1, 0.2], [0.3, 0.4]]}),
    )
    result = embed("http://fake", "nomic-embed-text", ["a", "b"])
    assert result == [[0.1, 0.2], [0.3, 0.4]]


def test_embed_raises_when_no_embeddings_field(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda req: _FakeResponse({"error": "not an embedding model"}))
    with pytest.raises(EmbeddingError):
        embed("http://fake", "devstral-small-2", ["a"])


def test_embed_raises_on_http_error(monkeypatch):
    import io
    import urllib.error

    def raise_http_error(req):
        raise urllib.error.HTTPError("http://fake/api/embed", 500, "boom", None, io.BytesIO(b"boom"))

    monkeypatch.setattr("urllib.request.urlopen", raise_http_error)
    with pytest.raises(EmbeddingError):
        embed("http://fake", "m", ["a"])
