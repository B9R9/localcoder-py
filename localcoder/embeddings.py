"""Thin client for Ollama's /api/embed — separate from ollama_client.py
because it's a completely different shape of call (no streaming, no tools,
batched input), not because it needs different plumbing.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request


class EmbeddingError(Exception):
    pass


def embed(host: str, model: str, inputs: list[str]) -> list[list[float]]:
    url = f"{host}/api/embed"
    payload = {"model": model, "input": inputs}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        text = err.read().decode("utf-8", errors="replace")
        raise EmbeddingError(f"Embedding request failed ({err.code}): {text}") from err
    except urllib.error.URLError as err:
        raise EmbeddingError(f"Could not reach Ollama at {host}: {err.reason}") from err

    embeddings = body.get("embeddings")
    if not embeddings:
        raise EmbeddingError('Ollama response had no "embeddings" field — is the model an embedding model?')
    return embeddings
