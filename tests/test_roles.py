from localcoder.roles import create_role, format_role, list_roles, load_role


def _setup(tmp_path):
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    (roles_dir / "code-review.md").write_text("You are in code-review mode. Do not write code, only point out issues.")
    (roles_dir / "tdd.txt").write_text("Always write a failing test before implementing.")
    return tmp_path


def test_load_role_md(tmp_path):
    cwd = _setup(tmp_path)
    role = load_role("code-review", cwd)
    assert role["name"] == "code-review"
    assert "code-review mode" in role["content"]


def test_load_role_txt(tmp_path):
    cwd = _setup(tmp_path)
    role = load_role("tdd", cwd)
    assert "failing test" in role["content"]


def test_load_role_missing(tmp_path):
    cwd = _setup(tmp_path)
    role = load_role("does-not-exist", cwd)
    assert "error" in role
    assert "not found" in role["error"]


def test_list_roles(tmp_path):
    cwd = _setup(tmp_path)
    assert list_roles(cwd) == ["code-review", "tdd"]


def test_format_role():
    assert format_role({"name": "tdd", "content": "hi"}) == "[role: tdd]\nhi"


def test_project_role_overrides_global(tmp_path, monkeypatch):
    cwd = _setup(tmp_path)
    global_dir = tmp_path / "global-home" / ".localcoder" / "roles"
    global_dir.mkdir(parents=True)
    (global_dir / "code-review.md").write_text("GLOBAL VERSION — should not win")
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "global-home")

    role = load_role("code-review", cwd)
    assert "code-review mode" in role["content"]
    assert "GLOBAL VERSION" not in role["content"]


def test_create_role_writes_a_new_file(tmp_path):
    result = create_role("pair-programmer", "Think out loud before every change.", tmp_path)
    assert "error" not in result
    assert (tmp_path / "roles" / "pair-programmer.md").exists()

    loaded = load_role("pair-programmer", tmp_path)
    assert "error" not in loaded
    assert "Think out loud" in loaded["content"]


def test_create_role_shows_up_in_list_roles(tmp_path):
    create_role("newbie", "Explain every step in detail.", tmp_path)
    assert "newbie" in list_roles(tmp_path)


def test_create_role_overwrites_an_existing_one(tmp_path):
    create_role("tdd", "first version", tmp_path)
    create_role("tdd", "second version", tmp_path)
    loaded = load_role("tdd", tmp_path)
    assert loaded["content"] == "second version"
