"""prompt_toolkit wiring for the interactive "/" menu: a Completer that
delegates to menu.compute_menu_items, plus key bindings so a "submit" item
(one with nothing left to type — /reset, /role list, a picked role name...)
runs on a single Enter, while a "complete-only" item (/role use, /session
save...) just fills the buffer and keeps editing. Only used when stdin is a
real TTY — repl.py falls back to plain input() otherwise (piped/non-
interactive stdin: scripts, tests), so nothing here needs to handle that.
"""

from __future__ import annotations

from typing import Callable

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.key_binding import KeyBindings

from localcoder.menu import compute_menu_items
from localcoder.ui import THEME


class MenuCompleter(Completer):
    """Wraps compute_menu_items() as a prompt_toolkit Completer, remembering
    each item's `submit` flag by its resulting text so the Enter binding
    below can look it up without recomputing the whole list.
    """

    def __init__(self, items_fn: Callable[[str], list]):
        self.items_fn = items_fn
        self._submit_by_value: dict[str, bool] = {}

    def get_completions(self, document, complete_event):
        buffer = document.text_before_cursor
        items = self.items_fn(buffer)
        self._submit_by_value = {item.value: item.submit for item in items}
        for item in items:
            yield Completion(text=item.value, start_position=-len(buffer), display=item.display)

    def submits(self, value: str) -> bool:
        return self._submit_by_value.get(value, False)


def make_key_bindings(completer: MenuCompleter) -> KeyBindings:
    kb = KeyBindings()

    @kb.add("enter")
    def _accept_or_complete(event):
        buf = event.current_buffer
        state = buf.complete_state
        if state is not None and state.current_completion is not None:
            # A completion is highlighted — its text is already live-previewed
            # into buf.text by prompt_toolkit's default arrow-navigation.
            if completer.submits(buf.text):
                buf.cancel_completion()
                buf.validate_and_handle()
            else:
                buf.cancel_completion()
            return
        buf.validate_and_handle()

    @kb.add("escape")
    def _clear(event):
        buf = event.current_buffer
        buf.cancel_completion()
        buf.reset()

    return kb


def build_line_session(items_fn: Callable[[str], list], get_toolbar=None) -> PromptSession:
    completer = MenuCompleter(items_fn)
    return PromptSession(
        completer=completer,
        complete_while_typing=True,
        key_bindings=make_key_bindings(completer),
        style=THEME,
        bottom_toolbar=get_toolbar,
    )
