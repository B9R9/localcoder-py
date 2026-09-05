from localcoder.menu import TOP_COMMANDS, compute_menu_items

LISTS = {
    "roles": lambda: ["code-review", "tdd", "vue-quasar"],
    "sessions": lambda: ["auth-bug", "checkout-flow"],
    "models": lambda: ["devstral-small-2", "qwen3-coder:30b"],
    "skills": lambda: ["write-tests", "commit-messages"],
    # "files" is partial-aware (a directory browser, see browse.py) — it
    # takes the typed-so-far text and returns already-filtered entries,
    # unlike the other lists above which are static and filtered generically.
    "files": lambda partial: {
        "": ["src/", "README.md"],
        "src/": ["src/auth.js", "src/index.mjs"],
    }.get(partial, []),
}


def test_empty_buffer_no_menu():
    assert compute_menu_items("", LISTS) == []
    assert compute_menu_items("hello", LISTS) == []


def test_bare_slash_lists_everything():
    items = compute_menu_items("/", LISTS)
    assert len(items) == len(TOP_COMMANDS)
    assert all(i.display.startswith("/") for i in items)


def test_prefix_narrowing():
    items = compute_menu_items("/role cl", LISTS)
    assert [i.value for i in items] == ["/role clear"]
    assert items[0].submit is True


def test_role_has_use_list_create_clear():
    items = compute_menu_items("/rol", LISTS)
    assert [i.value for i in items] == ["/role use ", "/role list", "/role create ", "/role clear"]
    use_item = next(i for i in items if i.value == "/role use ")
    assert use_item.submit is False
    create_item = next(i for i in items if i.value == "/role create ")
    assert create_item.submit is False
    list_item = next(i for i in items if i.value == "/role list")
    assert list_item.submit is True


def test_leaf_command_submits_immediately():
    items = compute_menu_items("/reset", LISTS)
    assert len(items) == 1
    assert items[0].value == "/reset"
    assert items[0].submit is True


def test_free_text_command_completes_without_submit():
    items = compute_menu_items("/session save", LISTS)
    assert len(items) == 1
    assert items[0].value == "/session save "
    assert items[0].submit is False


def test_dynamic_role_picker():
    items = compute_menu_items("/role use ", LISTS)
    assert [i.value for i in items] == ["/role use code-review", "/role use tdd", "/role use vue-quasar"]
    assert all(i.submit for i in items)


def test_dynamic_role_picker_filters():
    items = compute_menu_items("/role use td", LISTS)
    assert [i.value for i in items] == ["/role use tdd"]


def test_dynamic_session_picker():
    items = compute_menu_items("/session load ", LISTS)
    assert [i.value for i in items] == ["/session load auth-bug", "/session load checkout-flow"]


def test_dynamic_skill_picker():
    items = compute_menu_items("/skill use ", LISTS)
    assert [i.value for i in items] == ["/skill use write-tests", "/skill use commit-messages"]
    assert all(i.submit for i in items)


def test_skill_has_use_list_create_clear():
    items = compute_menu_items("/skil", LISTS)
    assert [i.value for i in items] == ["/skill use ", "/skill list", "/skill create ", "/skill clear"]


def test_dynamic_file_picker_starts_at_the_browse_root():
    # No subdirectory typed yet — the getter decides the starting point
    # (browse.py roots this at src/ or source/, not necessarily cwd).
    items = compute_menu_items("/context add ", LISTS)
    assert [i.value for i in items] == ["/context add src/", "/context add README.md"]
    src_item = next(i for i in items if i.value == "/context add src/")
    assert src_item.submit is True  # a directory can also be selected as context
    file_item = next(i for i in items if i.value == "/context add README.md")
    assert file_item.submit is True  # a file — pick it


def test_dynamic_file_picker_descends_into_a_typed_directory():
    items = compute_menu_items("/context add src/", LISTS)
    assert [i.value for i in items] == ["/context add src/auth.js", "/context add src/index.mjs"]
    assert all(i.submit for i in items)


def test_hand_typed_path_with_no_matches_falls_back_to_free_text():
    items = compute_menu_items("/context add docs/adr/", LISTS)
    assert items == []


def test_missing_list_getters_degrade_gracefully():
    assert compute_menu_items("/role use x", {}) == []
    assert compute_menu_items("/context add ", {}) == []


def test_dynamic_model_picker():
    items = compute_menu_items("/model use ", LISTS)
    assert [i.value for i in items] == ["/model use devstral-small-2", "/model use qwen3-coder:30b"]
    assert all(i.submit for i in items)


def test_model_list_is_a_leaf_command():
    items = compute_menu_items("/model list", LISTS)
    assert len(items) == 1
    assert items[0].value == "/model list"
    assert items[0].submit is True


def test_set_subcommands_need_free_text():
    items = compute_menu_items("/set", LISTS)
    values = {i.value: i.submit for i in items}
    assert values == {"/set temperature ": False, "/set num_ctx ": False, "/set embed_model ": False}


def test_stats_and_verbose_are_leaf_commands():
    for cmd in ("/stats", "/verbose"):
        items = compute_menu_items(cmd, LISTS)
        assert len(items) == 1
        assert items[0].value == cmd
        assert items[0].submit is True


def test_debug_and_socratic_are_leaf_commands():
    for cmd in ("/debug", "/socratic"):
        items = compute_menu_items(cmd, LISTS)
        assert len(items) == 1
        assert items[0].value == cmd
        assert items[0].submit is True


def test_summary_search_find_present():
    names = {c["cmd"] for c in TOP_COMMANDS}
    assert {"/summary", "/search", "/find", "/role create", "/skill use", "/skill create"} <= names


def test_every_command_has_a_real_description():
    # "plus d'explication dans le menu" — every entry needs an actual
    # explanation, not just a repeat of the command name.
    for c in TOP_COMMANDS:
        assert len(c["desc"]) >= 15
        assert c["desc"].lower() != c["cmd"].lstrip("/").lower()


def test_restart_is_a_leaf_command():
    items = compute_menu_items("/restart", LISTS)
    assert len(items) == 1
    assert items[0].value == "/restart"
    assert items[0].submit is True
