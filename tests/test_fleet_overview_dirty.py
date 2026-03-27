import argparse
import json
import pytest

from lantern import cli


def test_fleet_parser_supports_overview_dirty_and_snapshot_args():
    parser = cli.build_parser()

    overview_args = parser.parse_args(["fleet", "overview", "--output", "snapshot.json"])
    dirty_args = parser.parse_args(["fleet", "dirty"])
    apply_args = parser.parse_args(["fleet", "apply", "--snapshot", "snapshot.json", "--refresh"])

    assert overview_args.fleet_command == "overview"
    assert overview_args.output == "snapshot.json"
    assert overview_args.func == cli.cmd_fleet_overview
    assert dirty_args.fleet_command == "dirty"
    assert dirty_args.func == cli.cmd_fleet_dirty
    assert not hasattr(dirty_args, "with_prs")
    assert not hasattr(dirty_args, "server")
    assert apply_args.snapshot == "snapshot.json"
    assert apply_args.refresh is True


def test_fleet_dirty_parser_rejects_remote_pr_flags():
    parser = cli.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["fleet", "dirty", "--with-prs"])


def test_cmd_fleet_dirty_filters_tracked_changes(monkeypatch, capsys):
    snapshot_payload = {
        "repos": [
            {
                "repo": "alpha",
                "current_branch": "main",
                "tracked_dirty": "yes",
                "path": "/tmp/alpha",
            },
            {
                "repo": "beta",
                "current_branch": "main",
                "tracked_dirty": "no",
                "path": "/tmp/beta",
            },
        ]
    }
    monkeypatch.setattr(cli, "_build_fleet_snapshot", lambda *_args, **_kwargs: (snapshot_payload, {}))
    monkeypatch.setattr(cli, "render_table", lambda rows, cols: f"{cols}|{[row['repo'] for row in rows]}")

    args = argparse.Namespace(
        root="/tmp/workspace",
        max_depth=3,
        include_hidden=False,
        fetch=False,
    )

    rc = cli.cmd_fleet_dirty(args)

    out = capsys.readouterr().out
    assert rc == 0
    assert "alpha" in out
    assert "beta" not in out


def test_cmd_fleet_overview_writes_snapshot_output(monkeypatch, tmp_path, capsys):
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_payload = {
        "metadata": {"server": "github.com", "local_count": 1, "remote_count": 1},
        "repos": [
            {
                "repo": "alpha",
                "local_exists": True,
                "remote_exists": True,
                "default_branch": "main",
                "current_branch": "main",
                "upstream_branch": "origin/main",
                "current_vs_upstream": "≡",
                "latest_remote_branch": "main",
                "tracked_dirty": "no",
                "open_pr_numbers": "-",
                "state": "in-sync",
                "recommended_actions": "none",
                "path": "/tmp/alpha",
            }
        ],
    }
    monkeypatch.setattr(cli, "_build_fleet_snapshot", lambda *_args, **_kwargs: (snapshot_payload, snapshot_payload["metadata"]))
    monkeypatch.setattr(cli, "render_table", lambda rows, cols: f"{cols}|{rows[0]['repo']}")

    args = argparse.Namespace(
        root="/tmp/workspace",
        max_depth=3,
        include_hidden=False,
        fetch=False,
        server="github.com",
        input="",
        user="",
        token="",
        include_forks=False,
        orgs=[],
        all_orgs=False,
        with_user=False,
        with_prs=False,
        pr_stale_days=30,
        output=str(snapshot_path),
    )

    rc = cli.cmd_fleet_overview(args)

    out = capsys.readouterr().out
    assert rc == 0
    assert "alpha" in out
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert payload["repos"][0]["repo"] == "alpha"


def test_recommended_actions_only_include_review_local_for_tracked_dirty():
    assert cli._recommended_actions_for_snapshot(
        {
            "local_missing": False,
            "tracked_dirty": "no",
            "state": "in-sync",
            "remote_missing": False,
            "latest_remote_branch": "main",
            "current_branch": "main",
        }
    ) == ["none"]
    assert cli._recommended_actions_for_snapshot(
        {
            "local_missing": False,
            "tracked_dirty": "yes",
            "state": "in-sync",
            "remote_missing": False,
            "latest_remote_branch": "main",
            "current_branch": "main",
        }
    ) == ["review-local"]
