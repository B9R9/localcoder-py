from localcoder import graph_store


def test_build_produces_bidirectional_edges(tmp_path):
    (tmp_path / "a.py").write_text("from b import x\n")
    (tmp_path / "b.py").write_text("value = 1\n")

    result = graph_store.build_graph(tmp_path)
    assert result["fileCount"] == 2
    assert result["edgeCount"] == 1

    data = graph_store.load_graph(tmp_path)
    assert data["nodes"]["a.py"]["imports"] == ["b.py"]
    assert data["nodes"]["b.py"]["importedBy"] == ["a.py"]


def test_python_package_import_resolves_to_module(tmp_path):
    pkg = tmp_path / "pkgname"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "mod.py").write_text("value = 1\n")
    (tmp_path / "main.py").write_text("from pkgname.mod import foo\n")

    graph_store.build_graph(tmp_path)
    result = graph_store.graph_neighbors("main.py", tmp_path)
    assert result["imports"] == ["pkgname/mod.py"]


def test_python_relative_import_resolves(tmp_path):
    pkg = tmp_path / "pkgname"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "sibling.py").write_text("value = 1\n")
    (pkg / "user.py").write_text("from .sibling import x\n")

    graph_store.build_graph(tmp_path)
    result = graph_store.graph_neighbors("pkgname/user.py", tmp_path)
    assert result["imports"] == ["pkgname/sibling.py"]


def test_js_require_relative_resolves_with_extension_guessing(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "util.js").write_text("module.exports = {}\n")
    (src / "main.js").write_text("const u = require('./util')\n")

    graph_store.build_graph(tmp_path)
    result = graph_store.graph_neighbors("src/main.js", tmp_path)
    assert result["imports"] == ["src/util.js"]


def test_js_bare_specifier_is_skipped_as_external(tmp_path):
    (tmp_path / "main.js").write_text("const _ = require('lodash')\n")

    result = graph_store.build_graph(tmp_path)
    assert result["edgeCount"] == 0
    neighbors = graph_store.graph_neighbors("main.js", tmp_path)
    assert neighbors["imports"] == []


def test_named_graph_maps_coexist(tmp_path):
    (tmp_path / "a.py").write_text("from b import x\n")
    (tmp_path / "b.py").write_text("value = 1\n")

    graph_store.build_graph(tmp_path, name="default")
    (tmp_path / "a.py").write_text("value = 1\n")  # no longer imports b
    graph_store.build_graph(tmp_path, name="alt")

    assert graph_store.graph_stats(tmp_path, "default")["edgeCount"] == 1
    assert graph_store.graph_stats(tmp_path, "alt")["edgeCount"] == 0


def test_list_graph_maps_reports_every_named_graph(tmp_path):
    assert graph_store.list_graph_maps(tmp_path) == []

    (tmp_path / "a.py").write_text("x = 1\n")
    graph_store.build_graph(tmp_path, name="default")
    graph_store.build_graph(tmp_path, name="alt")

    names = sorted(g["name"] for g in graph_store.list_graph_maps(tmp_path))
    assert names == ["alt", "default"]


def test_active_graph_defaults_to_default_and_can_switch(tmp_path):
    assert graph_store.get_active_graph_name(tmp_path) == "default"
    graph_store.set_active_graph_name(tmp_path, "alt")
    assert graph_store.get_active_graph_name(tmp_path) == "alt"


def test_delete_graph_removes_file_and_falls_back_active_name_to_default(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    graph_store.build_graph(tmp_path, name="alt")
    graph_store.set_active_graph_name(tmp_path, "alt")

    assert graph_store.delete_graph(tmp_path, "alt") is True
    assert graph_store.get_active_graph_name(tmp_path) == "default"
    assert graph_store.graph_stats(tmp_path, "alt") is None


def test_delete_graph_missing_name_returns_false(tmp_path):
    assert graph_store.delete_graph(tmp_path, "nope") is False


def test_graph_neighbors_returns_both_directions(tmp_path):
    (tmp_path / "a.py").write_text("from b import x\n")
    (tmp_path / "b.py").write_text("value = 1\n")
    graph_store.build_graph(tmp_path)

    assert graph_store.graph_neighbors("a.py", tmp_path)["imports"] == ["b.py"]
    assert graph_store.graph_neighbors("b.py", tmp_path)["importedBy"] == ["a.py"]


def test_graph_neighbors_no_graph_built_yet_error(tmp_path):
    result = graph_store.graph_neighbors("a.py", tmp_path)
    assert "error" in result


def test_graph_neighbors_unknown_file_error(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    graph_store.build_graph(tmp_path)
    result = graph_store.graph_neighbors("missing.py", tmp_path)
    assert "error" in result
