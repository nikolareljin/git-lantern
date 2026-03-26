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
