import subprocess

from lantern import git


def test_get_working_tree_state_reports_clean(monkeypatch):
    monkeypatch.setattr(
        git,
        "_run_git_capture",
        lambda _repo_path, _args: subprocess.CompletedProcess(
            args=["git", "status", "--porcelain"],
            returncode=0,
            stdout="",
            stderr="",
        ),
    )

    state = git.get_working_tree_state("/tmp/repo")

    assert state == {
        "status_ok": True,
        "is_clean": True,
        "has_untracked": False,
        "has_tracked_changes": False,
        "allows_checkout_latest": True,
        "error": "",
    }


def test_get_working_tree_state_allows_untracked_only(monkeypatch):
    monkeypatch.setattr(
        git,
        "_run_git_capture",
        lambda _repo_path, _args: subprocess.CompletedProcess(
            args=["git", "status", "--porcelain"],
            returncode=0,
            stdout="?? notes.txt\n?? tmp/cache.json",
            stderr="",
        ),
    )

    state = git.get_working_tree_state("/tmp/repo")

    assert state == {
        "status_ok": True,
        "is_clean": False,
        "has_untracked": True,
        "has_tracked_changes": False,
        "allows_checkout_latest": True,
        "error": "",
    }


def test_get_working_tree_state_blocks_tracked_changes(monkeypatch):
    monkeypatch.setattr(
        git,
        "_run_git_capture",
        lambda _repo_path, _args: subprocess.CompletedProcess(
            args=["git", "status", "--porcelain"],
            returncode=0,
            stdout=" M src/lantern/cli.py\n?? notes.txt",
            stderr="",
        ),
    )

    state = git.get_working_tree_state("/tmp/repo")

    assert state == {
        "status_ok": True,
        "is_clean": False,
        "has_untracked": True,
        "has_tracked_changes": True,
        "allows_checkout_latest": False,
        "error": "",
    }


def test_get_working_tree_state_treats_git_failures_as_unsafe(monkeypatch):
    monkeypatch.setattr(
        git,
        "_run_git_capture",
        lambda _repo_path, _args: subprocess.CompletedProcess(
            args=["git", "status", "--porcelain"],
            returncode=128,
            stdout="",
            stderr="fatal",
        ),
    )

    state = git.get_working_tree_state("/tmp/repo")

    assert state == {
        "status_ok": False,
        "is_clean": False,
        "has_untracked": False,
        "has_tracked_changes": False,
        "allows_checkout_latest": None,
        "error": "git status failed; fatal; exit=128",
    }
