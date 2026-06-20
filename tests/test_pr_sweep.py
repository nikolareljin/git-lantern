"""Tests for pr_sweep discovery and filtering logic."""

import json
from unittest.mock import MagicMock, patch

import pytest

from lantern import pr_sweep, forge_client


# ---------------------------------------------------------------------------
# forge_client tests
# ---------------------------------------------------------------------------


def test_fetch_frozen_repos_parses_response():
    response_body = json.dumps(
        [
            {"external_repo_full_name": "owner/frozen-repo", "is_frozen": True},
            {"external_repo_full_name": "owner/active-repo", "is_frozen": False},
            {"external_repo_full_name": "Owner/Mixed-Case", "is_frozen": True},
        ]
    ).encode()

    mock_resp = MagicMock()
    mock_resp.read.return_value = response_body
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        frozen = forge_client.fetch_frozen_repos("http://localhost:8000")

    assert "owner/frozen-repo" in frozen
    assert "owner/mixed-case" in frozen  # lowercased
    assert "owner/active-repo" not in frozen


def test_fetch_frozen_repos_raises_on_network_error():
    import urllib.error

    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
        with pytest.raises(urllib.error.URLError):
            forge_client.fetch_frozen_repos("http://localhost:8000")


def test_fetch_frozen_repos_empty_list():
    mock_resp = MagicMock()
    mock_resp.read.return_value = b"[]"
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        frozen = forge_client.fetch_frozen_repos("http://localhost:8000")

    assert frozen == set()


# ---------------------------------------------------------------------------
# pr_sweep.list_owner_repos tests
# ---------------------------------------------------------------------------


def test_list_owner_repos_filters_response():
    raw = json.dumps(
        [
            {"nameWithOwner": "user/repo-a", "isFork": False, "isArchived": False},
            {"nameWithOwner": "user/forked", "isFork": True, "isArchived": False},
            {"nameWithOwner": "user/archived", "isFork": False, "isArchived": True},
        ]
    )

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = raw

    with patch("shutil.which", return_value="/usr/bin/gh"), \
         patch("subprocess.run", return_value=mock_proc):
        repos = pr_sweep.list_owner_repos("user")

    assert repos is not None
    assert len(repos) == 3
    fork_entries = [r for r in repos if r["fork"]]
    archived_entries = [r for r in repos if r["archived"]]
    assert len(fork_entries) == 1
    assert len(archived_entries) == 1


def test_list_owner_repos_returns_none_when_gh_missing():
    with patch("shutil.which", return_value=None):
        result = pr_sweep.list_owner_repos("user")
    assert result is None


def test_list_owner_repos_returns_none_on_nonzero_exit():
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stdout = ""

    with patch("shutil.which", return_value="/usr/bin/gh"), \
         patch("subprocess.run", return_value=mock_proc):
        result = pr_sweep.list_owner_repos("user")
    assert result is None


# ---------------------------------------------------------------------------
# pr_sweep.fetch_pr_unresolved_thread_count tests
# ---------------------------------------------------------------------------


def test_fetch_pr_unresolved_thread_count_counts_unresolved():
    payload = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "nodes": [
                            {"isResolved": False},
                            {"isResolved": True},
                            {"isResolved": False},
                        ]
                    }
                }
            }
        }
    }
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = json.dumps(payload)

    with patch("shutil.which", return_value="/usr/bin/gh"), \
         patch("subprocess.run", return_value=mock_proc):
        count = pr_sweep.fetch_pr_unresolved_thread_count("owner", "repo", 42)

    assert count == 2


def test_fetch_pr_unresolved_thread_count_paginates_review_threads():
    first_page = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "nodes": [
                            {"isResolved": True},
                            {"isResolved": False},
                        ],
                        "pageInfo": {"hasNextPage": True, "endCursor": "cursor-1"},
                    }
                }
            }
        }
    }
    second_page = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "nodes": [
                            {"isResolved": False},
                            {"isResolved": False},
                        ],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            }
        }
    }
    first_proc = MagicMock(returncode=0, stdout=json.dumps(first_page))
    second_proc = MagicMock(returncode=0, stdout=json.dumps(second_page))

    with patch("shutil.which", return_value="/usr/bin/gh"), patch(
        "subprocess.run", side_effect=[first_proc, second_proc]
    ) as run_mock:
        count = pr_sweep.fetch_pr_unresolved_thread_count("owner", "repo", 42)

    assert count == 3
    assert run_mock.call_count == 2
    assert "-F" in run_mock.call_args_list[1].args[0]
    assert "cursor=cursor-1" in run_mock.call_args_list[1].args[0]


def test_fetch_pr_unresolved_thread_count_returns_minus_one_when_gh_missing():
    with patch("shutil.which", return_value=None):
        count = pr_sweep.fetch_pr_unresolved_thread_count("owner", "repo", 1)
    assert count == -1


def test_fetch_pr_unresolved_thread_count_returns_minus_one_on_api_error():
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stdout = ""

    with patch("shutil.which", return_value="/usr/bin/gh"), \
         patch("subprocess.run", return_value=mock_proc):
        count = pr_sweep.fetch_pr_unresolved_thread_count("owner", "repo", 1)
    assert count == -1


# ---------------------------------------------------------------------------
# pr_sweep.discover_eligible_prs integration-style tests
# ---------------------------------------------------------------------------


def _make_pr(number: int, title: str = "Fix stuff", url: str = "") -> dict:
    return {
        "number": number,
        "title": title,
        "head_ref": "feat/fix",
        "updated_at": "2026-01-01T00:00:00Z",
        "html_url": url or f"https://github.com/user/repo/pull/{number}",
    }


def test_discover_eligible_prs_excludes_forks(monkeypatch):
    repos = [
        {"full_name": "user/original", "fork": False, "archived": False},
        {"full_name": "user/forked", "fork": True, "archived": False},
    ]
    monkeypatch.setattr(pr_sweep, "list_owner_repos", lambda owner: repos)
    monkeypatch.setattr(pr_sweep, "fetch_pr_unresolved_thread_count", lambda *_: 1)
    monkeypatch.setattr(
        "lantern.github.fetch_open_pull_requests",
        lambda owner, repo, token, **kw: [_make_pr(1)] if repo == "original" else [],
    )

    jobs, warnings = pr_sweep.discover_eligible_prs(
        owner="user",
        token=None,
        forge_url="",
        skip_forks=True,
        skip_frozen=False,
    )

    repos_in_jobs = {j["repo"] for j in jobs}
    assert "user/original" in repos_in_jobs
    assert "user/forked" not in repos_in_jobs


def test_discover_eligible_prs_excludes_archived(monkeypatch):
    repos = [
        {"full_name": "user/live", "fork": False, "archived": False},
        {"full_name": "user/old", "fork": False, "archived": True},
    ]
    monkeypatch.setattr(pr_sweep, "list_owner_repos", lambda owner: repos)
    monkeypatch.setattr(pr_sweep, "fetch_pr_unresolved_thread_count", lambda *_: 1)
    monkeypatch.setattr(
        "lantern.github.fetch_open_pull_requests",
        lambda owner, repo, token, **kw: [_make_pr(1)] if repo == "live" else [],
    )

    jobs, _ = pr_sweep.discover_eligible_prs(
        owner="user", token=None, forge_url="", skip_forks=True, skip_frozen=False
    )

    assert all(j["repo"] == "user/live" for j in jobs)


def test_discover_eligible_prs_preserves_archived_flag_in_rest_fallback(monkeypatch):
    api_repos = [
        {"name": "user/live", "fork": False, "archived": False},
        {"name": "user/old", "fork": False, "archived": True},
    ]
    monkeypatch.setattr(pr_sweep, "list_owner_repos", lambda owner: None)
    monkeypatch.setattr("lantern.forge.fetch_repos", lambda *args, **kwargs: api_repos)
    monkeypatch.setattr(pr_sweep, "fetch_pr_unresolved_thread_count", lambda *_: 1)
    monkeypatch.setattr(
        "lantern.github.fetch_open_pull_requests",
        lambda owner, repo, token, **kw: [_make_pr(1)],
    )

    jobs, _ = pr_sweep.discover_eligible_prs(
        owner="user", token=None, forge_url="", skip_forks=True, skip_frozen=False
    )

    assert [j["repo"] for j in jobs] == ["user/live"]


def test_discover_eligible_prs_excludes_frozen(monkeypatch):
    repos = [
        {"full_name": "user/active", "fork": False, "archived": False},
        {"full_name": "user/frozen-proj", "fork": False, "archived": False},
    ]
    monkeypatch.setattr(pr_sweep, "list_owner_repos", lambda owner: repos)
    monkeypatch.setattr(pr_sweep, "fetch_pr_unresolved_thread_count", lambda *_: 2)
    monkeypatch.setattr(
        "lantern.github.fetch_open_pull_requests",
        lambda owner, repo, token, **kw: [_make_pr(1)],
    )
    monkeypatch.setattr(
        forge_client,
        "fetch_frozen_repos",
        lambda url, **kw: {"user/frozen-proj"},
    )

    jobs, warnings = pr_sweep.discover_eligible_prs(
        owner="user",
        token=None,
        forge_url="http://localhost:8000",
        skip_forks=True,
        skip_frozen=True,
    )

    repos_in_jobs = {j["repo"] for j in jobs}
    assert "user/active" in repos_in_jobs
    assert "user/frozen-proj" not in repos_in_jobs
    assert warnings == []


def test_discover_eligible_prs_forge_mind_fallback(monkeypatch):
    """Graceful degradation when forge-mind is unreachable."""
    repos = [{"full_name": "user/repo-a", "fork": False, "archived": False}]
    monkeypatch.setattr(pr_sweep, "list_owner_repos", lambda owner: repos)
    monkeypatch.setattr(pr_sweep, "fetch_pr_unresolved_thread_count", lambda *_: 1)
    monkeypatch.setattr(
        "lantern.github.fetch_open_pull_requests",
        lambda owner, repo, token, **kw: [_make_pr(1)],
    )

    import urllib.error
    monkeypatch.setattr(
        forge_client,
        "fetch_frozen_repos",
        lambda url, **kw: (_ for _ in ()).throw(urllib.error.URLError("connection refused")),
    )

    jobs, warnings = pr_sweep.discover_eligible_prs(
        owner="user",
        token=None,
        forge_url="http://localhost:8000",
        skip_forks=True,
        skip_frozen=True,
    )

    # Should still return results despite forge-mind failure.
    assert len(jobs) == 1
    assert any("forge-mind" in w for w in warnings)


def test_discover_eligible_prs_repos_filter(monkeypatch):
    repos = [
        {"full_name": "user/repo-a", "fork": False, "archived": False},
        {"full_name": "user/repo-b", "fork": False, "archived": False},
    ]
    monkeypatch.setattr(pr_sweep, "list_owner_repos", lambda owner: repos)
    monkeypatch.setattr(pr_sweep, "fetch_pr_unresolved_thread_count", lambda *_: 1)
    monkeypatch.setattr(
        "lantern.github.fetch_open_pull_requests",
        lambda owner, repo, token, **kw: [_make_pr(1)],
    )

    jobs, _ = pr_sweep.discover_eligible_prs(
        owner="user",
        token=None,
        forge_url="",
        skip_forks=True,
        skip_frozen=False,
        repos_filter=["user/repo-a"],
    )

    assert all(j["repo"] == "user/repo-a" for j in jobs)


def test_discover_eligible_prs_skips_prs_with_zero_unresolved(monkeypatch):
    repos = [{"full_name": "user/repo", "fork": False, "archived": False}]
    monkeypatch.setattr(pr_sweep, "list_owner_repos", lambda owner: repos)
    # All threads resolved.
    monkeypatch.setattr(pr_sweep, "fetch_pr_unresolved_thread_count", lambda *_: 0)
    monkeypatch.setattr(
        "lantern.github.fetch_open_pull_requests",
        lambda owner, repo, token, **kw: [_make_pr(1), _make_pr(2)],
    )

    jobs, _ = pr_sweep.discover_eligible_prs(
        owner="user", token=None, forge_url="", skip_forks=True, skip_frozen=False
    )

    assert jobs == []


def test_discover_eligible_prs_skips_prs_with_unknown_unresolved_count(monkeypatch):
    repos = [{"full_name": "user/repo", "fork": False, "archived": False}]
    monkeypatch.setattr(pr_sweep, "list_owner_repos", lambda owner: repos)
    monkeypatch.setattr(pr_sweep, "fetch_pr_unresolved_thread_count", lambda *_: -1)
    monkeypatch.setattr(
        "lantern.github.fetch_open_pull_requests",
        lambda owner, repo, token, **kw: [_make_pr(1)],
    )

    jobs, warnings = pr_sweep.discover_eligible_prs(
        owner="user", token=None, forge_url="", skip_forks=True, skip_frozen=False
    )

    assert jobs == []
    assert any("could not be determined" in warning for warning in warnings)


# ---------------------------------------------------------------------------
# cmd_pr_sweep end-to-end tests (parser + command handler)
# ---------------------------------------------------------------------------


from lantern import cli  # noqa: E402


def _sweep_args(**overrides):
    parser = cli.build_parser()
    argv = ["pr", "sweep", "--owner", "user"]
    argv += overrides.pop("argv_extra", [])
    args = parser.parse_args(argv)
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


def _patch_github_server(monkeypatch):
    monkeypatch.setattr(cli.lantern_config, "load_config", lambda: {})
    monkeypatch.setattr(
        cli.lantern_config,
        "get_server",
        lambda cfg, name="": {"name": "github.com", "provider": "github", "token": ""},
    )


def test_pr_sweep_parser_repos_positional():
    parser = cli.build_parser()
    args = parser.parse_args(["pr", "sweep", "user/a", "user/b", "--dry-run"])
    assert args.repos == ["user/a", "user/b"]
    assert args.dry_run is True
    assert args.func == cli.cmd_pr_sweep


def test_cmd_pr_sweep_dry_run_lists_jobs(monkeypatch, capsys):
    _patch_github_server(monkeypatch)
    monkeypatch.setattr(
        cli, "render_table", lambda rows, cols: "\n".join(r["repo"] for r in rows)
    )
    monkeypatch.setattr(
        "lantern.pr_sweep.discover_eligible_prs",
        lambda **kw: (
            [{"repo": "user/a", "pr": 7, "title": "Fix", "url": "u", "unresolved_threads": 2}],
            [],
        ),
    )

    args = _sweep_args(argv_extra=["--dry-run"])
    rc = cli.cmd_pr_sweep(args)

    out = capsys.readouterr().out
    assert rc == 0
    assert "DRY-RUN" in out
    assert "user/a" in out


def test_cmd_pr_sweep_json_output(monkeypatch, capsys):
    _patch_github_server(monkeypatch)
    job = {"repo": "user/a", "pr": 7, "title": "Fix", "url": "u", "unresolved_threads": 2}
    monkeypatch.setattr(
        "lantern.pr_sweep.discover_eligible_prs", lambda **kw: ([job], [])
    )

    args = _sweep_args(argv_extra=["--json"])
    rc = cli.cmd_pr_sweep(args)

    out = capsys.readouterr().out
    assert rc == 0
    assert json.loads(out) == [job]


def test_cmd_pr_sweep_no_jobs(monkeypatch, capsys):
    _patch_github_server(monkeypatch)
    monkeypatch.setattr(
        "lantern.pr_sweep.discover_eligible_prs", lambda **kw: ([], [])
    )

    rc = cli.cmd_pr_sweep(_sweep_args())

    out = capsys.readouterr().out
    assert rc == 0
    assert "No eligible PRs" in out


def test_cmd_pr_sweep_rejects_non_github_provider(monkeypatch, capsys):
    monkeypatch.setattr(cli.lantern_config, "load_config", lambda: {})
    monkeypatch.setattr(
        cli.lantern_config,
        "get_server",
        lambda cfg, name="": {"name": "gitlab.com", "provider": "gitlab", "token": ""},
    )
    called = {"discover": False}

    def _fail(**kw):
        called["discover"] = True
        return ([], [])

    monkeypatch.setattr("lantern.pr_sweep.discover_eligible_prs", _fail)

    rc = cli.cmd_pr_sweep(_sweep_args())

    err = capsys.readouterr().err
    assert rc == 1
    assert "GitHub only" in err
    assert called["discover"] is False
