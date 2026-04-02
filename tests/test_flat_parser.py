from lantern import cli
import argparse
import json

def test_fleet_overview_parser_supports_flat():
    parser = cli.build_parser()
    args = parser.parse_args(["fleet", "overview", "--flat"])
    assert args.fleet_command == "overview"
    assert args.flat is True

def test_fleet_plan_parser_supports_flat():
    parser = cli.build_parser()
    args = parser.parse_args(["fleet", "plan", "--flat"])
    assert args.fleet_command == "plan"
    assert args.flat is True

def test_fleet_apply_parser_supports_flat():
    parser = cli.build_parser()
    args = parser.parse_args(["fleet", "apply", "--flat"])
    assert args.fleet_command == "apply"
    assert args.flat is True

def test_forge_clone_parser_supports_flat():
    parser = cli.build_parser()
    args = parser.parse_args(["forge", "clone", "--flat"])
    assert args.forge_command == "clone"
    assert args.flat is True


def test_fleet_plan_records_uses_flat_destination_for_missing_local_repo(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "find_repos", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(cli, "_fleet_server_context", lambda _args: ("github", "", "", "", {}, {}))

    args = argparse.Namespace(
        root=str(tmp_path),
        max_depth=1,
        include_hidden=False,
        fetch=False,
        with_prs=False,
        pr_stale_days=30,
        flat=True,
    )
    payload = {
        "repos": [
            {
                "name": "my-namespace/my-repo",
                "ssh_url": "git@example.com:my-namespace/my-repo.git",
            }
        ]
    }

    rows, _meta = cli._fleet_plan_records(args, payload=payload)

    assert rows[0]["state"] == "missing-local"
    assert rows[0]["path"] == str(tmp_path / "my-repo")


def test_cmd_github_clone_dry_run_uses_flat_destination(tmp_path, capsys):
    input_path = tmp_path / "repos.json"
    input_path.write_text(
        json.dumps(
            {
                "repos": [
                    {
                        "name": "my-namespace/my-repo",
                        "ssh_url": "git@example.com:my-namespace/my-repo.git",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    args = argparse.Namespace(
        input=str(input_path),
        server="",
        root=str(tmp_path / "workspace"),
        tui=False,
        flat=True,
        dry_run=True,
    )

    rc = cli.cmd_github_clone(args)

    out = capsys.readouterr().out
    assert rc == 0
    assert f"{tmp_path / 'workspace' / 'my-repo'}" in out
    assert "my-namespace/my-repo" not in out.split("git clone ", 1)[1].rsplit(" ", 1)[-1]
