import json
import subprocess

import pytest

from lantern import cli, git


def run(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True,
    ).stdout.strip()


def init(path):
    path.mkdir()
    run(path, "init", "-b", "main")
    run(path, "config", "user.name", "Test User")
    run(path, "config", "user.email", "test@example.com")


@pytest.fixture
def stale_module(tmp_path, monkeypatch):
    # Permit local fixture cloning without touching global git configuration.
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")
    source = tmp_path / "helpers"
    init(source)
    (source / "VERSION").write_text("1\n")
    run(source, "add", ".")
    run(source, "commit", "-m", "first")
    old = run(source, "rev-parse", "HEAD")
    parent = tmp_path / "demo"
    init(parent)
    run(parent, "submodule", "add", str(source), "scripts/helpers")
    run(parent, "commit", "-am", "add helpers")
    module = parent / "scripts/helpers"
    (source / "VERSION").write_text("2\n")
    run(source, "commit", "-am", "second")
    new = run(source, "rev-parse", "HEAD")
    run(module, "fetch")
    run(module, "checkout", new)
    run(parent, "commit", "-am", "bump helpers")
    run(module, "checkout", old)
    return parent, module, old, new


def test_sync_restores_clean_stale_checkout(stale_module):
    parent, module, _old, new = stale_module
    assert git.get_working_tree_state(str(parent))["has_tracked_changes"] is True
    assert git.sync_submodules(str(parent))["status"] == "ok"
    assert run(module, "rev-parse", "HEAD") == new
    assert git.is_clean(str(parent))


@pytest.mark.parametrize("change", ["tracked", "untracked", "staged-pointer", "config"])
def test_sync_preserves_local_changes(stale_module, change):
    parent, module, old, _new = stale_module
    if change == "tracked":
        (module / "VERSION").write_text("local\n")
    elif change == "untracked":
        (module / "notes").write_text("local\n")
    elif change == "staged-pointer":
        run(parent, "add", "scripts/helpers")
    else:
        with (parent / ".gitmodules").open("a") as handle:
            handle.write("# local configuration\n")
    before = run(parent, "status", "--porcelain")
    assert git.sync_submodules(str(parent))["status"] == "skip-dirty"
    assert run(module, "rev-parse", "HEAD") == old
    assert run(parent, "status", "--porcelain") == before


def test_fleet_repairs_stale_snapshot_before_only_clean_pull(stale_module, tmp_path, monkeypatch):
    parent, module, _old, new = stale_module
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps({"root": str(tmp_path), "repos": [{
        "repo": "demo", "path": str(parent), "state": "behind-remote",
        "current_branch": "main", "latest_remote_branch": "main",
        "tracked_dirty": "yes", "git_operation_in_progress": "no",
    }]}))
    monkeypatch.setattr(cli, "_fleet_server_context", lambda _args: ("github", "", "", "", {}, {}))
    monkeypatch.setattr(cli.git, "get_upstream", lambda _path: "origin/main")
    original_run = subprocess.run
    pulls = []

    def fake_pull(args, **kwargs):
        if "pull" in args:
            pulls.append(args)
            assert run(module, "rev-parse", "HEAD") == new
            return subprocess.CompletedProcess(args, 0)
        return original_run(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_pull)
    args = cli.build_parser().parse_args([
        "fleet", "apply", "--root", str(tmp_path), "--snapshot", str(snapshot),
        "--pull-behind", "--only-clean", "--log-json", str(tmp_path / "run.json"),
    ])
    assert cli.cmd_fleet_apply(args) == 0
    assert len(pulls) == 1
    actions = json.loads((tmp_path / "run.json").read_text())["results"][0]["actions"]
    assert [record["action"] for record in actions] == ["submodules", "pull", "submodules"]


def test_fleet_dry_run_leaves_stale_submodule_untouched(stale_module, tmp_path, monkeypatch):
    parent, module, old, _new = stale_module
    monkeypatch.setattr(cli, "_fleet_server_context", lambda _args: ("github", "", "", "", {}, {}))
    monkeypatch.setattr(cli, "_fleet_load_remote", lambda _args: {"repos": []})
    monkeypatch.setattr(cli, "_fleet_plan_records", lambda _args, payload=None: ([{
        "repo": "demo", "state": "behind-remote", "path": str(parent),
        "branch": "main", "latest_branch": "main", "tracked_dirty": "yes",
    }], {}))
    args = cli.build_parser().parse_args([
        "fleet", "apply", "--root", str(tmp_path), "--pull-behind", "--dry-run",
        "--log-json", str(tmp_path / "dry-run.json"),
    ])
    assert cli.cmd_fleet_apply(args) == 0
    assert run(module, "rev-parse", "HEAD") == old


def test_fleet_checkout_synchronizes_new_branch_pin(stale_module, tmp_path, monkeypatch):
    parent, module, old, _new = stale_module
    run(parent, "branch", "feature/other-pin", "HEAD~1")
    run(parent, "remote", "add", "origin", str(parent))
    monkeypatch.setattr(cli, "_fleet_server_context", lambda _args: ("github", "", "", "", {}, {}))
    monkeypatch.setattr(cli, "_fleet_load_remote", lambda _args: {"repos": []})
    monkeypatch.setattr(cli, "_fleet_plan_records", lambda _args, payload=None: ([{
        "repo": "demo", "state": "in-sync", "path": str(parent), "clean": "no",
        "branch": "main", "latest_branch": "feature/other-pin", "tracked_dirty": "yes",
        "git_operation_in_progress": "no",
    }], {}))
    args = cli.build_parser().parse_args([
        "fleet", "apply", "--root", str(tmp_path), "--checkout-latest-branch", "--only-clean",
        "--log-json", str(tmp_path / "checkout.json"),
    ])
    assert cli.cmd_fleet_apply(args) == 0
    assert run(parent, "branch", "--show-current") == "feature/other-pin"
    assert run(module, "rev-parse", "HEAD") == old
    assert git.is_clean(str(parent))


def test_sync_initializes_missing_modules(stale_module):
    parent, module, _old, new = stale_module
    run(parent, "submodule", "deinit", "-f", "--", "scripts/helpers")
    assert git.sync_submodules(str(parent))["status"] == "ok"
    assert run(module, "rev-parse", "HEAD") == new


def test_sync_reports_update_failure(stale_module, monkeypatch):
    parent, module, old, _new = stale_module
    original_run = subprocess.run

    def fail_update(args, **kwargs):
        if args[3:5] == ["submodule", "update"]:
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="missing commit")
        return original_run(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fail_update)
    assert git.sync_submodules(str(parent)) == {"status": "fail", "detail": "missing commit"}
    assert run(module, "rev-parse", "HEAD") == old
