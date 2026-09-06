"""Provider-agnostic exception base classes. ollama_client and nvidia_client
each keep their own named exceptions (OllamaError, NvidiaError, ...) for
backward-compatible/targeted catches, but both subclass these so repl.py and
fullscreen.py can catch one set of names regardless of which provider is
active (see providers.py).
"""

from __future__ import annotations


class ProviderError(Exception):
    pass


class ProviderCancelled(ProviderError):
    pass


class ProviderToolsUnsupported(ProviderError):
    pass
