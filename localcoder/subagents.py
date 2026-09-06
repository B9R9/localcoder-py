"""Graph mode: split a request into independent subtasks, run each in its own
sub-agent — its own isolated conversation and tool-call loop — in a separate
thread, then combine every sub-agent's result into one final answer.

Two guards make running several tool-calling loops at once safe instead of
just convenient:

- FileLockRegistry serializes write_file/edit_file on a given path, so two
  sub-agents editing the same file don't race each other — different paths
  stay fully parallel.
- ui_lock serializes every self.out.* call AND every confirm_fn call made
  from a sub-agent thread. This isn't just cosmetic: the full-screen UI's
  confirm_fn (fullscreen.py) stores the pending question in a single shared
  attribute — two threads calling it at once would let the second call's
  question clobber the first's before the first ever gets answered, hanging
  that thread forever. Serializing through one lock means only one sub-agent
  is ever "talking to the terminal" at a time, even though model calls and
  tool execution for different sub-agents still run concurrently.
"""

from __future__ import annotations

import json
import re
import threading
from contextlib import contextmanager
from pathlib import Path

from localcoder.index_store import get_active_index_name
from localcoder.provider_errors import ProviderCancelled, ProviderError, ProviderToolsUnsupported
from localcoder.providers import chat
from localcoder.tools import execute_tool, extract_fallback_tool_call, get_tools, needs_confirmation

MAX_SUBTASKS = 5
MAX_SUBAGENT_ROUNDS = 6

SUBAGENT_SYSTEM_PROMPT = (
    "You are one of several sub-agents working in parallel, each on one slice of a larger request. "
    "Focus only on your assigned subtask below — use the available tools to actually do the work "
    "(read/search/edit/write/run as needed), then reply with a short summary of what you did."
)

DECOMPOSE_PROMPT = (
    f"Split the user's request into 2 to {MAX_SUBTASKS} independent subtasks that could be worked on "
    "separately. Where possible, keep subtasks focused on different files to avoid conflicts. "
    "Reply with ONLY a JSON array of strings, each a short, self-contained subtask description — "
    "no markdown fences, no other text. If the request is a single simple step that shouldn't be "
    "split, reply with a JSON array containing just that one string."
)

AGGREGATE_PROMPT = (
    "Several sub-agents each worked on one part of the user's original request, shown below with "
    "their results (or errors). Combine them into one clear, coherent final answer for the user — "
    "call out anything that failed or is still missing."
)

_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


def _parse_subtask_list(text: str) -> list[str] | None:
    match = _JSON_ARRAY_RE.search(text)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list):
        return None
    subtasks = [str(item).strip() for item in data if str(item).strip()]
    return subtasks[:MAX_SUBTASKS] if subtasks else None


class FileLockRegistry:
    """{path: Lock} — write_file/edit_file on the same path blocks until the
    previous writer is done; different paths stay fully parallel."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}

    def _lock_for(self, path: str) -> threading.Lock:
        with self._lock:
            return self._locks.setdefault(path, threading.Lock())

    @contextmanager
    def guard(self, path: str):
        lock = self._lock_for(str(Path(path)))
        lock.acquire()
        try:
            yield
        finally:
            lock.release()


def run_subagent(
    index: int,
    task_description: str,
    *,
    config,
    cwd,
    host,
    embed_model,
    index_name,
    background,
    tools_supported: bool,
    plan_mode: bool,
    confirm_fn,
    ui_lock: threading.Lock,
    file_locks: FileLockRegistry,
    out,
    cancel_event=None,
) -> dict:
    label = f"[graph #{index}]"
    with ui_lock:
        out.info(f"{label} starting: {task_description}")

    messages = [
        {"role": "system", "content": SUBAGENT_SYSTEM_PROMPT},
        {"role": "user", "content": task_description},
    ]
    tools = get_tools(cwd, index_name) if tools_supported else []
    tool_ctx = {"cwd": cwd, "host": host, "embed_model": embed_model, "index_name": index_name, "background": background}

    result_text = ""
    error = None

    for _ in range(MAX_SUBAGENT_ROUNDS):
        try:
            result = chat(config=config, messages=messages, tools=tools, on_token=None, cancel_event=cancel_event)
        except ProviderToolsUnsupported:
            tools = []
            try:
                result = chat(config=config, messages=messages, tools=[], on_token=None, cancel_event=cancel_event)
            except (ProviderCancelled, ProviderError) as e:
                error = str(e)
                break
        except (ProviderCancelled, ProviderError) as e:
            error = str(e)
            break

        tool_calls = result.get("tool_calls")
        if not tool_calls:
            fallback = extract_fallback_tool_call(result.get("content") or "")
            if fallback is None:
                result_text = result["content"]
                messages.append({"role": "assistant", "content": result_text})
                break
            tool_calls = [{"id": None, "function": {"name": fallback["name"], "arguments": fallback["arguments"]}}]
            messages.append({"role": "assistant", "content": "", "tool_calls": tool_calls})
        else:
            result_text = result.get("content") or result_text
            messages.append({"role": "assistant", "content": result["content"], "tool_calls": tool_calls})

        for i, call in enumerate(tool_calls):
            fn = call.get("function") or {}
            name = fn.get("name")
            raw_args = fn.get("arguments")
            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args)
                except json.JSONDecodeError:
                    args = {}
            else:
                args = raw_args or {}
            tool_call_id = call.get("id") or f"sub{index}_{name}_{i}"

            with ui_lock:
                out.tool_call(f"{label} {name}", args)

            if plan_mode and needs_confirmation(name):
                result_payload = {"error": "Plan mode is active — write actions are blocked until the user turns it off."}
            elif needs_confirmation(name):
                with ui_lock:
                    approved = confirm_fn(f"{label} Approve this action?")
                if not approved:
                    result_payload = {"error": "User declined this action."}
                elif name in ("write_file", "edit_file"):
                    with file_locks.guard(args.get("path", "")):
                        result_payload = execute_tool(name, args, tool_ctx)
                else:
                    result_payload = execute_tool(name, args, tool_ctx)
            else:
                result_payload = execute_tool(name, args, tool_ctx)

            with ui_lock:
                out.tool_result(result_payload)

            messages.append({"role": "tool", "tool_call_id": tool_call_id, "content": json.dumps(result_payload)})
    else:
        # Exhausted MAX_SUBAGENT_ROUNDS without a plain-text final reply.
        if not result_text:
            result_text = "[hit the sub-agent round limit before finishing]"

    with ui_lock:
        out.info(f"{label} done." if error is None else f"{label} failed: {error}")

    return {"index": index, "task": task_description, "result": result_text, "error": error}


def run_graph_turn(app, confirm_fn, cancel_event=None, on_response=None) -> None:
    task_text = app.conversation[-1]["content"]

    try:
        decompose_result = chat(
            config=app.config,
            messages=[
                {"role": "system", "content": DECOMPOSE_PROMPT},
                {"role": "user", "content": task_text},
            ],
            tools=[],
            on_token=None,
            cancel_event=cancel_event,
        )
    except (ProviderCancelled, ProviderError, ProviderToolsUnsupported) as e:
        app.out.err(app.format_error(e))
        return

    subtasks = _parse_subtask_list(decompose_result.get("content") or "")

    if not subtasks or len(subtasks) < 2:
        app.out.info("[graph mode] Task doesn't split cleanly into independent parts — running as a single agent instead.")
        app.run_turn(confirm_fn, cancel_event=cancel_event, on_response=on_response)
        return

    app.out.info(
        f"[graph mode] Split into {len(subtasks)} sub-agents:\n" + "\n".join(f"  {i + 1}. {t}" for i, t in enumerate(subtasks))
    )

    index_name = get_active_index_name(app.cwd)
    ui_lock = threading.Lock()
    file_locks = FileLockRegistry()
    results: list[dict | None] = [None] * len(subtasks)

    def worker(i: int, task: str) -> None:
        results[i] = run_subagent(
            i + 1,
            task,
            config=app.config,
            cwd=app.cwd,
            host=app.config.host,
            embed_model=app.config.embed_model,
            index_name=index_name,
            background=app.background,
            tools_supported=app.tools_supported,
            plan_mode=app.plan_mode,
            confirm_fn=confirm_fn,
            ui_lock=ui_lock,
            file_locks=file_locks,
            out=app.out,
            cancel_event=cancel_event,
        )

    threads = [threading.Thread(target=worker, args=(i, t)) for i, t in enumerate(subtasks)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    blocks = []
    for r in results:
        if r["error"]:
            blocks.append(f"Subtask {r['index']}: {r['task']}\nERROR: {r['error']}")
        else:
            blocks.append(f"Subtask {r['index']}: {r['task']}\nResult: {r['result']}")

    aggregate_messages = [
        {"role": "system", "content": AGGREGATE_PROMPT},
        {"role": "user", "content": f"Original request: {task_text}\n\n" + "\n\n".join(blocks)},
    ]

    app.out.assistant_label()
    try:
        final = chat(
            config=app.config,
            messages=aggregate_messages,
            tools=[],
            on_token=app.out.token,
            cancel_event=cancel_event,
            on_response=on_response,
        )
    except (ProviderCancelled, ProviderError, ProviderToolsUnsupported) as e:
        app.out.newline()
        app.out.err(app.format_error(e))
        return
    app.out.newline()

    meta = final.get("done_meta")
    if meta:
        app.stats["turns"] += 1
        app.stats["prompt_tokens"] += meta.get("prompt_eval_count") or 0
        app.stats["eval_tokens"] += meta.get("eval_count") or 0
        app.stats["total_ns"] += meta.get("total_duration") or 0
        app.last_prompt_tokens = meta.get("prompt_eval_count") or app.last_prompt_tokens
        if app.verbose:
            app.out.verbose_stats(meta)

    app.conversation.append({"role": "assistant", "content": final["content"]})
