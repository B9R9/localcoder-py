"""Git plumbing for spawn_coding_subagents: gives each parallel coding
sub-agent its own checked-out branch (via `git worktree`) so concurrent
sub-agents editing files never race on the same working directory. Shells
out to the system `git` binary — no GitPython dependency, consistent with
the rest of localcoder staying stdlib-only plus prompt_toolkit.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

# A fresh sandbox/worktree checkout has no git identity configured, and
# commits/merges need one — these are only ever used for localcoder's own
# throwaway automated commits, never attributed to the actual developer.
_GIT_IDENTITY = ["-c", "user.email=localcoder@localhost", "-c", "user.name=localcoder"]


class GitError(Exception):
    pass


def _git(args: list[str], cwd: Path, timeout: float = 120.0) -> str:
    try:
        result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as err:
        raise GitError(str(err)) from err
    if result.returncode != 0:
        raise GitError((result.stderr or result.stdout).strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def is_git_repo(path: Path) -> bool:
    try:
        _git(["rev-parse", "--is-inside-work-tree"], path)
        return True
    except GitError:
        return False


def repo_root(path: Path) -> Path:
    return Path(_git(["rev-parse", "--show-toplevel"], path))


def current_branch(path: Path) -> str:
    return _git(["rev-parse", "--abbrev-ref", "HEAD"], path)


def add_worktree(repo: Path, worktree_path: Path, branch_name: str, base: str) -> None:
    """Creates branch_name off base and checks it out at worktree_path — a
    separate working directory, so this never touches whatever's currently
    checked out in `repo` itself.
    """
    _git(["worktree", "add", "-b", branch_name, str(worktree_path), base], repo)


def remove_worktree(repo: Path, worktree_path: Path) -> None:
    """Best-effort: a worktree that failed half-way through creation, or was
    already removed, shouldn't block cleanup of the rest.
    """
    try:
        _git(["worktree", "remove", str(worktree_path), "--force"], repo)
    except GitError:
        pass


def delete_branch(repo: Path, branch_name: str) -> None:
    """Best-effort, same reasoning as remove_worktree — a branch that was
    never created (e.g. add_worktree itself failed) is fine to no-op on.
    """
    try:
        _git(["branch", "-D", branch_name], repo)
    except GitError:
        pass


def commit_all(worktree_path: Path, message: str) -> bool:
    """Stages and commits everything in the worktree. Returns False (not an
    error) when there's nothing to commit — a sub-agent that only
    investigated without changing anything is a normal outcome.
    """
    _git(["add", "-A"], worktree_path)
    if not _git(["status", "--porcelain"], worktree_path):
        return False
    _git([*_GIT_IDENTITY, "commit", "-q", "-m", message], worktree_path)
    return True


def merge_branch(worktree_path: Path, branch_name: str) -> None:
    """Merges branch_name into whatever's checked out at worktree_path.
    Raises GitError on a conflicting merge — callers should call
    abort_merge() and treat that branch as failed rather than leaving the
    work worktree in a conflicted state that would break the next merge.
    """
    _git(
        [*_GIT_IDENTITY, "merge", "--no-ff", "-m", f"Merge {branch_name}", branch_name],
        worktree_path,
    )


def abort_merge(worktree_path: Path) -> None:
    try:
        _git(["merge", "--abort"], worktree_path)
    except GitError:
        pass
