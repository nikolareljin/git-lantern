import json

from lantern import cli


def test_fleet_apply_snapshot_only_clean_skips_tracked_dirty_repo(monkeypatch, tmp_path, capsys):
    repo_path = tmp_path / "demo"
    repo_path.mkdir()
    snapshot_path = tmp_path / "fleet-snapshot.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "root": str(tmp_path),
                "repos": [
                    {
                        "repo": "demo",
                        "path": str(repo_path),
                        "state": "behind-remote",
                        "current_branch": "main",
                        "current_vs_upstream": "1↓",
                        "git_operation_in_progress": "no",
                        "tracked_dirty": "yes",
                        "latest_remote_branch": "main",
                        "open_pr_numbers": "-",
                        "origin_url": "git@example.com:demo.git",
                        "primary_action": "pull",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "render_table", lambda rows, _columns: rows[0]["result"])
    monkeypatch.setattr(cli, "_fleet_server_context", lambda _args: ("github", "", "", "", {}, {}))
    monkeypatch.setattr(cli.git, "run_git", lambda *_args, **_kwargs: "")

    args = cli.build_parser().parse_args(
        ["fleet", "apply", "--root", str(tmp_path), "--snapshot", str(snapshot_path), "--pull-behind", "--only-clean"]
    )
    rc = cli.cmd_fleet_apply(args)

    assert rc == 0
    assert "pull:skip-dirty" in capsys.readouterr().out
