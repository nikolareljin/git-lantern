import argparse

from lantern import cli


def test_cmd_repos_uses_snapshot_payload(monkeypatch, capsys):
    snapshot_payload = {
        "repos": [
            {
                "repo": "alpha",
                "path": "/tmp/alpha",
                "origin_url": "git@example.com:alpha.git",
            }
        ]
    }
    monkeypatch.setattr(cli, "_build_fleet_snapshot", lambda *_args, **_kwargs: (snapshot_payload, {}))
    monkeypatch.setattr(cli, "render_table", lambda rows, cols: f"{cols}|{rows[0]['name']}|{rows[0]['origin']}")

    args = argparse.Namespace(root="/tmp/workspace", max_depth=3, include_hidden=False, fetch=False)
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
