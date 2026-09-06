import subprocess

import pytest

from localcoder import worktree as wt


def _init_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=path, check=True)
    return path


def _show(repo, ref, path):
    return subprocess.run(
        ["git", "show", f"{ref}:{path}"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


def test_is_git_repo(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert wt.is_git_repo(plain) is False

    repo = _init_repo(tmp_path / "repo")
    assert wt.is_git_repo(repo) is True


def test_current_branch_and_repo_root(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    assert wt.current_branch(repo) == "main"
    assert wt.repo_root(repo) == repo.resolve()


def test_add_worktree_creates_branch_and_checkout(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    wpath = tmp_path / "wt1"

    wt.add_worktree(repo, wpath, "feature-1", "main")

    assert (wpath / "README.md").read_text() == "hello\n"
    assert wt.current_branch(wpath) == "feature-1"


def test_commit_all_returns_false_when_nothing_changed(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    assert wt.commit_all(repo, "no-op") is False


def test_commit_all_commits_changes(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    (repo / "new.txt").write_text("content\n")

    assert wt.commit_all(repo, "add new.txt") is True
    assert _show(repo, "HEAD", "new.txt") == "content\n"


def test_merge_branch_merges_cleanly(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    wpath = tmp_path / "wt1"
    wt.add_worktree(repo, wpath, "feature-1", "main")
    (wpath / "feature.txt").write_text("feature work\n")
    wt.commit_all(wpath, "add feature.txt")

    wt.merge_branch(repo, "feature-1")

    assert _show(repo, "main", "feature.txt") == "feature work\n"


def test_merge_branch_conflict_raises_and_abort_merge_recovers(tmp_path):
    repo = _init_repo(tmp_path / "repo")

    # Two branches independently create the same file with different
    # content — merging the second into main must conflict.
    for name, content in [("branch-a", "from a\n"), ("branch-b", "from b\n")]:
        wpath = tmp_path / name
        wt.add_worktree(repo, wpath, name, "main")
        (wpath / "shared.txt").write_text(content)
        wt.commit_all(wpath, f"add shared.txt from {name}")

    wt.merge_branch(repo, "branch-a")
    with pytest.raises(wt.GitError):
        wt.merge_branch(repo, "branch-b")

    wt.abort_merge(repo)
    # The repo must be usable again after the abort — a second, unrelated
    # merge should succeed with no leftover conflict state.
    wpath = tmp_path / "branch-c"
    wt.add_worktree(repo, wpath, "branch-c", "main")
    (wpath / "other.txt").write_text("fine\n")
    wt.commit_all(wpath, "add other.txt")
    wt.merge_branch(repo, "branch-c")
    assert _show(repo, "main", "other.txt") == "fine\n"


def test_remove_worktree_and_delete_branch_are_best_effort(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    # Neither of these was ever created — must not raise.
    wt.remove_worktree(repo, tmp_path / "never-existed")
    wt.delete_branch(repo, "never-existed")
