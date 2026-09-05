from localcoder.skills import create_skill, format_skill, list_skills, load_skill


def _setup(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "write-tests.md").write_text("Always add a failing test before fixing a bug.")
    (skills_dir / "commit-messages.txt").write_text("Explain why, not what, in the commit body.")
    return tmp_path


def test_load_skill_md(tmp_path):
    cwd = _setup(tmp_path)
    skill = load_skill("write-tests", cwd)
    assert skill["name"] == "write-tests"
    assert "failing test" in skill["content"]


def test_load_skill_txt(tmp_path):
    cwd = _setup(tmp_path)
    skill = load_skill("commit-messages", cwd)
    assert "Explain why" in skill["content"]


def test_load_skill_missing(tmp_path):
    cwd = _setup(tmp_path)
    skill = load_skill("does-not-exist", cwd)
    assert "error" in skill
    assert "not found" in skill["error"]


def test_list_skills(tmp_path):
    cwd = _setup(tmp_path)
    assert list_skills(cwd) == ["commit-messages", "write-tests"]


def test_format_skill():
    assert format_skill({"name": "write-tests", "content": "hi"}) == "[skill: write-tests]\nhi"


def test_project_skill_overrides_global(tmp_path, monkeypatch):
    cwd = _setup(tmp_path)
    global_dir = tmp_path / "global-home" / ".localcoder" / "skills"
    global_dir.mkdir(parents=True)
    (global_dir / "write-tests.md").write_text("GLOBAL VERSION — should not win")
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "global-home")

    skill = load_skill("write-tests", cwd)
    assert "failing test" in skill["content"]
    assert "GLOBAL VERSION" not in skill["content"]


def test_create_skill_writes_a_new_file(tmp_path):
    result = create_skill("api-design", "Prefer nouns for resources, verbs stay in HTTP methods.", tmp_path)
    assert "error" not in result
    assert (tmp_path / "skills" / "api-design.md").exists()

    loaded = load_skill("api-design", tmp_path)
    assert "error" not in loaded
    assert "nouns for resources" in loaded["content"]


def test_create_skill_shows_up_in_list_skills(tmp_path):
    create_skill("changelog", "Group entries by Added/Changed/Fixed.", tmp_path)
    assert "changelog" in list_skills(tmp_path)
