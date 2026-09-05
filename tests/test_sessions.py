from localcoder.sessions import list_sessions, load_session, save_session


def test_save_and_load_roundtrip(tmp_path):
    save_session("auth-bug", "code-review", ["docs/adr/0001-x.md"], [{"role": "user", "content": "hi"}], tmp_path)
    loaded = load_session("auth-bug", tmp_path)
    assert loaded["name"] == "auth-bug"
    assert loaded["role"] == "code-review"
    assert loaded["contextPaths"] == ["docs/adr/0001-x.md"]
    assert loaded["conversation"] == [{"role": "user", "content": "hi"}]
    assert "updatedAt" in loaded


def test_load_missing_session_returns_none(tmp_path):
    assert load_session("nope", tmp_path) is None


def test_load_corrupt_session_returns_error(tmp_path):
    sessions_dir = tmp_path / ".localcoder" / "sessions"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / "broken.json").write_text("{not valid json")
    result = load_session("broken", tmp_path)
    assert "error" in result


def test_list_sessions(tmp_path):
    save_session("b", None, [], [], tmp_path)
    save_session("a", None, [], [], tmp_path)
    assert list_sessions(tmp_path) == ["a", "b"]


def test_list_sessions_empty_when_no_dir(tmp_path):
    assert list_sessions(tmp_path) == []


def test_creates_dir_on_first_save(tmp_path):
    assert not (tmp_path / ".localcoder").exists()
    save_session("first", None, [], [], tmp_path)
    assert (tmp_path / ".localcoder" / "sessions" / "first.json").exists()
