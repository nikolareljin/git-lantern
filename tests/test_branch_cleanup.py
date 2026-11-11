import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lantern import cleanup, cleanup_cmd, cleanup_forge, cli, forge  # noqa: E402


NOW = datetime(2026, 11, 10, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def quiet_progress_dialogs(monkeypatch):
    monkeypatch.setattr(cli, "_dialog_infobox", lambda *args, **kwargs: None)


def command(path, *args):
    env = dict(os.environ, GIT_AUTHOR_DATE="2020-01-01T00:00:00Z", GIT_COMMITTER_DATE="2020-01-01T00:00:00Z")
    return subprocess.run(["git", "-C", str(path), *args], env=env, check=True,
                          capture_output=True, text=True).stdout.strip()


class Provider:
    identity = "https://example.test/team/repo"
    default_branch = "main"

    def verify_origin(self, value):
        pass

    def refresh(self):
        pass

    def protected(self, name):
        return False

    def activity(self, name, commits):
        return cleanup_forge.Activity()


@pytest.fixture
def repo(tmp_path):
    remote = tmp_path / "origin.git"
    remote.mkdir()
    command(remote, "init", "--bare", "--initial-branch=main")
    local = tmp_path / "clone"
    command(tmp_path, "clone", str(remote), str(local))
    command(local, "config", "user.name", "Test User")
    command(local, "config", "user.email", "test@example.test")
    (local / "file").write_text("base\n")
    command(local, "add", "file")
    command(local, "commit", "-m", "base")
    command(local, "push", "-u", "origin", "main")
    command(local, "checkout", "-b", "old-feature")
    (local / "file").write_text("base\nfeature\n")
    command(local, "commit", "-am", "feature")
    command(local, "push", "-u", "origin", "old-feature")
    command(local, "checkout", "main")
    command(local, "merge", "--no-ff", "old-feature", "-m", "merge feature")
    command(local, "push")
    return str(local)


def aged_scan(path, store, provider=None):
    provider = provider or Provider()
    cleanup.scan(path, provider, store, "30d", NOW - timedelta(days=40))
    return cleanup.scan(path, provider, store, "30d", NOW)


def test_first_seen_not_commit_age_controls_eligibility(repo, tmp_path):
    with cleanup.Observations(tmp_path / "state.json") as store:
        rows = cleanup.scan(repo, Provider(), store, "30d", NOW)
        assert all(r.reason for r in rows)
        assert "first observation" in next(r.reason for r in rows if r.name == "old-feature")
    with cleanup.Observations(tmp_path / "state.json") as store:
        rows = cleanup.scan(repo, Provider(), store, "30d", NOW + timedelta(days=31))
        assert len([r for r in rows if not r.reason]) == 2


def test_actual_local_and_remote_deletion(repo, tmp_path):
    with cleanup.Observations(tmp_path / "state.json") as store:
        rows = aged_scan(repo, store)
        eligible = [r for r in rows if not r.reason]
        assert {(r.scope, r.name) for r in eligible} == {("local", "old-feature"), ("remote", "old-feature")}
        for row in eligible:
            cleanup.delete(repo, Provider(), row)
        assert command(repo, "branch", "--list", "old-feature") == ""
        assert command(repo, "ls-remote", "--heads", "origin", "old-feature") == ""
        assert command(repo, "ls-remote", "--heads", "origin", "main")


def test_new_ref_to_old_commit_is_never_selected(repo, tmp_path):
    with cleanup.Observations(tmp_path / "state.json") as store:
        aged_scan(repo, store)
        command(repo, "branch", "brand-new", "old-feature")
        command(repo, "push", "-u", "origin", "brand-new")
        rows = cleanup.scan(repo, Provider(), store, "30d", NOW)
        assert all(r.reason for r in rows if r.name == "brand-new")


def test_worktrees_and_unpushed_commits_are_excluded(repo, tmp_path):
    command(repo, "worktree", "add", str(tmp_path / "worktree"), "old-feature")
    with cleanup.Observations(tmp_path / "state.json") as store:
        rows = aged_scan(repo, store)
        assert all("worktree" in r.reason for r in rows if r.name == "old-feature")
    command(repo, "worktree", "remove", str(tmp_path / "worktree"))
    command(repo, "checkout", "old-feature")
    (Path(repo) / "file").write_text("new work\n")
    command(repo, "commit", "-am", "unpushed")
    command(repo, "checkout", "main")
    with cleanup.Observations(tmp_path / "other.json") as store:
        rows = aged_scan(repo, store)
        assert "unpushed" in next(r.reason for r in rows if r.name == "old-feature" and r.scope == "local")


def test_remote_lease_refuses_a_changed_tip(repo, tmp_path):
    with cleanup.Observations(tmp_path / "state.json") as store:
        row = next(r for r in aged_scan(repo, store) if r.name == "old-feature" and r.scope == "remote")
        command(repo, "push", "origin", "main:old-feature")
        with pytest.raises(cleanup.Unsafe):
            cleanup.delete(repo, Provider(), row)
        assert command(repo, "ls-remote", "--heads", "origin", "old-feature")


def test_local_compare_and_swap_refuses_a_changed_tip(repo, tmp_path):
    with cleanup.Observations(tmp_path / "state.json") as store:
        row = next(r for r in aged_scan(repo, store) if r.name == "old-feature" and r.scope == "local")
        command(repo, "branch", "-f", "old-feature", "main")
        with pytest.raises(cleanup.Unsafe, match="changed"):
            cleanup.delete(repo, Provider(), row)


@pytest.mark.parametrize("mode", ["blocked", "recent", "failed"])
def test_server_activity_always_prevents_selection(repo, tmp_path, mode):
    class Active(Provider):
        def activity(self, name, commits):
            if mode == "failed":
                raise cleanup.Unsafe("API unavailable")
            return cleanup_forge.Activity([NOW] if mode == "recent" else [], "open or Draft PR" if mode == "blocked" else "")

    with cleanup.Observations(tmp_path / "state.json") as store:
        rows = aged_scan(repo, store, Active())
        assert all(r.reason for r in rows)


def test_squash_merge_and_post_merge_commit(repo):
    base = command(repo, "rev-parse", "main")
    command(repo, "checkout", "-b", "squashed")
    (Path(repo) / "squash").write_text("first\n")
    command(repo, "add", "squash")
    command(repo, "commit", "-m", "first")
    (Path(repo) / "squash").write_text("first\nsecond\n")
    command(repo, "commit", "-am", "second")
    tip = command(repo, "rev-parse", "HEAD")
    command(repo, "checkout", "main")
    command(repo, "merge", "--squash", "squashed")
    command(repo, "commit", "-m", "squash")
    merged = command(repo, "rev-parse", "HEAD")
    assert cleanup.merge_state(repo, merged, tip)
    assert not cleanup.merge_state(repo, base, tip)
    command(repo, "checkout", "squashed")
    (Path(repo) / "squash").write_text(" first\nsecond\n")
    command(repo, "commit", "-am", "significant whitespace")
    assert not cleanup.merge_state(repo, merged, command(repo, "rev-parse", "HEAD"))


def test_calendar_cutoffs_and_boundary():
    assert cleanup.cutoff("6m", datetime(2026, 8, 31, tzinfo=timezone.utc)) == datetime(2026, 2, 28, tzinfo=timezone.utc)
    assert cleanup.cutoff("1y", datetime(2024, 2, 29, tzinfo=timezone.utc)) == datetime(2023, 2, 28, tzinfo=timezone.utc)
    assert cleanup.cutoff("90d", NOW) == NOW - timedelta(days=90)


def test_observation_corruption_changes_and_missing_refs(tmp_path):
    state = tmp_path / "state.json"
    state.write_text("bad json")
    with cleanup.Observations(state) as store:
        assert store.observe("repo:branch", "aaa", NOW) == NOW
        assert store.observe("repo:branch", "aaa", NOW + timedelta(days=40)) == NOW
        assert store.observe("repo:branch", "bbb", NOW + timedelta(days=40)) != NOW
        store.forget_missing("repo:", [])
        assert not store.entries


def adapter(provider="github"):
    result = cleanup_forge.Provider(provider, "", "user", "token", None, "team/repo")
    result.meta = {"id": 42, "uuid": "uuid"}
    result.github_associated_prs = lambda name: result.prs
    return result


@pytest.mark.parametrize("provider,pr", [
    ("github", {"head": {"ref": "old", "repo": {"full_name": "team/repo"}}, "state": "open", "draft": True}),
    ("gitlab", {"source_branch": "old", "source_project_id": 42, "state": "opened", "draft": True}),
    ("bitbucket", {"source": {"branch": {"name": "old"}, "repository": {"uuid": "uuid"}}, "state": "OPEN", "draft": True}),
])
def test_all_providers_exclude_drafts(provider, pr):
    api = adapter(provider)
    api.prs = [pr]
    assert api.activity("old", set()).blocked


def test_github_fork_same_branch_does_not_block(monkeypatch):
    api = adapter()
    api.prs = [{"head": {"ref": "old", "repo": {"full_name": "fork/repo"}}, "state": "open"}]
    assert not api.activity("old", set()).blocked


def test_github_unresolved_thread_blocks(monkeypatch):
    api = adapter()
    api.prs = [{"head": {"ref": "old", "repo": {"full_name": "team/repo"}}, "state": "closed", "number": 1}]
    monkeypatch.setattr(api, "github_threads", lambda _, target=None: True)
    assert "unresolved" in api.activity("old", set()).blocked


def test_gitlab_unresolved_and_edited_comments():
    api = adapter("gitlab")
    activity = cleanup_forge.Activity()
    api.add_comments(activity, [{"resolvable": True, "resolved": False,
                                 "created_at": "2020-01-01T00:00:00Z", "updated_at": NOW.isoformat()}])
    assert activity.blocked and max(activity.dates) == NOW
    with pytest.raises(cleanup.Unsafe):
        api.add_comments(activity, [{"body": "missing dates"}])


def test_pagination_and_untrusted_next(monkeypatch):
    api = adapter()
    calls = []

    def request(url):
        calls.append(url)
        if len(calls) == 1:
            return [{"number": 1}], {"link": '<https://api.github.com/next>; rel="next"'}
        return [{"number": 2}], {}

    monkeypatch.setattr(api, "request", request)
    assert len(api.pages("/pulls?state=all")) == 2
    assert calls[1] == "https://api.github.com/next"
    real = adapter()
    with pytest.raises(cleanup.Unsafe, match="untrusted"):
        real.request("https://untrusted.test/next")


def test_cli_defaults_and_noninteractive_refusal(monkeypatch):
    args = cli.build_parser().parse_args(["fleet", "cleanup"])
    assert args.older_than == "30d" and not args.confirm_first and not args.apply
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(cleanup_cmd, "repositories", lambda _: pytest.fail("must reject before discovery"))
    assert cleanup_cmd.command(args) == 1


def test_cli_dry_run_never_deletes(monkeypatch, tmp_path):
    args = cli.build_parser().parse_args(["fleet", "cleanup", "--repos", "team/repo", "--dry-run"])
    monkeypatch.setattr(cleanup_cmd, "repositories", lambda _: [{"name": "team/repo", "path": "clone", "adapter": Provider()}])
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(cleanup, "scan", lambda *a: [cleanup.Candidate("old", "remote", "sha", merged=True)])
    monkeypatch.setattr(cleanup, "delete", lambda *a: pytest.fail("dry-run deleted a branch"))
    assert cleanup_cmd.command(args) == 0


def test_confirm_first_unmerged_unchecked_and_cancel(monkeypatch, tmp_path):
    args = cli.build_parser().parse_args(["fleet", "cleanup", "--repos", "team/repo", "--confirm-first", "--apply"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli, "_dialog_available", lambda: True)
    monkeypatch.setattr(cli, "_dialog_textbox_from_text", lambda *args: True)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(cleanup_cmd, "repositories", lambda _: [{"name": "team/repo", "path": "clone", "adapter": Provider()}])
    rows = [cleanup.Candidate("merged", "remote", "a", merged=True), cleanup.Candidate("unmerged", "remote", "b")]
    monkeypatch.setattr(cleanup, "scan", lambda *a: rows)

    def choose(title, text, items, **kwargs):
        assert [i[2] for i in items] == [True, False]
        assert "creator=unknown" in items[0][1]
        return ["0", "1"]

    monkeypatch.setattr(cleanup_cmd, "choose", choose)
    monkeypatch.setattr(cli, "_dialog_yesno", lambda *a, **kw: False)
    monkeypatch.setattr(cleanup, "delete", lambda *a: pytest.fail("cancelled confirmation deleted a branch"))
    assert cleanup_cmd.command(args) == 0


@pytest.mark.parametrize("provider", ["gitlab", "bitbucket"])
def test_group_scope_reaches_provider(monkeypatch, provider):
    called = []

    def fetch(*args, **kwargs):
        called.append((args, kwargs))
        return [{"name": "group/repo"}]

    monkeypatch.setattr(forge, "_fetch_" + provider + "_repos", fetch)
    repos = forge.fetch_repos(provider, "user", "token", False, "", organizations=[{"name": "group"}], include_user=False)
    assert repos == [{"name": "group/repo"}]
    assert len(called) == 1
    assert called[0][1]["group" if provider == "gitlab" else "workspace"] == "group"


def test_tui_cancellation_never_runs(monkeypatch):
    monkeypatch.setattr(cli.lantern_config, "load_config", lambda: {})
    monkeypatch.setattr(cli, "_tui_select_server", lambda *a: "github.com")
    monkeypatch.setattr(cli, "_dialog_menu", lambda *a: None)
    monkeypatch.setattr(cleanup_cmd, "command", lambda *a: pytest.fail("cancelled TUI ran cleanup"))
    cleanup_cmd.tui({}, 24, 80)


def test_github_outgoing_ref_associations_paginate(monkeypatch):
    api = adapter()
    del api.github_associated_prs
    calls = []

    def request(url, data=None):
        calls.append((url, data))
        if data:
            cursor = data["variables"]["cursor"]
            return {"data": {"repository": {"ref": {"associatedPullRequests": {
                "nodes": [{"number": 7 if cursor is None else 8, "state": "OPEN",
                           "repository": {"nameWithOwner": "upstream/repo"},
                           "headRepository": {"nameWithOwner": "team/repo"}, "headRefName": "old"}],
                "pageInfo": {"hasNextPage": cursor is None, "endCursor": "next" if cursor is None else None},
            }}}}}, {}
        return {"head": {"ref": "old", "repo": {"full_name": "team/repo"}}, "state": "open"}, {}

    monkeypatch.setattr(api, "request", request)
    prs = api.github_associated_prs("old")
    assert len(prs) == 2
    assert prs[0]["_cleanup_target"] == "upstream/repo"
    assert all("/repos/upstream/repo/pulls/" in url for url, data in calls if data is None)
    assert "states:[OPEN,CLOSED,MERGED]" in calls[0][1]["query"]


def test_github_threads_paginate_and_refuse_partial_graphql(monkeypatch):
    api = adapter()
    responses = iter([
        {"data": {"repository": {"pullRequest": {"reviewThreads": {
            "nodes": [{"isResolved": True}], "pageInfo": {"hasNextPage": True, "endCursor": "more"}}}}}},
        {"data": {"repository": {"pullRequest": {"reviewThreads": {
            "nodes": [{"isResolved": False}], "pageInfo": {"hasNextPage": False}}}}}},
    ])
    monkeypatch.setattr(api, "request", lambda *args: (next(responses), {}))
    assert api.github_threads(1)
    monkeypatch.setattr(api, "request", lambda *args: ({"errors": [{"message": "denied"}]}, {}))
    with pytest.raises(cleanup.Unsafe, match="unavailable"):
        api.github_threads(1)


def test_github_truncated_pr_commits_are_not_safe(monkeypatch):
    api = adapter()
    api.prs = [{"head": {"ref": "old", "repo": {"full_name": "team/repo"}},
                "state": "closed", "number": 1, "commits": 251}]
    monkeypatch.setattr(api, "github_threads", lambda *args: False)
    monkeypatch.setattr(api, "pages", lambda url: [{"sha": "a"}] if url.endswith("/commits") else [])
    with pytest.raises(cleanup.Unsafe, match="incomplete"):
        api.activity("old", set())


def test_gitlab_outgoing_mr_checks_target_discussions(monkeypatch):
    api = adapter("gitlab")
    api.prs = [{"source_branch": "old", "source_project_id": 42, "target_project_id": 99,
                "state": "merged", "iid": 7}]
    called = []

    def pages(url):
        called.append(url)
        if url.endswith("/discussions"):
            return [{"notes": [{"created_at": "2020-01-01T00:00:00Z", "resolvable": True, "resolved": False}]}]
        return []

    monkeypatch.setattr(api, "pages", pages)
    assert api.activity("old", set()).blocked
    assert "https://gitlab.com/api/v4/projects/99/merge_requests/7/discussions" in called


def test_gitlab_global_mr_discovery(monkeypatch):
    api = adapter("gitlab")
    monkeypatch.setattr(api, "request", lambda *args: ({"default_branch": "main", "id": 42}, {}))
    called = []
    monkeypatch.setattr(api, "pages", lambda url: called.append(url) or [])
    api.refresh()
    assert "https://gitlab.com/api/v4/merge_requests?state=all&scope=all" not in called
    api.activity("old", set())
    assert "https://gitlab.com/api/v4/merge_requests?state=all&scope=all" in called


def test_bitbucket_fork_network_checks_outgoing_prs(monkeypatch):
    api = adapter("bitbucket")

    def request(url):
        name = "upstream/repo" if url.endswith("upstream/repo") else "team/repo"
        meta = {"full_name": name, "uuid": name}
        if name == "team/repo":
            meta["parent"] = {"full_name": "upstream/repo"}
        return meta, {}

    def pages(url):
        if url.endswith("/forks"):
            return [{"full_name": "team/repo"}] if "/upstream/repo/" in url else []
        if "/upstream/repo/" in url:
            return [{"state": "OPEN", "source": {"branch": {"name": "old"}, "repository": {"uuid": "uuid"}}}]
        return []

    monkeypatch.setattr(api, "request", request)
    monkeypatch.setattr(api, "pages", pages)
    api.prs = api.bitbucket_network_prs({"full_name": "team/repo"})
    assert len(api.prs) == 1
    assert api.prs[0]["_cleanup_target"] == "upstream/repo"
    assert api.activity("old", set()).blocked


def test_bitbucket_unknown_old_comment_resolution_blocks(monkeypatch):
    api = adapter("bitbucket")
    api.prs = [{"id": 1, "state": "MERGED", "source": {
        "branch": {"name": "old"}, "repository": {"uuid": "uuid"}}}]
    monkeypatch.setattr(api, "pages", lambda url: [{"created_on": "2020-01-01T00:00:00Z"}]
                        if url.endswith("/comments") else [])
    assert api.activity("old", set()).blocked


@pytest.mark.parametrize("provider,response,headers", [
    ("gitlab", [{"id": 1}], {"x-next-page": "2"}),
    ("bitbucket", {"values": [{"id": 1}], "next": "https://api.bitbucket.org/2.0/next"}, {}),
])
def test_provider_pagination(provider, response, headers, monkeypatch):
    api = adapter(provider)
    calls = []

    def request(url):
        calls.append(url)
        if len(calls) == 1:
            return response, headers
        return ({"values": []} if provider == "bitbucket" else []), {}

    monkeypatch.setattr(api, "request", request)
    assert api.pages("/test") == [{"id": 1}]
    assert len(calls) == 2
    if provider == "gitlab":
        assert "page=2" in calls[1]


def test_empty_repo_selection_never_scans(monkeypatch):
    args = cli.build_parser().parse_args(["fleet", "cleanup", "--select", "--apply"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli, "_dialog_available", lambda: True)
    monkeypatch.setattr(cleanup_cmd, "repositories", lambda _: [{"name": "team/repo", "path": "clone", "adapter": Provider()}])
    monkeypatch.setattr(cleanup_cmd, "choose", lambda *args: [])
    monkeypatch.setattr(cleanup, "scan", lambda *args: pytest.fail("empty selection scanned repositories"))
    assert cleanup_cmd.command(args) == 0


def test_apply_without_confirmation_never_deletes_unmerged(monkeypatch, tmp_path):
    args = cli.build_parser().parse_args(["fleet", "cleanup", "--repos", "team/repo", "--apply"])
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(cleanup_cmd, "repositories", lambda _: [{"name": "team/repo", "path": "clone", "adapter": Provider()}])
    rows = [cleanup.Candidate("merged", "remote", "a", merged=True), cleanup.Candidate("unmerged", "remote", "b")]
    monkeypatch.setattr(cleanup, "scan", lambda *args: rows)
    deleted = []
    monkeypatch.setattr(cleanup, "delete", lambda path, api, row: deleted.append(row.name))
    assert cleanup_cmd.command(args) == 0
    assert deleted == ["merged"]


def test_changed_evidence_after_selection_refuses_deletion(monkeypatch, tmp_path):
    args = cli.build_parser().parse_args(["fleet", "cleanup", "--repos", "team/repo", "--apply"])
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(cleanup_cmd, "repositories", lambda _: [{"name": "team/repo", "path": "clone", "adapter": Provider()}])
    rows = iter([[cleanup.Candidate("old", "remote", "a", merged=True)],
                 [cleanup.Candidate("old", "remote", "a", merged=True, reason="open PR")]])
    monkeypatch.setattr(cleanup, "scan", lambda *args: next(rows))
    monkeypatch.setattr(cleanup, "delete", lambda *args: pytest.fail("changed evidence permitted deletion"))
    assert cleanup_cmd.command(args) == 0


def test_rebase_merge_classification(repo):
    command(repo, "checkout", "-b", "rebased")
    for name in ("a", "b"):
        (Path(repo) / name).write_text(name + "\n")
        command(repo, "add", name)
        command(repo, "commit", "-m", name)
    tip = command(repo, "rev-parse", "HEAD")
    commits = command(repo, "rev-list", "--reverse", "main..rebased").splitlines()
    command(repo, "checkout", "main")
    (Path(repo) / "unrelated").write_text("different parent\n")
    command(repo, "add", "unrelated")
    command(repo, "commit", "-m", "advance base")
    for sha in commits:
        command(repo, "cherry-pick", sha)
    assert cleanup.merge_state(repo, command(repo, "rev-parse", "HEAD"), tip)


def test_merge_driver_cannot_discard_unmerged_content(repo):
    command(repo, "config", "merge.discard.driver", "true")
    (Path(repo) / ".gitattributes").write_text("file merge=discard\n")
    command(repo, "add", ".gitattributes")
    command(repo, "commit", "-m", "attributes")
    command(repo, "checkout", "-b", "driver-test")
    (Path(repo) / "file").write_text("branch content\n")
    command(repo, "commit", "-am", "branch")
    tip = command(repo, "rev-parse", "HEAD")
    command(repo, "checkout", "main")
    (Path(repo) / "file").write_text("base content\n")
    command(repo, "commit", "-am", "base")
    assert not cleanup.merge_state(repo, command(repo, "rev-parse", "HEAD"), tip)


def test_fetch_does_not_prune_tags_or_local_heads(repo, tmp_path):
    command(repo, "tag", "local-tag")
    command(repo, "config", "--add", "remote.origin.fetch", "+refs/heads/*:refs/heads/*")
    command(repo, "config", "fetch.pruneTags", "true")
    with cleanup.Observations(tmp_path / "state.json") as store:
        aged_scan(repo, store)
    assert command(repo, "tag", "--list", "local-tag") == "local-tag"
    assert command(repo, "symbolic-ref", "--short", "HEAD") == "main"


def test_binary_post_merge_change_is_preserved(repo):
    command(repo, "checkout", "-b", "binary")
    (Path(repo) / "blob").write_bytes(b"\0first")
    command(repo, "add", "blob")
    command(repo, "commit", "-m", "binary")
    tip = command(repo, "rev-parse", "HEAD")
    command(repo, "checkout", "main")
    command(repo, "merge", "--squash", "binary")
    command(repo, "commit", "-m", "land binary")
    base = command(repo, "rev-parse", "HEAD")
    assert cleanup.merge_state(repo, base, tip)
    command(repo, "checkout", "binary")
    (Path(repo) / "blob").write_bytes(b"\0second")
    command(repo, "commit", "-am", "new binary")
    assert not cleanup.merge_state(repo, base, command(repo, "rev-parse", "HEAD"))


def test_bitbucket_workspace_keeps_basic_auth_user(monkeypatch):
    import base64

    calls = []

    def request(url, headers):
        calls.append((url, headers))
        return {"values": []}, {}

    monkeypatch.setattr(forge, "_request", request)
    forge.fetch_repos("bitbucket", "login", "token", False, "", {"type": "basic"},
                      organizations=[{"name": "workspace"}], include_user=False)
    assert "/repositories/workspace?" in calls[0][0]
    assert calls[0][1]["Authorization"] == "Basic " + base64.b64encode(b"login:token").decode()


@pytest.mark.parametrize("selection,confirm,expected", [(["1"], True, ["unmerged"]), (["0"], True, ["merged"]), ([], True, []), (["0"], False, [])])
def test_confirm_first_selects_before_deletion(monkeypatch, tmp_path, selection, confirm, expected):
    args = cli.build_parser().parse_args(["fleet", "cleanup", "--repos", "team/repo", "--confirm-first", "--apply"])
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli, "_dialog_available", lambda: True)
    monkeypatch.setattr(cleanup_cmd, "repositories", lambda _: [{"name": "team/repo", "path": "clone", "adapter": Provider()}])
    rows = [cleanup.Candidate("merged", "remote", "a", merged=True),
            cleanup.Candidate("unmerged", "remote", "b"),
            cleanup.Candidate("open-pr", "remote", "c", merged=True, reason="open PR"),
            cleanup.Candidate("draft-pr", "remote", "d", merged=True, reason="draft PR"),
            cleanup.Candidate("new", "remote", "e", reason="branch is too new"),
            cleanup.Candidate("discussion", "remote", "f", reason="active discussion")]
    monkeypatch.setattr(cleanup, "scan", lambda *args: rows)
    monkeypatch.setattr(cli, "_dialog_textbox_from_text", lambda *args: pytest.fail("read-only screen blocked branch selection"))
    events = []

    def choose(title, text, items, **kwargs):
        assert len(items) == 2
        assert [item[2] for item in items] == [True, False]
        assert "merged" in items[0][1] and "unmerged" in items[1][1]
        for field in ("creator=", "created=", "last-author=", "last-committer=", "last-commit="):
            assert all(field in item[1] for item in items)
        events.append("select")
        return selection

    def approve(*args, **kwargs):
        assert events == ["select"]
        events.append("confirm")
        return confirm

    deleted = []
    monkeypatch.setattr(cleanup_cmd, "choose", choose)
    monkeypatch.setattr(cli, "_dialog_yesno", approve)
    def delete(path, adapter, row):
        assert events == ["select", "confirm"]
        deleted.append(row.name)
    monkeypatch.setattr(cleanup, "delete", delete)
    assert cleanup_cmd.command(args) == 0
    assert events[0] == "select"
    assert deleted == expected


def test_concurrent_cleanup_refuses_same_observation_store(tmp_path):
    path = tmp_path / "state.json"
    with cleanup.Observations(path):
        with pytest.raises(cleanup.Unsafe, match="another branch cleanup"):
            with cleanup.Observations(path):
                pytest.fail("concurrent cleanup acquired the same lock")


def test_inherited_git_context_cannot_redirect_cleanup(repo, tmp_path, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    command(other, "init")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    monkeypatch.setenv("GIT_INDEX_FILE", str(other / "index"))
    assert cleanup.git(repo, "rev-parse", "--show-toplevel").stdout.strip() == repo
    with cleanup.Observations(tmp_path / "state.json") as store:
        assert len([row for row in aged_scan(repo, store) if not row.reason]) == 2


def test_repeated_context_at_another_location_is_not_a_merge(repo):
    block = "start\na\nb\nc\nd\ne\nf\nend\n"
    (Path(repo) / "repeat").write_text(block * 4)
    command(repo, "add", "repeat")
    command(repo, "commit", "-m", "repeated blocks")
    command(repo, "checkout", "-b", "not-landed")
    (Path(repo) / "repeat").write_text(block.replace("d\n", "NEW\nd\n", 1) + block * 3)
    command(repo, "commit", "-am", "change first block")
    tip = command(repo, "rev-parse", "HEAD")
    command(repo, "checkout", "main")
    (Path(repo) / "repeat").write_text(block * 2 + block.replace("d\n", "NEW\nd\n", 1) + block)
    command(repo, "commit", "-am", "change third block")
    assert not cleanup.merge_state(repo, command(repo, "rev-parse", "HEAD"), tip)


@pytest.mark.parametrize("provider", ["github", "gitlab", "bitbucket"])
def test_server_id_change_resets_branch_age(monkeypatch, provider):
    api = adapter(provider)
    meta = {"id": 1, "uuid": "one", "default_branch": "main", "mainbranch": {"name": "main"}}
    monkeypatch.setattr(api, "request", lambda *args: (meta, {}))
    monkeypatch.setattr(api, "pages", lambda *args: [])
    monkeypatch.setattr(api, "bitbucket_network_prs", lambda *args: [])
    api.refresh()
    original = api.identity
    meta.update(id=2, uuid="two")
    api.refresh()
    assert api.identity != original


@pytest.mark.parametrize("name", ["clone with spaces", pytest.param(
    'clone with "quotes"', marks=pytest.mark.skipif(os.name == "nt", reason="Windows disallows quotes in directory names")
)])
def test_isolated_merge_reads_objects_from_quoted_paths(repo, name):
    moved = Path(repo).with_name(name)
    Path(repo).rename(moved)
    base = command(moved, "rev-parse", "main")
    tree = command(moved, "rev-parse", "main^{tree}")
    parent = command(moved, "rev-parse", "old-feature")
    equivalent = command(moved, "commit-tree", tree, "-p", parent, "-m", "equivalent tree")
    assert cleanup.merge_state(str(moved), base, equivalent)


def test_global_merge_driver_cannot_discard_branch_work(repo, tmp_path, monkeypatch):
    global_config = tmp_path / "global-config"
    global_config.write_text('[merge "discard"]\n\tdriver = true\n')
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    (Path(repo) / ".gitattributes").write_text("file merge=discard\n")
    command(repo, "add", ".gitattributes")
    command(repo, "commit", "-m", "attributes")
    command(repo, "checkout", "-b", "global-driver-test")
    (Path(repo) / "file").write_text("branch content\n")
    command(repo, "commit", "-am", "branch")
    tip = command(repo, "rev-parse", "HEAD")
    command(repo, "checkout", "main")
    (Path(repo) / "file").write_text("base content\n")
    command(repo, "commit", "-am", "base")
    assert not cleanup.merge_state(repo, command(repo, "rev-parse", "HEAD"), tip)


def test_fresh_observations_do_not_check_merge_or_discussions(repo, tmp_path, monkeypatch):
    api = Provider()
    monkeypatch.setattr(api, "activity", lambda *args: pytest.fail("new branch checked discussion history"))
    monkeypatch.setattr(cleanup, "merge_state", lambda *args: pytest.fail("new branch checked merge history"))
    with cleanup.Observations(tmp_path / "state.json") as store:
        rows = cleanup.scan(repo, api, store, "30d", NOW)
    assert all(row.reason for row in rows)
    assert any("first observation" in row.reason for row in rows)
    assert all("[not checked]" in row.label() for row in rows if "first observation" in row.reason)


@pytest.mark.parametrize("status,output,expected", [(0, "1\n", ["1"]), (0, "", []), (1, "1\n", [])])
def test_branch_checklist_metadata_lifetime_and_cancel(monkeypatch, status, output, expected):
    from types import SimpleNamespace

    files = []
    monkeypatch.setattr(cli, "_dialog_init", lambda: (24, 100))

    def dialog(command, **kwargs):
        assert "--checklist" in command
        assert "--textbox" not in command
        assert command[command.index("--ok-label") + 1] == "Review deletion"
        filename = Path(command[command.index("--hfile") + 1])
        assert filename.read_text() == "creator=Test; created=2020-01-01"
        files.append(filename)
        return SimpleNamespace(returncode=status, stdout=output)

    monkeypatch.setattr(cli.subprocess, "run", dialog)
    assert cleanup_cmd.choose("Branches", "Select", [("1", "old", False)],
                              details="creator=Test; created=2020-01-01") == expected
    assert len(files) == 1 and not files[0].exists()


@pytest.mark.parametrize("approve_unknown", [False, True])
def test_first_run_confirm_reviews_real_old_refs_with_explicit_age_approval(monkeypatch, tmp_path, repo, approve_unknown):
    args = cli.build_parser().parse_args(["fleet", "cleanup", "--repos", "team/repo", "--confirm-first", "--apply"])
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli, "_dialog_available", lambda: True)
    monkeypatch.setattr(cleanup_cmd, "repositories", lambda _: [{"name": "team/repo", "path": repo, "adapter": Provider()}])
    def choose(title, text, items, **kwargs):
        assert len(items) == 2
        unknown = [(tag, label, checked) for tag, label, checked in items if "remote old-feature" in label]
        assert len(unknown) == 1 and not unknown[0][2]
        assert "UNKNOWN - manual approval required" in unknown[0][1]
        return [unknown[0][0]]
    monkeypatch.setattr(cleanup_cmd, "choose", choose)
    monkeypatch.setattr(cli, "_dialog_msgbox", lambda *args: pytest.fail("old inactive branches were hidden"))
    prompts = []
    def confirm(title, text, **kwargs):
        assert kwargs["default_no"]
        prompts.append(title)
        if title == "Approve unknown branch creation age":
            assert "newly created refs" in text
            return approve_unknown
        return True
    monkeypatch.setattr(cli, "_dialog_yesno", confirm)
    assert cleanup_cmd.command(args) == 0
    assert prompts == (["Approve unknown branch creation age", "Confirm branch deletion"] if approve_unknown else ["Approve unknown branch creation age"])
    assert bool(command(repo, "ls-remote", "--heads", "origin", "old-feature")) is not approve_unknown
    assert command(repo, "branch", "--list", "old-feature")


@pytest.mark.parametrize("mode", ["blocked", "recent", "failed"])
def test_unknown_age_review_still_checks_server_activity(repo, tmp_path, monkeypatch, mode):
    api = Provider()
    def activity(*args):
        if mode == "failed":
            raise cleanup.Unsafe("PR checks unavailable")
        return cleanup_forge.Activity(blocked="open PR or unresolved discussion" if mode == "blocked" else "",
                                      dates=[NOW] if mode == "recent" else [])
    monkeypatch.setattr(api, "activity", activity)
    with cleanup.Observations(tmp_path / "state.json") as observations:
        rows = cleanup.scan(repo, api, observations, "30d", NOW, True)
    assert all(row.reason for row in rows)


def test_unknown_age_review_does_not_admit_recent_local_ref(repo, tmp_path):
    subprocess.run(["git", "-C", repo, "branch", "new-local", "old-feature"],check=True)
    command(repo, "push", "-u", "origin", "new-local")
    with cleanup.Observations(tmp_path / "state.json") as observations:
        rows = cleanup.scan(repo, Provider(), observations, "30d", None, True)
    row = next(row for row in rows if row.name == "new-local")
    assert row.reason == "recent branch activity"


def test_interrupt_stops_remaining_branch_deletions(monkeypatch, tmp_path):
    args = cli.build_parser().parse_args(["fleet", "cleanup", "--repos", "team/repo", "--apply"])
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(cleanup_cmd, "repositories", lambda _: [{"name": "team/repo", "path": "clone", "adapter": Provider()}])
    rows = [cleanup.Candidate("old-a", "remote", "a", merged=True), cleanup.Candidate("old-b", "remote", "b", merged=True)]
    monkeypatch.setattr(cleanup, "scan", lambda *args: rows)
    deleted = []
    def delete(path, adapter, row):
        deleted.append(row.name)
        raise KeyboardInterrupt
    monkeypatch.setattr(cleanup, "delete", delete)
    monkeypatch.setattr(cli, "_main", lambda: cleanup_cmd.run(args))
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 130 and deleted == ["old-a"]
    with cleanup.Observations() as observations:
        assert observations.entries == {}
