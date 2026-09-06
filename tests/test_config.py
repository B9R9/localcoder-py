import json

from localcoder.config import load_config


def test_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home-empty")
    monkeypatch.chdir(tmp_path)
    cfg = load_config([])
    assert cfg.model == "devstral-small-2"
    assert cfg.num_ctx == 8192
    assert cfg.auto_approve is False
    assert cfg.context == []


def test_flags_override_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home-empty")
    monkeypatch.chdir(tmp_path)
    cfg = load_config(["--model", "qwen3-coder:30b", "--num-ctx", "4096", "--yolo", "--role", "tdd"])
    assert cfg.model == "qwen3-coder:30b"
    assert cfg.num_ctx == 4096
    assert cfg.auto_approve is True
    assert cfg.role == "tdd"


def test_local_overrides_global(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".localcoder.json").write_text(json.dumps({"model": "global-model", "context": ["GLOBAL.md"]}))
    project = tmp_path / "project"
    project.mkdir()
    (project / "localcoder.json").write_text(json.dumps({"model": "local-model", "context": ["LOCAL.md"]}))

    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    monkeypatch.chdir(project)

    cfg = load_config(["--context", "FLAG.md"])
    assert cfg.model == "local-model"  # local beats global
    # context is additive across all three sources, not overridden
    assert cfg.context == ["GLOBAL.md", "LOCAL.md", "FLAG.md"]


def test_flags_beat_local_config(monkeypatch, tmp_path):
    project = tmp_path
    (project / "localcoder.json").write_text(json.dumps({"model": "local-model"}))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home-empty")
    monkeypatch.chdir(project)

    cfg = load_config(["--model", "flag-model"])
    assert cfg.model == "flag-model"


def test_provider_defaults_to_ollama(monkeypatch, tmp_path):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home-empty")
    monkeypatch.chdir(tmp_path)
    cfg = load_config([])
    assert cfg.provider == "ollama"
    assert cfg.model == "devstral-small-2"


def test_nvidia_provider_switches_default_model(monkeypatch, tmp_path):
    """devstral-small-2 is an Ollama model name — nvidia needs its own
    default rather than inheriting one that doesn't exist on its catalog.
    """
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home-empty")
    monkeypatch.chdir(tmp_path)
    cfg = load_config(["--provider", "nvidia"])
    assert cfg.provider == "nvidia"
    assert cfg.model == "moonshotai/kimi-k3"


def test_nvidia_provider_keeps_an_explicit_model(monkeypatch, tmp_path):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home-empty")
    monkeypatch.chdir(tmp_path)
    cfg = load_config(["--provider", "nvidia", "--model", "meta/llama-3.3-70b-instruct"])
    assert cfg.model == "meta/llama-3.3-70b-instruct"


def test_api_key_defaults_from_env(monkeypatch, tmp_path):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home-empty")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NVIDIA_API_KEY", "sk-test-123")
    cfg = load_config([])
    assert cfg.api_key == "sk-test-123"


def test_context_dedup(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".localcoder.json").write_text(json.dumps({"context": ["README.md"]}))
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    monkeypatch.chdir(tmp_path)

    cfg = load_config(["--context", "README.md", "--context", "docs/adr/*.md"])
    assert cfg.context == ["README.md", "docs/adr/*.md"]
