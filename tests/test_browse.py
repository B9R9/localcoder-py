from localcoder.browse import browse_entries, default_browse_root


def _make_project(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth.js").write_text("")
    (tmp_path / "src" / "index.mjs").write_text("")
    (tmp_path / "src" / "components").mkdir()
    (tmp_path / "src" / "components" / "Foo.vue").write_text("")
    (tmp_path / "README.md").write_text("")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.js").write_text("")
    (tmp_path / ".git").mkdir()
    return tmp_path


def test_default_root_prefers_src(tmp_path):
    _make_project(tmp_path)
    assert default_browse_root(tmp_path) == "src"


def test_default_root_falls_back_to_source(tmp_path):
    (tmp_path / "source").mkdir()
    assert default_browse_root(tmp_path) == "source"


def test_default_root_falls_back_to_project_root(tmp_path):
    (tmp_path / "README.md").write_text("")
    assert default_browse_root(tmp_path) == ""


def test_browsing_with_no_input_starts_at_src(tmp_path):
    _make_project(tmp_path)
    entries = browse_entries(tmp_path, "")
    # directories first, node_modules/.git excluded entirely — this is the
    # project's src/ folder, not its root.
    assert entries == ["src/components/", "src/auth.js", "src/index.mjs"]


def test_browsing_descends_into_a_typed_subdirectory(tmp_path):
    _make_project(tmp_path)
    entries = browse_entries(tmp_path, "src/components/")
    assert entries == ["src/components/Foo.vue"]


def test_browsing_filters_by_prefix_within_a_directory(tmp_path):
    _make_project(tmp_path)
    entries = browse_entries(tmp_path, "src/i")
    assert entries == ["src/index.mjs"]


def test_browsing_can_climb_above_the_project_root_with_dotdot(tmp_path):
    # "contexte additionner doit etre global" — a sibling project is a
    # completely normal thing to want to pull context from.
    _make_project(tmp_path)
    sibling = tmp_path.parent / "sibling-project"
    sibling.mkdir()
    (sibling / "notes.md").write_text("")

    entries = browse_entries(tmp_path, "../")
    assert f"../{sibling.name}/" in entries


def test_browsing_an_absolute_path_reaches_anywhere_on_disk(tmp_path):
    _make_project(tmp_path)
    other = tmp_path.parent / "elsewhere"
    other.mkdir()
    (other / "config.json").write_text("{}")

    entries = browse_entries(tmp_path, f"{other}/")
    assert entries == [f"{other}/config.json"]


def test_browsing_a_home_relative_path_expands_it(tmp_path, monkeypatch):
    fake_home = tmp_path.parent / "fake-home"
    fake_home.mkdir()
    (fake_home / "dotfiles").mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    entries = browse_entries(tmp_path, "~/")
    assert "~/dotfiles/" in entries


def test_browsing_nonexistent_directory_returns_nothing(tmp_path):
    _make_project(tmp_path)
    assert browse_entries(tmp_path, "does/not/exist/") == []


def test_falls_back_to_project_root_when_no_src_or_source(tmp_path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "a.py").write_text("")
    (tmp_path / "top.txt").write_text("")
    entries = browse_entries(tmp_path, "")
    assert entries == ["lib/", "top.txt"]
