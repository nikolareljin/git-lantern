import argparse

from lantern import cli


def _status_args(with_prs: bool) -> argparse.Namespace:
    return argparse.Namespace(
        root="/tmp/workspace",
        max_depth=4,
        include_hidden=False,
        fetch=False,
        server="",
        user="",
        token="",
        with_prs=with_prs,
        pr_stale_days=30,
    )


def test_cmd_status_always_includes_latest_columns(monkeypatch, capsys):
    monkeypatch.setattr(cli, "find_repos", lambda *_args, **_kwargs: ["/tmp/workspace/repo"])
    monkeypatch.setattr(
        cli,
        "_collect_repo_records_with_progress",
        lambda *_args, **_kwargs: [
            {
                "name": "repo",
                "path": "/tmp/workspace/repo",
                "branch": "main",
                "upstream": "origin/main",
                "up": "≡",
                "main_ref": "origin/main",
                "main": "≡",
                "origin": "git@github.com:owner/repo.git",
            }
        ],
    )
    monkeypatch.setattr(cli, "add_divergence_fields", lambda rec: rec)
    monkeypatch.setattr(cli, "_detect_latest_branch", lambda _path: "main")
    monkeypatch.setattr(
        cli,
        "render_table",
        lambda rows, cols: f"{','.join(cols)}|{rows[0].get('latest_branch')}|{rows[0].get('latest_pr')}",
    )

    rc = cli.cmd_status(_status_args(with_prs=False))

    out = capsys.readouterr().out
    assert rc == 0
    assert "latest_branch" in out
    assert "latest_pr" in out
    assert "|main|-" in out


def test_cmd_status_with_prs_uses_latest_pr_number(monkeypatch, capsys):
    monkeypatch.setattr(cli, "find_repos", lambda *_args, **_kwargs: ["/tmp/workspace/repo"])
    monkeypatch.setattr(
        cli,
        "_collect_repo_records_with_progress",
        lambda *_args, **_kwargs: [
            {
                "name": "repo",
                "path": "/tmp/workspace/repo",
                "branch": "main",
                "upstream": "origin/main",
                "up": "≡",
                "main_ref": "origin/main",
                "main": "≡",
                "origin": "git@github.com:owner/repo.git",
            }
        ],
    )
    monkeypatch.setattr(cli, "add_divergence_fields", lambda rec: rec)
    monkeypatch.setattr(cli, "_detect_latest_branch", lambda _path: "main")
    monkeypatch.setattr(
        cli,
        "_fleet_server_context",
        lambda _args: ("github", "https://api.github.com", "user", "token", None, {}),
    )
    monkeypatch.setattr(
        cli.github,
        "fetch_open_pull_requests",
        lambda **_kwargs: [{"number": 42, "head_ref": "feature/pr"}],
    )
    monkeypatch.setattr(
        cli,
        "render_table",
        lambda rows, cols: f"{','.join(cols)}|{rows[0].get('latest_branch')}|{rows[0].get('latest_pr')}",
    )

    rc = cli.cmd_status(_status_args(with_prs=True))

    out = capsys.readouterr().out
    assert rc == 0
    assert "|main|42" in out


def test_cmd_status_uses_latest_pr_head_ref_when_branch_unknown(monkeypatch, capsys):
    monkeypatch.setattr(cli, "find_repos", lambda *_args, **_kwargs: ["/tmp/workspace/repo"])
    monkeypatch.setattr(
        cli,
        "_collect_repo_records_with_progress",
        lambda *_args, **_kwargs: [
            {
                "name": "repo",
                "path": "/tmp/workspace/repo",
                "branch": "detached",
                "upstream": "-",
                "up": "-",
                "main_ref": "origin/main",
                "main": "-",
                "origin": "https://github.com/owner/repo.git",
            }
        ],
    )
    monkeypatch.setattr(cli, "add_divergence_fields", lambda rec: rec)
    monkeypatch.setattr(cli, "_detect_latest_branch", lambda _path: "-")
    monkeypatch.setattr(
        cli,
        "_fleet_server_context",
        lambda _args: ("github", "https://api.github.com", "user", "token", None, {}),
    )
    monkeypatch.setattr(
        cli.github,
        "fetch_open_pull_requests",
        lambda **_kwargs: [{"number": 101, "head_ref": "release/next"}],
    )
    monkeypatch.setattr(
        cli,
        "render_table",
        lambda rows, cols: f"{','.join(cols)}|{rows[0].get('latest_branch')}|{rows[0].get('latest_pr')}",
    )

    rc = cli.cmd_status(_status_args(with_prs=True))

    out = capsys.readouterr().out
    assert rc == 0
    assert "|release/next|101" in out


def test_status_parser_accepts_with_prs_flags():
    parser = cli.build_parser()

    args = parser.parse_args(["status", "--with-prs", "--pr-stale-days", "14"])

    assert args.with_prs is True
    assert args.pr_stale_days == 14


def test_cmd_status_preserves_zero_pr_stale_days(monkeypatch, capsys):
    seen: dict = {}
    monkeypatch.setattr(cli, "find_repos", lambda *_args, **_kwargs: ["/tmp/workspace/repo"])
    monkeypatch.setattr(
        cli,
        "_collect_repo_records_with_progress",
        lambda *_args, **_kwargs: [
            {
                "name": "repo",
                "path": "/tmp/workspace/repo",
                "branch": "main",
                "upstream": "origin/main",
                "up": "≡",
                "main_ref": "origin/main",
                "main": "≡",
                "origin": "git@github.com:owner/repo.git",
            }
        ],
    )
    monkeypatch.setattr(cli, "add_divergence_fields", lambda rec: rec)
    monkeypatch.setattr(cli, "_detect_latest_branch", lambda _path: "main")
    monkeypatch.setattr(
        cli,
        "_fleet_server_context",
        lambda _args: ("github", "https://api.github.com", "user", "token", None, {}),
    )

    def _fake_fetch(**kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr(cli.github, "fetch_open_pull_requests", _fake_fetch)
    monkeypatch.setattr(cli, "render_table", lambda rows, cols: f"{','.join(cols)}|{rows[0].get('latest_pr')}")
    args = _status_args(with_prs=True)
    args.pr_stale_days = 0

    rc = cli.cmd_status(args)

    capsys.readouterr()
    assert rc == 0
    assert seen.get("stale_days") == 0


def test_cmd_status_skips_pr_lookup_when_origin_host_mismatch(monkeypatch, capsys):
    monkeypatch.setattr(cli, "find_repos", lambda *_args, **_kwargs: ["/tmp/workspace/repo"])
    monkeypatch.setattr(
        cli,
        "_collect_repo_records_with_progress",
        lambda *_args, **_kwargs: [
            {
                "name": "repo",
                "path": "/tmp/workspace/repo",
                "branch": "main",
                "upstream": "origin/main",
                "up": "≡",
                "main_ref": "origin/main",
                "main": "≡",
                "origin": "git@gitlab.com:owner/repo.git",
            }
        ],
    )
    monkeypatch.setattr(cli, "add_divergence_fields", lambda rec: rec)
    monkeypatch.setattr(cli, "_detect_latest_branch", lambda _path: "main")
    monkeypatch.setattr(
        cli,
        "_fleet_server_context",
        lambda _args: ("github", "https://api.github.com", "user", "token", None, {}),
    )
    monkeypatch.setattr(
        cli.github,
        "fetch_open_pull_requests",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("fetch_open_pull_requests should not be called")),
    )
    monkeypatch.setattr(
        cli,
        "render_table",
        lambda rows, cols: f"{','.join(cols)}|{rows[0].get('latest_branch')}|{rows[0].get('latest_pr')}",
    )

    rc = cli.cmd_status(_status_args(with_prs=True))

    out = capsys.readouterr().out
    assert rc == 0
    assert "|main|-" in out


def test_cmd_status_warns_when_pr_lookup_fails(monkeypatch, capsys):
    monkeypatch.setattr(cli, "find_repos", lambda *_args, **_kwargs: ["/tmp/workspace/repo"])
    monkeypatch.setattr(
        cli,
        "_collect_repo_records_with_progress",
        lambda *_args, **_kwargs: [
            {
                "name": "repo",
                "path": "/tmp/workspace/repo",
                "branch": "main",
                "upstream": "origin/main",
                "up": "≡",
                "main_ref": "origin/main",
                "main": "≡",
                "origin": "git@github.com:owner/repo.git",
            }
        ],
    )
    monkeypatch.setattr(cli, "add_divergence_fields", lambda rec: rec)
    monkeypatch.setattr(cli, "_detect_latest_branch", lambda _path: "main")
    monkeypatch.setattr(
        cli,
        "_fleet_server_context",
        lambda _args: ("github", "https://api.github.com", "user", "token", None, {}),
    )
    monkeypatch.setattr(
        cli.github,
        "fetch_open_pull_requests",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(cli, "render_table", lambda rows, cols: f"{','.join(cols)}|{rows[0].get('latest_pr')}")

    rc = cli.cmd_status(_status_args(with_prs=True))

    captured = capsys.readouterr()
    assert rc == 0
    assert "Warning: failed to fetch pull requests for owner/repo: boom" in captured.err
