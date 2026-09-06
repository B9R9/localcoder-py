import time

from localcoder.background import BackgroundManager
from localcoder.tools import execute_tool, get_tools, needs_confirmation


def _wait_until(predicate, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _fixture(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.js").write_text("function add(a, b) { return a + b; }\n")
    (src / "b.js").write_text("function subtract(a, b) { return a - b; }\n// TODO: fix add\n")
    return tmp_path


def test_list_dir(tmp_path):
    cwd = _fixture(tmp_path)
    result = execute_tool("list_dir", {}, {"cwd": cwd})
    assert result["entries"] == ["package.json", "src/"]

    result = execute_tool("list_dir", {"path": "src"}, {"cwd": cwd})
    assert result["entries"] == ["a.js", "b.js"]


def test_search_code(tmp_path):
    cwd = _fixture(tmp_path)
    result = execute_tool("search_code", {"pattern": "fix add"}, {"cwd": cwd})
    assert "src/b.js" in result["matches"]

    result = execute_tool("search_code", {"pattern": "nope-not-here"}, {"cwd": cwd})
    assert result["matches"] == "(no matches)"


def test_search_code_excludes_node_modules(tmp_path):
    cwd = _fixture(tmp_path)
    nm = cwd / "node_modules" / "dep"
    nm.mkdir(parents=True)
    (nm / "index.js").write_text("function add() { /* should be excluded */ }\n")

    result = execute_tool("search_code", {"pattern": "add"}, {"cwd": cwd})
    assert "node_modules" not in result["matches"]


def test_read_file_rejects_path_outside_project(tmp_path):
    cwd = _fixture(tmp_path)
    secret = tmp_path.parent / "secret.txt"
    secret.write_text("top secret")

    result = execute_tool("read_file", {"path": "../secret.txt"}, {"cwd": cwd})
    assert "error" in result
    assert "escapes" in result["error"]


def test_write_file_rejects_path_outside_project(tmp_path):
    cwd = _fixture(tmp_path)
    result = execute_tool("write_file", {"path": "../escaped.txt", "content": "x"}, {"cwd": cwd})
    assert "error" in result
    assert not (tmp_path.parent / "escaped.txt").exists()


def test_edit_file_rejects_path_outside_project(tmp_path):
    cwd = _fixture(tmp_path)
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("hello")

    result = execute_tool(
        "edit_file", {"path": "../outside.txt", "old_string": "hello", "new_string": "hacked"}, {"cwd": cwd}
    )
    assert "error" in result
    assert outside.read_text() == "hello"


def test_list_dir_rejects_path_outside_project(tmp_path):
    cwd = _fixture(tmp_path)
    result = execute_tool("list_dir", {"path": ".."}, {"cwd": cwd})
    assert "error" in result


def test_search_code_rejects_path_outside_project(tmp_path):
    cwd = _fixture(tmp_path)
    result = execute_tool("search_code", {"pattern": "x", "path": ".."}, {"cwd": cwd})
    assert "error" in result


# search_code's ripgrep-less fallback used to shell out to the system `grep`
# with `--exclude-dir` — a GNU-only flag that silently breaks on macOS's
# bundled grep ("search option ne semble pas fonctionner"). It's now a
# pure-Python walk-and-regex-search instead, exercised directly here (these
# assertions mirror the ripgrep-path tests above) so its correctness doesn't
# depend on whether `rg` happens to be installed wherever the suite runs.
def test_fallback_search_finds_matches_and_reports_no_matches(tmp_path, monkeypatch):
    monkeypatch.setattr("localcoder.tools._has_ripgrep", lambda: False)
    cwd = _fixture(tmp_path)

    result = execute_tool("search_code", {"pattern": "fix add"}, {"cwd": cwd})
    assert "src/b.js" in result["matches"]

    result = execute_tool("search_code", {"pattern": "nope-not-here"}, {"cwd": cwd})
    assert result["matches"] == "(no matches)"


def test_fallback_search_excludes_node_modules_and_dotfiles(tmp_path, monkeypatch):
    monkeypatch.setattr("localcoder.tools._has_ripgrep", lambda: False)
    cwd = _fixture(tmp_path)
    nm = cwd / "node_modules" / "dep"
    nm.mkdir(parents=True)
    (nm / "index.js").write_text("function add() { /* should be excluded */ }\n")
    (cwd / ".git").mkdir()
    (cwd / ".git" / "config").write_text("add\n")

    result = execute_tool("search_code", {"pattern": "add"}, {"cwd": cwd})
    assert "node_modules" not in result["matches"]
    assert ".git" not in result["matches"]


def test_fallback_search_is_smart_case_like_ripgrep(tmp_path, monkeypatch):
    monkeypatch.setattr("localcoder.tools._has_ripgrep", lambda: False)
    cwd = _fixture(tmp_path)

    # All-lowercase pattern -> case-insensitive, matches "TODO" (b.js) too.
    result = execute_tool("search_code", {"pattern": "todo"}, {"cwd": cwd})
    assert "src/b.js" in result["matches"]

    # A pattern with an uppercase letter -> case-sensitive, no match against
    # the lowercase "add" actually in the file.
    result = execute_tool("search_code", {"pattern": "Add"}, {"cwd": cwd})
    assert result["matches"] == "(no matches)"


def test_fallback_search_supports_regex(tmp_path, monkeypatch):
    monkeypatch.setattr("localcoder.tools._has_ripgrep", lambda: False)
    cwd = _fixture(tmp_path)

    result = execute_tool("search_code", {"pattern": r"\bsubtract\b"}, {"cwd": cwd})
    assert "src/b.js" in result["matches"]


def test_fallback_search_reports_an_invalid_pattern(tmp_path, monkeypatch):
    monkeypatch.setattr("localcoder.tools._has_ripgrep", lambda: False)
    cwd = _fixture(tmp_path)

    result = execute_tool("search_code", {"pattern": "("}, {"cwd": cwd})
    assert "error" in result


def test_fallback_search_skips_binary_files(tmp_path, monkeypatch):
    monkeypatch.setattr("localcoder.tools._has_ripgrep", lambda: False)
    cwd = _fixture(tmp_path)
    (cwd / "blob.bin").write_bytes(b"\xff\xfe\x00add\x00")

    result = execute_tool("search_code", {"pattern": "add"}, {"cwd": cwd})
    assert "blob.bin" not in result["matches"]


def test_edit_file(tmp_path):
    cwd = _fixture(tmp_path)
    result = execute_tool(
        "edit_file",
        {"path": "src/a.js", "old_string": "a + b", "new_string": "a + b /* patched */"},
        {"cwd": cwd},
    )
    assert result == {"ok": True}
    assert "patched" in (cwd / "src" / "a.js").read_text()


def test_edit_file_not_found_string(tmp_path):
    cwd = _fixture(tmp_path)
    result = execute_tool("edit_file", {"path": "src/a.js", "old_string": "nope", "new_string": "x"}, {"cwd": cwd})
    assert "error" in result
    assert "not found" in result["error"]


def test_edit_file_ambiguous(tmp_path):
    cwd = _fixture(tmp_path)
    (cwd / "dup.js").write_text("x\nx\n")
    result = execute_tool("edit_file", {"path": "dup.js", "old_string": "x", "new_string": "y"}, {"cwd": cwd})
    assert "matches 2 times" in result["error"]


def test_write_file_creates_parent_dirs(tmp_path):
    cwd = _fixture(tmp_path)
    result = execute_tool("write_file", {"path": "new/dir/hello.txt", "content": "hi"}, {"cwd": cwd})
    assert result["ok"] is True
    assert (cwd / "new" / "dir" / "hello.txt").read_text() == "hi"


def test_needs_confirmation():
    assert needs_confirmation("write_file") is True
    assert needs_confirmation("edit_file") is True
    assert needs_confirmation("run_shell") is True
    assert needs_confirmation("run_shell_background") is True
    assert needs_confirmation("stop_background_task") is True
    assert needs_confirmation("read_file") is False
    assert needs_confirmation("search_code") is False
    assert needs_confirmation("list_background_tasks") is False
    assert needs_confirmation("get_background_output") is False


def test_run_shell_background_dispatch(tmp_path):
    ctx = {"cwd": tmp_path, "background": BackgroundManager()}
    started = execute_tool("run_shell_background", {"command": "echo hi"}, ctx)
    assert started["started"] is True
    task_id = started["id"]

    listed = execute_tool("list_background_tasks", {}, ctx)
    assert listed["tasks"][0]["id"] == task_id

    assert _wait_until(lambda: not ctx["background"].output(task_id)["running"])
    output = execute_tool("get_background_output", {"id": task_id}, ctx)
    assert "hi" in output["stdout"]

    stopped = execute_tool("stop_background_task", {"id": task_id}, ctx)
    assert stopped.get("note") == "already finished"


def test_get_tools_no_index_no_ctags(tmp_path, monkeypatch):
    # Force both optional prerequisites off, regardless of what's actually
    # installed on the machine running the tests.
    monkeypatch.setattr("localcoder.tools.has_ctags", lambda: False)
    names = [t["function"]["name"] for t in get_tools(tmp_path)]
    assert names == [
        "read_file",
        "list_dir",
        "search_code",
        "edit_file",
        "write_file",
        "run_shell",
        "run_shell_background",
        "list_background_tasks",
        "get_background_output",
        "stop_background_task",
        "spawn_subagent",
    ]


def test_get_tools_advertises_symbol_tools_when_ctags_present(tmp_path, monkeypatch):
    monkeypatch.setattr("localcoder.tools.has_ctags", lambda: True)
    names = [t["function"]["name"] for t in get_tools(tmp_path)]
    assert "find_definition" in names
    assert "find_references" in names
    assert "semantic_search" not in names  # no index built for this tmp_path
