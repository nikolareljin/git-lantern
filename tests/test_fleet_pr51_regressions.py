import argparse

from lantern import cli


def test_recommended_actions_skip_checkout_latest_for_missing_local_repo():
    assert cli._recommended_actions_for_snapshot(
        {
            "local_exists": False,
            "local_missing": True,
            "tracked_dirty": "no",
            "state": "missing-local",
            "remote_missing": False,
            "latest_remote_branch": "release/1.0",
            "current_branch": "-",
        }
    ) == ["clone"]


def test_tui_load_fleet_snapshot_records_fetch_state(monkeypatch):
    session = {"root": "/tmp/workspace"}
    monkeypatch.setattr(cli, "_fleet_snapshot_path", lambda _root: "/tmp/workspace/.lantern/fleet-snapshot.json")
    monkeypatch.setattr(cli, "_tui_common_fleet_opts", lambda _session, _server: ["--root", "/tmp/workspace"])
    monkeypatch.setattr(cli, "_dialog_infobox", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli.subprocess, "run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_run_lantern_subprocess", lambda *_args, **_kwargs: argparse.Namespace(returncode=0, stderr=""))
    monkeypatch.setattr(cli, "_load_snapshot_payload", lambda _path: {"repos": []})

    payload = cli._tui_load_fleet_snapshot(session, "github.com", True, False, 24, 80)

    assert payload == {"repos": []}
    assert session["snapshot_fetched"] is True


def test_tui_open_repo_actions_passes_snapshot_fetch_state_to_apply(monkeypatch):
    session = {"root": "/tmp/workspace", "snapshot_fetched": True}
    seen_cmds = []
    choices = iter(["checkout_latest", "back"])

    monkeypatch.setattr(cli, "_dialog_menu", lambda *_args, **_kwargs: next(choices))
    monkeypatch.setattr(cli, "_dialog_infobox", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_dialog_msgbox", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_tui_repo_detail_text", lambda _row: "repo detail")
    monkeypatch.setattr(cli, "_tui_common_fleet_opts", lambda _session, _server: ["--root", "/tmp/workspace"])
    monkeypatch.setattr(cli, "_fleet_log_path", lambda _root: "/tmp/workspace/.lantern/fleet-log.json")
    monkeypatch.setattr(cli, "_fleet_short_summary_from_log", lambda _path: "ok")
    monkeypatch.setattr(cli.subprocess, "run", lambda *_args, **_kwargs: None)

    def fake_run(cmd, *_args, **_kwargs):
        seen_cmds.append(cmd)
        return argparse.Namespace(returncode=0, stderr="")

    monkeypatch.setattr(cli, "_run_lantern_subprocess", fake_run)

    cli._tui_open_repo_actions(
        {"repo": "alpha", "path": "/tmp/workspace/alpha"},
        session,
        "github.com",
        "/tmp/workspace/.lantern/fleet-snapshot.json",
        24,
        80,
    )

    assert seen_cmds
    apply_cmd = seen_cmds[0]
    assert "--snapshot" in apply_cmd
    assert "--fetch" in apply_cmd
    assert "--checkout-latest-branch" in apply_cmd


def test_tui_select_server_uses_non_colliding_default_tag(monkeypatch):
    config = {}
    monkeypatch.setattr(cli.lantern_config, "list_servers", lambda _config: [{"name": "default", "provider": "github"}])
    monkeypatch.setattr(cli.lantern_config, "get_server_name", lambda _config, _fallback: "default")
    seen_items = {}

    def fake_dialog(_title, _text, items, *_args):
        seen_items["items"] = items
        return "__default__"

    monkeypatch.setattr(cli, "_dialog_menu", fake_dialog)

    choice = cli._tui_select_server(config, 24, 80)

    assert choice == "default"
    assert seen_items["items"][0][0] == "__default__"
    assert seen_items["items"][1][0] == "default"


def test_snapshot_paths_within_root_rejects_outside_workspace(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    ok, invalid_paths, warning = cli._snapshot_paths_within_root(
        {
            "root": str(root),
            "repos": [
                {"path": str(root / "repo-a")},
                {"path": str(outside / "repo-b")},
            ],
        },
        str(root),
    )

    assert ok is False
    assert invalid_paths == [str(outside / "repo-b")]
    assert warning is None


def test_cmd_fleet_apply_rejects_snapshot_paths_outside_root(monkeypatch, capsys):
    snapshot_payload = {
        "root": "/tmp/workspace",
        "repos": [
            {"repo": "alpha", "path": "/tmp/outside/alpha", "state": "in-sync", "current_branch": "main"},
        ],
    }
    monkeypatch.setattr(cli, "_fleet_server_context", lambda _args: ("github", "", "", "", {}, {}))
    monkeypatch.setattr(cli, "_load_snapshot_payload", lambda _path: snapshot_payload)

    args = argparse.Namespace(
        checkout_branch="",
        checkout_pr="",
        checkout_latest_branch=False,
        clone_missing=False,
        pull_behind=False,
        push_ahead=False,
        root="/tmp/workspace",
        max_depth=1,
        include_hidden=False,
        fetch=False,
        server="",
        input="",
        user="",
        token="",
        include_forks=False,
        orgs=[],
        all_orgs=False,
        with_user=False,
        repos="",
        dry_run=False,
        only_clean=False,
        log_json="",
        with_prs=False,
        pr_stale_days=30,
        snapshot="/tmp/snapshot.json",
        refresh=False,
    )

    rc = cli.cmd_fleet_apply(args)

    err = capsys.readouterr().err
    assert rc == 1
    assert "Refusing to operate on snapshot paths outside the workspace root" in err


def test_cmd_fleet_apply_snapshot_checkout_still_fetches(monkeypatch, tmp_path, capsys):
    repo_path = tmp_path / "demo"
    repo_path.mkdir()
    monkeypatch.setattr(cli, "render_table", lambda rows, _cols: rows[0]["result"])
    monkeypatch.setattr(cli, "_fleet_server_context", lambda _args: ("github", "", "", "", {}, {}))
    monkeypatch.setattr(
        cli,
        "_load_snapshot_payload",
        lambda _path: {
            "root": str(tmp_path),
            "repos": [
                {
                    "repo": "demo",
                    "path": str(repo_path),
                    "state": "in-sync",
                    "current_branch": "main",
                    "latest_remote_branch": "release/1.0",
                }
            ],
        },
    )
    monkeypatch.setattr(cli, "_is_valid_git_branch_name", lambda _branch: True)
    monkeypatch.setattr(
        cli.git,
        "get_working_tree_state",
        lambda _path: {
            "status_ok": True,
            "is_clean": True,
            "has_untracked": False,
            "has_tracked_changes": False,
            "allows_checkout_latest": True,
            "error": "",
        },
    )
    seen = {}

    def fake_checkout(**kwargs):
        seen.update(kwargs)
        return (["checkout-latest:release/1.0:ok"], [{"action": "checkout-latest", "status": "ok", "branch": "release/1.0"}])

    monkeypatch.setattr(cli, "_checkout_remote_branch", fake_checkout)

    args = argparse.Namespace(
        checkout_branch="",
        checkout_pr="",
        checkout_latest_branch=True,
        clone_missing=False,
        pull_behind=False,
        push_ahead=False,
        root=str(tmp_path),
        max_depth=1,
        include_hidden=False,
        fetch=True,
        server="",
        input="",
        user="",
        token="",
        include_forks=False,
        orgs=[],
        all_orgs=False,
        with_user=False,
        repos="demo",
        dry_run=False,
        only_clean=False,
        log_json="",
        with_prs=False,
        pr_stale_days=30,
        snapshot=str(tmp_path / "snapshot.json"),
        refresh=False,
    )

    rc = cli.cmd_fleet_apply(args)

    out = capsys.readouterr().out
    assert rc == 0
    assert "checkout-latest:release/1.0:ok" in out
    assert seen["fetch_first"] is True
