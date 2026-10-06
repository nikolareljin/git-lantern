import argparse

from lantern import cli


def test_detect_latest_branch_prefers_origin_refs(monkeypatch):
    def _fake_run_git(_path, args):
        if args[-1] == "refs/remotes/origin":
            return "refs/remotes/origin/HEAD\nrefs/remotes/origin/feature-latest\nrefs/remotes/origin/main"
        return ""

    monkeypatch.setattr(cli.git, "run_git", _fake_run_git)

    assert cli._detect_latest_branch("/tmp/repo") == "feature-latest"


def test_detect_latest_branch_falls_back_to_local_refs(monkeypatch):
    def _fake_run_git(_path, args):
        if args[-1] == "refs/remotes/origin":
            return ""
        if args[-1] == "refs/heads":
            return "refs/heads/dev\nrefs/heads/main"
        return ""

    monkeypatch.setattr(cli.git, "run_git", _fake_run_git)

    assert cli._detect_latest_branch("/tmp/repo") == "dev"


def test_detect_latest_branch_ignores_short_origin_head_symbolic_name(monkeypatch):
    def _fake_run_git(_path, args):
        if args[-1] == "refs/remotes/origin":
            return "origin\norigin/release/0.4.2\norigin/main"
        return ""

    monkeypatch.setattr(cli.git, "run_git", _fake_run_git)

    assert cli._detect_latest_branch("/tmp/repo") == "release/0.4.2"


def test_cmd_fleet_plan_always_includes_latest_branch_column(monkeypatch, capsys):
    rows = [
        {
            "repo": "owner/repo",
            "state": "in-sync",
            "up": "≡",
            "clean": "yes",
            "action": "-",
            "latest_branch": "main",
            "prs": "-",
            "path": "/tmp/repo",
        }
    ]
    monkeypatch.setattr(cli, "_fleet_plan_records", lambda _args: (rows, {"server": "github.com", "local_count": 1, "remote_count": 1}))
    monkeypatch.setattr(cli, "render_table", lambda _rows, columns: ",".join(columns))
    args = argparse.Namespace(with_prs=False)

    rc = cli.cmd_fleet_plan(args)

    out = capsys.readouterr().out
    assert rc == 0
    assert "latest_branch" in out


def test_detect_latest_branch_excludes_dependabot(monkeypatch):
    monkeypatch.setattr(cli.git, "run_git", lambda _path, args:
                        "refs/remotes/origin/dependabot/pip/pkg\nrefs/remotes/origin/feature"
                        if args[0] == "for-each-ref" else "")
    assert cli._detect_latest_branch("/tmp/repo") == "feature"


def test_detect_latest_branch_prefers_main_containing_latest_tip(monkeypatch):
    def fake_git(_path, args):
        if args[0] == "for-each-ref":
            return "refs/remotes/origin/release/1.0\nrefs/remotes/origin/main"
        if args[0] == "rev-list":
            return "0"
        return ""

    monkeypatch.setattr(cli.git, "run_git", fake_git)
    assert cli._detect_latest_branch("/tmp/repo") == "main"
