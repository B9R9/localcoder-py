from localcoder.context import (
    format_context_entry,
    list_context_sets,
    list_project_files,
    load_context_entry,
    load_context_path,
    load_context_set_paths,
    save_context_set,
)


def test_file_entry(tmp_path):
    (tmp_path / "README.md").write_text("# Fixture project\nThis is a test.\n")
    entry = load_context_entry("README.md", tmp_path)
    assert entry == {"path": "README.md", "kind": "file", "content": "# Fixture project\nThis is a test.\n"}


def test_dir_entry(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth.js").write_text("x")
    (tmp_path / "README.md").write_text("x")
    entry = load_context_entry(".", tmp_path)
    assert entry["kind"] == "dir"
    assert "README.md" in entry["content"]
    assert "src/" in entry["content"]
    assert "src/auth.js" in entry["content"]


def test_missing_path(tmp_path):
    entry = load_context_entry("does-not-exist.txt", tmp_path)
    assert entry["kind"] == "error"
    assert "not found" in entry["content"]


def test_glob_expansion(tmp_path):
    adr = tmp_path / "docs" / "adr"
    adr.mkdir(parents=True)
    (adr / "0001-use-postgres.md").write_text("a")
    (adr / "0002-no-orm.md").write_text("b")
    (adr / "README.md").write_text("c")

    entries = load_context_path("docs/adr/*.md", tmp_path)
    paths = sorted(e["path"] for e in entries)
    assert paths == ["docs/adr/0001-use-postgres.md", "docs/adr/0002-no-orm.md", "docs/adr/README.md"]
    assert all(e["kind"] == "file" for e in entries)


def test_glob_no_match(tmp_path):
    adr = tmp_path / "docs" / "adr"
    adr.mkdir(parents=True)
    entries = load_context_path("docs/adr/*.rst", tmp_path)
    assert len(entries) == 1
    assert entries[0]["kind"] == "error"
    assert "No files matched" in entries[0]["content"]


def test_format_context_entry():
    file_entry = {"path": "a.md", "kind": "file", "content": "hi"}
    assert format_context_entry(file_entry) == "[context: a.md]\nhi"
    dir_entry = {"path": ".", "kind": "dir", "content": "a\nb"}
    assert "file tree" in format_context_entry(dir_entry)
    err_entry = {"path": "x", "kind": "error", "content": "boom"}
    assert format_context_entry(err_entry) == "[context: x] boom"


def test_list_project_files_excludes_ignored_dirs(tmp_path):
    (tmp_path / "node_modules" / "dep").mkdir(parents=True)
    (tmp_path / "node_modules" / "dep" / "index.js").write_text("x")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.js").write_text("x")

    files = list_project_files(tmp_path)
    assert "src/app.js" in files
    assert not any("node_modules" in f for f in files)


def test_named_context_set_round_trip(tmp_path):
    assert load_context_set_paths(tmp_path, "adr") is None
    assert list_context_sets(tmp_path) == []

    save_context_set(tmp_path, "adr", ["docs/adr/*.md", "README.md"])

    assert load_context_set_paths(tmp_path, "adr") == ["docs/adr/*.md", "README.md"]
    assert list_context_sets(tmp_path) == ["adr"]


def test_saving_a_context_set_again_overwrites_it(tmp_path):
    save_context_set(tmp_path, "adr", ["README.md"])
    save_context_set(tmp_path, "adr", ["docs/"])

    assert load_context_set_paths(tmp_path, "adr") == ["docs/"]
    assert list_context_sets(tmp_path) == ["adr"]
