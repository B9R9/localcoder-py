"""Sub-agent: a nested agentic loop the model can delegate a self-contained
investigation task to, via the spawn_subagent/spawn_subagents tools (see
tools.py). Runs against the same Ollama endpoint/model as the parent
conversation, but with its own throwaway message history and a read-only
tool set — it has no access to the parent's conversation, and (since it runs
headless inside a tool call, with nobody around to approve a write) it
cannot write files or run shell commands.

Two shapes are offered to the model: `spawn_subagent` (one task, "loop"
style — the same straight sequential round-trip as the main agent's own
tool loop) and `spawn_subagents` (several independent tasks at once, "graph"
style — split into parallel branches that each finalize on their own, then
report back to the caller to synthesize). Both share run_subagent() below;
run_subagents() just fans it out over a thread pool, since each branch is
dominated by waiting on Ollama/network I/O rather than CPU.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from localcoder.ollama_client import OllamaCancelled, OllamaError, chat
from localcoder.tools import execute_tool, get_tools

MAX_SUBAGENT_ROUNDS = 6

# Caps how many branches spawn_subagents fans out to at once — a runaway
# model call asking for dozens of branches would otherwise flood Ollama
# with concurrent requests for no real benefit (a single local model
# instance serializes them anyway).
MAX_PARALLEL_SUBAGENTS = 4

SYSTEM_PROMPT = (
    "You are a sub-agent investigating one self-contained task on behalf of another agent. "
    "You have read-only tools (no file writes, no shell commands). Use them to gather what you "
    "need, then reply with a concise final answer summarizing what you found — no further tool "
    "calls once you have enough to answer."
)

# Only the read-only tools from tools.py are ever offered to a sub-agent —
# write_file/edit_file/run_shell(_background) all need a human to approve
# them, and no human is watching a sub-agent's tool calls.
READ_ONLY_TOOLS = {"read_file", "list_dir", "search_code", "semantic_search", "find_definition", "find_references"}


def run_subagent(task: str, ctx: dict, cancel_event=None) -> dict:
    tools = [t for t in get_tools(ctx["cwd"], ctx.get("index_name", "default")) if t["function"]["name"] in READ_ONLY_TOOLS]
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task},
    ]

    rounds = 0
    while True:
        rounds += 1
        if rounds > MAX_SUBAGENT_ROUNDS:
            return {"error": f"Sub-agent stopped after {MAX_SUBAGENT_ROUNDS} tool rounds without a final answer."}

        try:
            result = chat(
                host=ctx["host"],
                model=ctx["model"],
                messages=messages,
                tools=tools,
                num_ctx=ctx["num_ctx"],
                temperature=ctx.get("temperature", 0.2),
                cancel_event=cancel_event,
            )
        except OllamaCancelled:
            return {"error": "Sub-agent cancelled."}
        except OllamaError as err:
            return {"error": f"Sub-agent failed: {err}"}

        tool_calls = result.get("tool_calls")
        if not tool_calls:
            return {"result": result["content"]}

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
            tool_call_id = call.get("id") or f"{name}_{rounds}_{i}"

            payload = execute_tool(name, args, ctx) if name in READ_ONLY_TOOLS else {"error": "Tool not available to sub-agents."}
            messages.append({"role": "tool", "tool_call_id": tool_call_id, "content": json.dumps(payload)})


def run_subagents(tasks: list[str], ctx: dict, cancel_event=None) -> dict:
    """The "graph" fan-out: runs each task as its own independent
    run_subagent() branch, concurrently, and returns every branch's outcome
    for the caller to combine — as opposed to spawn_subagent's single
    sequential ("loop") branch. Branches don't share state (each gets its
    own message history) and errors in one don't cancel the others.
    """
    if not tasks:
        return {"error": "No tasks given."}
    tasks = tasks[:MAX_PARALLEL_SUBAGENTS]

    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = [pool.submit(run_subagent, task, ctx, cancel_event) for task in tasks]
        outcomes = [future.result() for future in futures]

    return {"results": [{"task": task, **outcome} for task, outcome in zip(tasks, outcomes)]}
