import argparse
import json

from lantern import cli


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


def test_fleet_plan_records_uses_suffix_for_flat_destination_collision(monkeypatch, tmp_path):
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
                "name": "alpha/shared-repo",
                "ssh_url": "git@example.com:alpha/shared-repo.git",
            },
            {
                "name": "beta/shared-repo",
                "ssh_url": "git@example.com:beta/shared-repo.git",
            },
        ]
    }

    rows, _meta = cli._fleet_plan_records(args, payload=payload)

    assert [row["path"] for row in rows] == [
        str(tmp_path / "shared-repo"),
        str(tmp_path / "shared-repo-2"),
    ]


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


def test_cmd_github_clone_dry_run_uses_suffix_for_flat_destination_collision(tmp_path, capsys):
    input_path = tmp_path / "repos.json"
    input_path.write_text(
        json.dumps(
            {
                "repos": [
                    {
                        "name": "alpha/shared-repo",
                        "ssh_url": "git@example.com:alpha/shared-repo.git",
                    },
                    {
                        "name": "beta/shared-repo",
                        "ssh_url": "git@example.com:beta/shared-repo.git",
                    },
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

    out_lines = capsys.readouterr().out.splitlines()
    assert rc == 0
    assert any(str(tmp_path / "workspace" / "shared-repo") in line for line in out_lines)
    assert any(str(tmp_path / "workspace" / "shared-repo-2") in line for line in out_lines)


def test_cmd_github_clone_dry_run_falls_back_to_encoded_destination_on_basename_collision(tmp_path, capsys):
    input_path = tmp_path / "repos.json"
    input_path.write_text(
        json.dumps(
            {
                "repos": [
                    {
                        "name": "alpha/shared-repo",
                        "ssh_url": "git@example.com:alpha/shared-repo.git",
                    },
                    {
                        "name": "beta/shared-repo",
                        "ssh_url": "git@example.com:beta/shared-repo.git",
                    },
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
        flat=False,
        dry_run=True,
    )

    rc = cli.cmd_github_clone(args)

    out_lines = capsys.readouterr().out.splitlines()
    assert rc == 0
    assert any(str(tmp_path / "workspace" / "shared-repo") in line for line in out_lines)
    assert any(str(tmp_path / "workspace" / "beta%2Fshared-repo") in line for line in out_lines)


def test_cmd_github_clone_dry_run_skips_repo_when_basename_destination_already_exists(tmp_path, capsys, monkeypatch):
    input_path = tmp_path / "repos.json"
    workspace = tmp_path / "workspace"
    (workspace / "shared-repo").mkdir(parents=True)
    input_path.write_text(
        json.dumps(
            {
                "repos": [
                    {
                        "name": "alpha/shared-repo",
                        "ssh_url": "git@example.com:alpha/shared-repo.git",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    args = argparse.Namespace(
        input=str(input_path),
        server="",
        root=str(workspace),
        tui=False,
        flat=False,
        dry_run=True,
    )

    monkeypatch.setattr(cli.git, "is_git_repo", lambda path: path == str(workspace / "shared-repo"))
    monkeypatch.setattr(cli.git, "get_origin_url", lambda path: "git@example.com:alpha/shared-repo.git")
    rc = cli.cmd_github_clone(args)

    out = capsys.readouterr().out
    assert rc == 0
    assert out == ""


def test_cmd_github_clone_dry_run_falls_back_when_existing_basename_is_different_repo(tmp_path, capsys, monkeypatch):
    input_path = tmp_path / "repos.json"
    workspace = tmp_path / "workspace"
    (workspace / "shared-repo").mkdir(parents=True)
    input_path.write_text(
        json.dumps(
            {
                "repos": [
                    {
                        "name": "alpha/shared-repo",
                        "ssh_url": "git@example.com:alpha/shared-repo.git",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    args = argparse.Namespace(
        input=str(input_path),
        server="",
        root=str(workspace),
        tui=False,
        flat=False,
        dry_run=True,
    )

    monkeypatch.setattr(cli.git, "is_git_repo", lambda path: path == str(workspace / "shared-repo"))
    monkeypatch.setattr(cli.git, "get_origin_url", lambda path: "git@example.com:other/shared-repo.git")

    rc = cli.cmd_github_clone(args)

    out = capsys.readouterr().out
    assert rc == 0
    assert str(workspace / "alpha%2Fshared-repo") in out


def test_cmd_github_clone_dry_run_skips_repo_when_encoded_destination_already_exists(tmp_path, capsys, monkeypatch):
    input_path = tmp_path / "repos.json"
    workspace = tmp_path / "workspace"
    (workspace / "shared-repo").mkdir(parents=True)
    (workspace / "alpha%2Fshared-repo").mkdir(parents=True)
    input_path.write_text(
        json.dumps(
            {
                "repos": [
                    {
                        "name": "alpha/shared-repo",
                        "ssh_url": "git@example.com:alpha/shared-repo.git",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    args = argparse.Namespace(
        input=str(input_path),
        server="",
        root=str(workspace),
        tui=False,
        flat=False,
        dry_run=True,
    )

    def fake_is_git_repo(path):
        return path in {
            str(workspace / "shared-repo"),
            str(workspace / "alpha%2Fshared-repo"),
        }

    def fake_get_origin_url(path):
        if path == str(workspace / "shared-repo"):
            return "git@example.com:other/shared-repo.git"
        if path == str(workspace / "alpha%2Fshared-repo"):
            return "git@example.com:alpha/shared-repo.git"
        return ""

    monkeypatch.setattr(cli.git, "is_git_repo", fake_is_git_repo)
    monkeypatch.setattr(cli.git, "get_origin_url", fake_get_origin_url)

    rc = cli.cmd_github_clone(args)

    out = capsys.readouterr().out
    assert rc == 0
    assert out == ""
