import pytest

from localcoder.symbols import find_definition, find_references, has_ctags


def _fixture(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "auth.js").write_text(
        "function login(user, pass) {\n"
        "  return checkCredentials(user, pass);\n"
        "}\n\n"
        "function checkCredentials(user, pass) {\n"
        "  return user && pass;\n"
        "}\n\n"
        "module.exports = { login };\n"
    )
    (src / "app.js").write_text(
        'const { login } = require("./auth");\n\n'
        'login("bob", "secret");\n'
        'login("alice", "hunter2");\n'
    )
    nm = tmp_path / "node_modules" / "dep"
    nm.mkdir(parents=True)
    (nm / "index.js").write_text("function login() { /* should be excluded */ }\n")
    return tmp_path


@pytest.mark.skipif(not has_ctags(), reason="requires Universal Ctags installed")
def test_find_definition(tmp_path):
    cwd = _fixture(tmp_path)
    result = find_definition("login", cwd)
    assert not any("node_modules" in m["path"] for m in result["matches"])
    func_def = next((m for m in result["matches"] if m["kind"] == "function"), None)
    assert func_def is not None
    assert func_def["path"] == "src/auth.js"
    assert func_def["line"] == 1


@pytest.mark.skipif(not has_ctags(), reason="requires Universal Ctags installed")
def test_find_definition_missing(tmp_path):
    cwd = _fixture(tmp_path)
    result = find_definition("doesNotExist", cwd)
    assert result["matches"] == []
    assert "note" in result


def test_find_references(tmp_path):
    cwd = _fixture(tmp_path)
    result = find_references("login", cwd)
    lines = result["matches"].split("\n")
    assert any("src/app.js" in l and 'login("bob"' in l for l in lines)
    assert any("src/app.js" in l and 'login("alice"' in l for l in lines)
    assert "node_modules" not in result["matches"]


def test_find_references_word_boundary(tmp_path):
    cwd = _fixture(tmp_path)
    result = find_references("log", cwd)
    assert result["matches"] == "(no references found)"
