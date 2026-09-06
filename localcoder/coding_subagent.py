"""Write-capable sub-agents isolated via git worktrees — spawn_coding_subagents
in tools.py. Unlike subagent.py's read-only spawn_subagent(s) (safe to run
unsupervised because they can't change anything), a coding sub-agent can
write files and run shell commands, auto-approved, without the usual
confirmation prompt — safe here specifically because each one is confined to
its own disposable branch and working copy that nothing else touches. A bad
edit's blast radius is "this one throwaway branch", not the project.

Flow: the caller's own current branch/worktree is never touched. A shared
"work" branch is created off it; each task gets its own branch off *that*
work branch plus its own worktree; every task's coding sub-agent runs in
parallel (a thread pool — dominated by waiting on Ollama, not CPU); once a
task finishes, its worktree is auto-committed (in case the model itself
never ran `git commit`) and removed. After every task is done, its branch is
merged into the work branch one at a time — sequential on purpose, so two
conflicting merges can never race each other. The work branch is left in
place for the caller to review; merging it into the caller's own branch
always goes through the ordinary run_shell tool, which still asks for
confirmation like any other write — this module never touches `base`.
"""

from __future__ import annotations

import json
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from localcoder import worktree as wt
from localcoder.background import BackgroundManager
from localcoder.ollama_client import OllamaCancelled, OllamaError, chat
from localcoder.subagent import DEFAULT_MAX_PARALLEL_SUBAGENTS, HARD_MAX_PARALLEL_SUBAGENTS
from localcoder.tools import execute_tool, get_tools

MAX_CODING_SUBAGENT_ROUNDS = 12

SYSTEM_PROMPT = (
    "You are a coding sub-agent implementing one self-contained task on your own git branch, in your own "
    "isolated working copy — nothing you do here affects any other branch or working copy. Read what you "
    "need, then make the change completely (write/edit files, run commands to verify — tests, linters — "
    "as needed). Reply with a short final summary of what you changed once done; no further tool calls "
    "after that."
)


def _non_spawning_tools(cwd: Path, index_name: str) -> list[dict]:
    # Every tool except the sub-agent spawners themselves — a coding
    # sub-agent gets the same read/write toolset as the main agent, just
    # without a confirmation gate on the write ones (see module docstring).
    # Excluding spawn_* stops one coding sub-agent from recursively forking
    # off more isolated branches of its own.
    return [t for t in get_tools(cwd, index_name) if not t["function"]["name"].startswith("spawn_")]


def _run_one_coding_subagent(task: str, worktree_path: Path, ctx: dict, cancel_event=None) -> dict:
    task_ctx = {**ctx, "cwd": worktree_path, "background": BackgroundManager()}
    tools = _non_spawning_tools(worktree_path, ctx.get("index_name", "default"))
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task},
    ]

    rounds = 0
    while True:
        rounds += 1
        if rounds > MAX_CODING_SUBAGENT_ROUNDS:
            return {"error": f"Coding sub-agent stopped after {MAX_CODING_SUBAGENT_ROUNDS} tool rounds without finishing."}

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
            return {"error": "Coding sub-agent cancelled."}
        except OllamaError as err:
            return {"error": f"Coding sub-agent failed: {err}"}

        tool_calls = result.get("tool_calls")
        if not tool_calls:
            return {"summary": result["content"]}

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

            # No confirmation gate here, unlike the main agent's tool loop —
            # this worktree is disposable and isolated (see module docstring).
            if name.startswith("spawn_"):
                payload = {"error": "Tool not available to a coding sub-agent."}
            else:
                payload = execute_tool(name, args, task_ctx)
            messages.append({"role": "tool", "tool_call_id": tool_call_id, "content": json.dumps(payload)})


def run_coding_subagents(tasks: list[str], ctx: dict, cancel_event=None) -> dict:
    repo = ctx["cwd"]
    if not tasks:
        return {"error": "No tasks given."}
    if not wt.is_git_repo(repo):
        return {"error": "Not a git repository — run `git init` (and make an initial commit) before using spawn_coding_subagents."}

    root = wt.repo_root(repo)
    base = wt.current_branch(root)
    requested_cap = ctx.get("max_subagents", DEFAULT_MAX_PARALLEL_SUBAGENTS)
    cap = max(1, min(requested_cap, HARD_MAX_PARALLEL_SUBAGENTS))
    tasks = tasks[:cap]

    work_branch = f"localcoder/graph-{uuid.uuid4().hex[:8]}"
    work_path = Path(tempfile.mkdtemp(prefix="localcoder-work-"))
    try:
        wt.add_worktree(root, work_path, work_branch, base)
    except wt.GitError as err:
        return {"error": f"Could not create work branch '{work_branch}': {err}"}

    def run_one(index: int, task: str) -> dict:
        branch = f"{work_branch}--{index}"
        task_path = Path(tempfile.mkdtemp(prefix="localcoder-task-"))
        try:
            wt.add_worktree(root, task_path, branch, work_branch)
        except wt.GitError as err:
            return {"task": task, "branch": branch, "committed": False, "error": f"Could not create branch: {err}"}

        outcome = _run_one_coding_subagent(task, task_path, ctx, cancel_event)
        try:
            committed = wt.commit_all(task_path, f"spawn_coding_subagents: {task[:72]}")
        except wt.GitError as err:
            committed = False
            outcome = {**outcome, "commit_error": str(err)}
        finally:
            wt.remove_worktree(root, task_path)

        return {"task": task, "branch": branch, "committed": committed, **outcome}

    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        results = list(pool.map(lambda pair: run_one(*pair), enumerate(tasks)))

    merged: list[str] = []
    conflicts: list[dict] = []
    for r in results:
        if not r["committed"]:
            wt.delete_branch(root, r["branch"])  # nothing to merge — the branch is empty
            continue
        try:
            wt.merge_branch(work_path, r["branch"])
            merged.append(r["branch"])
            wt.delete_branch(root, r["branch"])
        except wt.GitError as err:
            wt.abort_merge(work_path)
            conflicts.append({"branch": r["branch"], "error": str(err)})  # left in place for manual resolution

    wt.remove_worktree(root, work_path)

    return {
        "work_branch": work_branch,
        "base_branch": base,
        "merged_branches": merged,
        "conflicts": conflicts,
        "results": results,
        "note": (
            f"Committed work is on branch '{work_branch}' (created off '{base}'). It was NOT merged into "
            f"'{base}' — review/test it, then merge it yourself with run_shell, which still requires "
            "confirmation like any other write."
        ),
    }
