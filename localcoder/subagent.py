"""Sub-agent: a nested agentic loop the model can delegate a self-contained
investigation task to, via the spawn_subagent tool (see tools.py). Runs
against the same Ollama endpoint/model as the parent conversation, but with
its own throwaway message history and a read-only tool set — it has no
access to the parent's conversation, and (since it runs headless inside a
tool call, with nobody around to approve a write) it cannot write files or
run shell commands.
"""

from __future__ import annotations

import json

from localcoder.ollama_client import OllamaCancelled, OllamaError, chat
from localcoder.tools import execute_tool, get_tools

MAX_SUBAGENT_ROUNDS = 6

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
