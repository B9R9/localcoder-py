from prompt_toolkit.formatted_text import HTML, to_formatted_text

from localcoder import ui


def _fragments(html_str: str):
    return to_formatted_text(HTML(html_str))


def test_spinner_frame_bounces_within_track():
    frames = [ui.spinner_frame(i) for i in range(30)]
    for frame in frames:
        assert frame.startswith("[") and frame.endswith("]")
        assert ui.MASCOT in frame

    # It should actually move, not just repeat the same frame forever.
    assert len(set(frames)) > 1


def test_spinner_frame_is_periodic():
    period = (ui._SPINNER_TRACK_WIDTH - 1) * 2
    assert ui.spinner_frame(0) == ui.spinner_frame(period)
    assert ui.spinner_frame(3) == ui.spinner_frame(period + 3)


def test_verbose_stats_fragments_contain_all_metrics():
    meta = {
        "total_duration": 2_000_000_000,
        "load_duration": 500_000_000,
        "prompt_eval_count": 42,
        "prompt_eval_duration": 100_000_000,
        "eval_count": 17,
        "eval_duration": 900_000_000,
    }
    joined = "\n".join(ui.verbose_stats_fragments(meta))
    for label in ("total duration", "load duration", "prompt eval count", "prompt eval rate", "eval count", "eval rate"):
        assert label in joined
    assert "42 token(s)" in joined
    assert "17 token(s)" in joined


def test_banner_fragments_show_lazzy_and_skills():
    class FakeConfig:
        model = "devstral-small-2"
        num_ctx = 8192
        temperature = 0.2
        host = "http://localhost:11434"

    fragments = ui.banner_fragments(
        FakeConfig(),
        "/tmp/project",
        "auth-bug",
        {"name": "tdd", "content": "..."},
        [{"path": "README.md", "kind": "file"}],
        None,
        True,
        skills=[{"name": "write-tests"}],
    )
    joined = "\n".join(fragments)
    assert ui.MASCOT_NAME in joined
    assert "write-tests" in joined
    assert "/role use|list|create|clear" in joined
    assert "/skill use|list|create|clear" in joined


def test_assistant_label_uses_lazzy_not_assistant():
    fragment = ui.assistant_label_fragment()
    assert f"{ui.MASCOT_NAME}&gt;" in fragment
    assert "assistant&gt;" not in fragment


def test_user_separator_is_a_visible_rule():
    frag = ui.user_separator_fragment()
    assert "─" in frag
    assert len(ui.separator()) > 10


def test_render_streamed_reply_styles_a_fenced_code_block():
    raw = "Here:\n```python\ndef add(a, b):\n    return a + b\n```\nDone."
    fragments = _fragments(ui.render_streamed_reply(raw))
    code_text = "".join(text for style, text in fragments if style == "class:code")
    assert "def add(a, b):" in code_text
    assert "python" not in code_text  # the language hint line is dropped
    prose_text = "".join(text for style, text in fragments if style != "class:code")
    assert "Here:" in prose_text and "Done." in prose_text


def test_render_streamed_reply_styles_inline_code():
    fragments = _fragments(ui.render_streamed_reply("Call `add(1, 2)` to see."))
    assert any(style == "class:code.inline" and text == "add(1, 2)" for style, text in fragments)


def test_render_streamed_reply_handles_an_unclosed_trailing_fence():
    # Mid-stream: the closing ``` hasn't arrived yet. Should still style what
    # has arrived as code instead of waiting for the fence to close.
    fragments = _fragments(ui.render_streamed_reply("```python\ndef add(a, b):\n    return"))
    code_text = "".join(text for style, text in fragments if style == "class:code")
    assert "def add(a, b):" in code_text


def test_render_streamed_reply_escapes_html_special_characters_in_code():
    fragments = _fragments(ui.render_streamed_reply("```\nif a < b && c > d:\n```"))
    code_text = "".join(text for style, text in fragments if style == "class:code")
    assert "a < b && c > d" in code_text
