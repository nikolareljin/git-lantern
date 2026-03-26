import argparse

from lantern import cli


def test_cmd_repos_keeps_lightweight_local_scan(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_build_fleet_snapshot", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("snapshot path should not be used")))
    monkeypatch.setattr(cli, "find_repos", lambda *_args, **_kwargs: ["/tmp/alpha"])
    monkeypatch.setattr(cli.git, "get_origin_url", lambda _path: "git@example.com:alpha.git")
    monkeypatch.setattr(cli, "render_table", lambda rows, cols: f"{cols}|{rows[0]['name']}|{rows[0]['origin']}")

    args = argparse.Namespace(root="/tmp/workspace", max_depth=3, include_hidden=False)
    rc = cli.cmd_repos(args)

    out = capsys.readouterr().out
    assert rc == 0
    assert "alpha" in out
    assert "git@example.com:alpha.git" in out


def test_cmd_sync_uses_snapshot_state_for_skip_checks(monkeypatch, capsys):
    snapshot_payload = {
        "repos": [
            {
                "repo": "alpha",
                "path": "/tmp/alpha",
                "git_operation_in_progress": "yes",
                "upstream_branch": "origin/main",
            },
            {
                "repo": "beta",
                "path": "/tmp/beta",
                "git_operation_in_progress": "no",
                "upstream_branch": "-",
            },
        ]
    }
    monkeypatch.setattr(cli, "_build_fleet_snapshot", lambda *_args, **_kwargs: (snapshot_payload, {}))
    monkeypatch.setattr(cli, "render_table", lambda rows, cols: "\n".join(f"{row['name']}:{row['result']}" for row in rows))

    args = argparse.Namespace(
        root="/tmp/workspace",
        max_depth=3,
        include_hidden=False,
        fetch=False,
        pull=True,
        push=False,
        dry_run=False,
        only_clean=True,
        only_upstream=True,
    )

    rc = cli.cmd_sync(args)

    out = capsys.readouterr().out
    assert rc == 0
    assert "alpha:skip:in-progress" in out
    assert "beta:skip:no-upstream" in out


def test_build_fleet_snapshot_defaults_missing_fetch_and_skips_server_context_for_local_only(monkeypatch):
    monkeypatch.setattr(cli, "find_repos", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(cli, "_collect_repo_records_with_progress", lambda repos, fetch, label: [] if not repos and fetch is False and label == "snapshot" else None)
    monkeypatch.setattr(
        cli,
        "_fleet_server_context",
        lambda _args: (_ for _ in ()).throw(AssertionError("local-only snapshots should not load remote server context")),
    )

    args = argparse.Namespace(root="/tmp/workspace", max_depth=3, include_hidden=False)
    snapshot_payload, meta = cli._build_fleet_snapshot(args, include_remote=False)

    assert snapshot_payload["repos"] == []
    assert meta["remote_count"] == 0
