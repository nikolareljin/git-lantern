import subprocess

from lantern import git


def test_get_working_tree_state_reports_clean(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
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
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
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
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
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
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
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


def test_get_branch_maps_literal_head_to_none(monkeypatch):
    monkeypatch.setattr(
        git,
        "run_git",
        lambda _path, args: "HEAD" if args == ["rev-parse", "--abbrev-ref", "HEAD"] else "",
    )
    assert git.get_branch("/fake/repo") is None


def test_repo_status_infers_upstream_from_origin_branch_when_no_tracking_branch(monkeypatch):
    def fake_run_git(_path, args):
        if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
            return "main"
        if args[:3] == ["rev-parse", "--abbrev-ref", "--symbolic-full-name"]:
            return ""  # no @{u}
        if args[:2] == ["rev-parse", "--verify"] and args[2] == "origin/main":
            return "deadbeef"
        if args[:3] == ["rev-list", "--left-right", "--count"]:
            return "0 3"
        if args == ["remote"]:
            return "origin"
        if args[:2] == ["symbolic-ref", "-q"]:
            return "origin/main"
        return ""

    monkeypatch.setattr(git, "run_git", fake_run_git)
    status = git.repo_status("/fake/repo")

    assert status["upstream"] is None
    assert status["upstream_inferred"] is True
    assert status["upstream_ahead"] == "0"
    assert status["upstream_behind"] == "3"


def test_repo_status_skips_origin_inference_for_detached_head(monkeypatch):
    tried = []

    def fake_run_git(_path, args):
        tried.append(list(args))
        if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
            return "HEAD"  # detached — get_branch() maps this to None
        if args[:3] == ["rev-parse", "--abbrev-ref", "--symbolic-full-name"]:
            return ""
        if args == ["remote"]:
            return ""
        return ""

    monkeypatch.setattr(git, "run_git", fake_run_git)
    status = git.repo_status("/fake/repo")

    assert all(args[:2] != ["rev-parse", "--verify"] for args in tried), (
        "Should not attempt origin/<branch> inference for detached HEAD"
    )
    assert status["branch"] is None
    assert status["upstream"] is None
    assert status["upstream_inferred"] is False
    assert status["upstream_ahead"] is None
    assert status["upstream_behind"] is None
