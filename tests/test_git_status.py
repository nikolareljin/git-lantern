from lantern import git


def test_get_working_tree_state_reports_clean(monkeypatch):
    monkeypatch.setattr(git, "run_git", lambda _repo_path, _args: "")

    state = git.get_working_tree_state("/tmp/repo")

    assert state == {
        "is_clean": True,
        "has_untracked": False,
        "has_tracked_changes": False,
        "allows_checkout_latest": True,
    }


def test_get_working_tree_state_allows_untracked_only(monkeypatch):
    monkeypatch.setattr(git, "run_git", lambda _repo_path, _args: "?? notes.txt\n?? tmp/cache.json")

    state = git.get_working_tree_state("/tmp/repo")

    assert state == {
        "is_clean": False,
        "has_untracked": True,
        "has_tracked_changes": False,
        "allows_checkout_latest": True,
    }


def test_get_working_tree_state_blocks_tracked_changes(monkeypatch):
    monkeypatch.setattr(git, "run_git", lambda _repo_path, _args: " M src/lantern/cli.py\n?? notes.txt")

    state = git.get_working_tree_state("/tmp/repo")

    assert state == {
        "is_clean": False,
        "has_untracked": True,
        "has_tracked_changes": True,
        "allows_checkout_latest": False,
    }
