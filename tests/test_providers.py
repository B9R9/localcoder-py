from localcoder.config import Config
from localcoder import providers


def test_chat_dispatches_to_ollama_by_default(monkeypatch):
    seen = {}

    def fake_ollama_chat(**kwargs):
        seen.update(kwargs)
        return {"content": "ok", "tool_calls": None, "done_meta": None}

    monkeypatch.setattr(providers.ollama_client, "chat", fake_ollama_chat)
    cfg = Config(provider="ollama", host="http://h", model="m", num_ctx=123, temperature=0.5)

    result = providers.chat(cfg, messages=[{"role": "user", "content": "hi"}], tools=[])
    assert result["content"] == "ok"
    assert seen["host"] == "http://h"
    assert seen["model"] == "m"
    assert seen["num_ctx"] == 123


def test_chat_dispatches_to_nvidia(monkeypatch):
    seen = {}

    def fake_nvidia_chat(**kwargs):
        seen.update(kwargs)
        return {"content": "ok", "tool_calls": None, "done_meta": None}

    monkeypatch.setattr(providers.nvidia_client, "chat", fake_nvidia_chat)
    cfg = Config(provider="nvidia", base_url="http://n/v1", api_key="k", model="moonshotai/kimi-k3", temperature=0.5)

    result = providers.chat(cfg, messages=[{"role": "user", "content": "hi"}], tools=[])
    assert result["content"] == "ok"
    assert seen["base_url"] == "http://n/v1"
    assert seen["api_key"] == "k"
    assert seen["model"] == "moonshotai/kimi-k3"
    assert "num_ctx" not in seen


def test_list_models_dispatches_by_provider(monkeypatch):
    monkeypatch.setattr(providers.ollama_client, "list_models", lambda host: ["ollama-model"])
    monkeypatch.setattr(providers.nvidia_client, "list_models", lambda base_url, api_key: ["nvidia-model"])

    assert providers.list_models(Config(provider="ollama")) == ["ollama-model"]
    assert providers.list_models(Config(provider="nvidia")) == ["nvidia-model"]


def test_warm_up_is_a_noop_for_nvidia(monkeypatch):
    calls = []
    monkeypatch.setattr(providers.ollama_client, "warm_up", lambda host, model: calls.append((host, model)))
    monkeypatch.setattr(providers.nvidia_client, "warm_up", lambda: calls.append("nvidia"))

    providers.warm_up(Config(provider="nvidia"))
    providers.warm_up(Config(provider="ollama", host="http://h", model="m"))
    assert calls == ["nvidia", ("http://h", "m")]
