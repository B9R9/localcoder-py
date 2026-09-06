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
    assert cfg.max_subagents == 4


def test_max_subagents_flag(monkeypatch, tmp_path):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home-empty")
    monkeypatch.chdir(tmp_path)
    cfg = load_config(["--max-subagents", "2"])
    assert cfg.max_subagents == 2


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


def test_context_dedup(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".localcoder.json").write_text(json.dumps({"context": ["README.md"]}))
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    monkeypatch.chdir(tmp_path)

    cfg = load_config(["--context", "README.md", "--context", "docs/adr/*.md"])
    assert cfg.context == ["README.md", "docs/adr/*.md"]
