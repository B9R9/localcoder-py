import string

from localcoder import index_store


def _fake_embed(host, model, inputs):
    # Deterministic 26-dim letter-frequency embedding — enough to make
    # cosine similarity behave meaningfully differently for different text,
    # without needing a real Ollama server.
    vectors = []
    for text in inputs:
        lower = text.lower()
        vectors.append([lower.count(letter) for letter in string.ascii_lowercase])
    return vectors


def test_build_and_search(tmp_path, monkeypatch):
    monkeypatch.setattr(index_store, "embed", _fake_embed)

    (tmp_path / "README.md").write_text("unrelated readme content\n")
    src = tmp_path / "src"
    src.mkdir()
    (src / "xray.js").write_text("x" * 40 + "\n")

    result = index_store.build_index(tmp_path, "http://fake", "fake-embed")
    assert result["fileCount"] == 2
    assert result["embedded"] == 2
    assert result["reused"] == 0

    stats = index_store.index_stats(tmp_path)
    assert stats["fileCount"] == 2
    assert stats["chunkCount"] == 2

    # rebuilding with no changes reuses everything
    result2 = index_store.build_index(tmp_path, "http://fake", "fake-embed")
    assert result2["embedded"] == 0
    assert result2["reused"] == 2

    # changing one file re-embeds only that one
    (src / "xray.js").write_text("y" * 40 + "\n")
    result3 = index_store.build_index(tmp_path, "http://fake", "fake-embed")
    assert result3["embedded"] == 1
    assert result3["reused"] == 1


def test_semantic_search_matches_relevant_chunk(tmp_path, monkeypatch):
    monkeypatch.setattr(index_store, "embed", _fake_embed)
    (tmp_path / "README.md").write_text("unrelated readme content\n")
    src = tmp_path / "src"
    src.mkdir()
    (src / "xray.js").write_text("x" * 40 + "\n")

    index_store.build_index(tmp_path, "http://fake", "fake-embed")
    result = index_store.semantic_search("x" * 40, tmp_path, "http://fake", "fake-embed")
    assert result["results"][0]["path"] == "src/xray.js"
    assert result["results"][0]["score"] > result["results"][-1]["score"]


def test_semantic_search_no_index(tmp_path):
    result = index_store.semantic_search("anything", tmp_path, "http://fake", "fake-embed")
    assert "error" in result


def test_index_stats_none_when_no_index(tmp_path):
    assert index_store.index_stats(tmp_path) is None


def test_chunking_splits_long_files(tmp_path, monkeypatch):
    monkeypatch.setattr(index_store, "embed", _fake_embed)
    long_file = tmp_path / "big.py"
    long_file.write_text("\n".join(f"line {i}" for i in range(120)) + "\n")

    index_store.build_index(tmp_path, "http://fake", "fake-embed")
    stats = index_store.index_stats(tmp_path)
    # 120 lines / (40 - 8 overlap per step) should split into multiple chunks
    assert stats["chunkCount"] > 1


def test_named_indexes_coexist(tmp_path, monkeypatch):
    monkeypatch.setattr(index_store, "embed", _fake_embed)
    (tmp_path / "a.py").write_text("x" * 10 + "\n")

    index_store.build_index(tmp_path, "http://fake", "fake-embed", name="default")
    index_store.build_index(tmp_path, "http://fake", "other-model", name="alt")

    # Each name is its own file — building one doesn't disturb the other.
    assert index_store.index_stats(tmp_path, "default")["model"] == "fake-embed"
    assert index_store.index_stats(tmp_path, "alt")["model"] == "other-model"


def test_list_indexes_reports_every_named_index(tmp_path, monkeypatch):
    monkeypatch.setattr(index_store, "embed", _fake_embed)
    assert index_store.list_indexes(tmp_path) == []

    (tmp_path / "a.py").write_text("x" * 10 + "\n")
    index_store.build_index(tmp_path, "http://fake", "fake-embed", name="default")
    index_store.build_index(tmp_path, "http://fake", "fake-embed", name="alt")

    names = sorted(i["name"] for i in index_store.list_indexes(tmp_path))
    assert names == ["alt", "default"]


def test_active_index_defaults_to_default_and_can_switch(tmp_path):
    assert index_store.get_active_index_name(tmp_path) == "default"
    index_store.set_active_index_name(tmp_path, "alt")
    assert index_store.get_active_index_name(tmp_path) == "alt"


def test_delete_index_removes_file_and_falls_back_active_name_to_default(tmp_path, monkeypatch):
    monkeypatch.setattr(index_store, "embed", _fake_embed)
    (tmp_path / "a.py").write_text("x" * 10 + "\n")
    index_store.build_index(tmp_path, "http://fake", "fake-embed", name="alt")
    index_store.set_active_index_name(tmp_path, "alt")

    assert index_store.delete_index(tmp_path, "alt") is True
    assert index_store.get_active_index_name(tmp_path) == "default"
    assert index_store.index_stats(tmp_path, "alt") is None


def test_delete_index_missing_name_returns_false(tmp_path):
    assert index_store.delete_index(tmp_path, "nope") is False
