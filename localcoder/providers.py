"""Dispatch layer between the App (repl.py/fullscreen.py) and whichever
model backend `config.provider` points at. Callers pass the whole Config
instead of individual fields so this stays the only place that needs to
know each backend's actual parameter list — ollama_client and nvidia_client
otherwise know nothing about each other.
"""

from __future__ import annotations

from typing import Callable, Optional

from localcoder import nvidia_client, ollama_client
from localcoder.provider_errors import ProviderCancelled, ProviderError, ProviderToolsUnsupported

__all__ = [
    "ProviderCancelled",
    "ProviderError",
    "ProviderToolsUnsupported",
    "chat",
    "list_models",
    "warm_up",
]


def chat(
    config,
    messages: list,
    tools: list,
    on_token: Optional[Callable[[str], None]] = None,
    cancel_event=None,
    on_response=None,
) -> dict:
    if config.provider == "nvidia":
        return nvidia_client.chat(
            base_url=config.base_url,
            api_key=config.api_key,
            model=config.model,
            messages=messages,
            tools=tools,
            temperature=config.temperature,
            on_token=on_token,
            cancel_event=cancel_event,
            on_response=on_response,
        )
    return ollama_client.chat(
        host=config.host,
        model=config.model,
        messages=messages,
        tools=tools,
        num_ctx=config.num_ctx,
        temperature=config.temperature,
        on_token=on_token,
        cancel_event=cancel_event,
        on_response=on_response,
    )


def list_models(config) -> list[str]:
    if config.provider == "nvidia":
        return nvidia_client.list_models(config.base_url, config.api_key)
    return ollama_client.list_models(config.host)


def warm_up(config) -> None:
    if config.provider == "nvidia":
        nvidia_client.warm_up()
        return
    ollama_client.warm_up(config.host, config.model)
