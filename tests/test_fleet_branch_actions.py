import argparse

from lantern import cli


def _make_apply_args(**overrides):
    base = dict(
        checkout_branch="",
        checkout_pr="",
        checkout_latest_branch=False,
        clone_missing=False,
        pull_behind=False,
        push_ahead=False,
        root=".",
        max_depth=1,
        include_hidden=False,
        fetch=False,
        server="",
        input="",
        user="",
        token="",
        include_forks=False,
        orgs=[],
        all_orgs=False,
        with_user=False,
        repos="",
        dry_run=True,
        only_clean=False,
        log_json="",
        with_prs=False,
        pr_stale_days=30,
        snapshot="",
        refresh=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_fleet_apply_parser_accepts_checkout_latest_branch_flag():
    parser = cli.build_parser()
    args = parser.parse_args(["fleet", "apply", "--checkout-latest-branch"])
    assert args.fleet_command == "apply"
    assert args.checkout_latest_branch is True


def test_fleet_action_parts_uses_latest_branch_hint_when_requested():
    row = {"state": "in-sync", "latest_branch": "feature/latest"}
    parts = cli._fleet_action_parts_for_row(
        row=row,
        clone_missing=False,
        pull_behind=False,
        push_ahead=False,
        checkout_branch="",
        checkout_pr="",
        checkout_latest_branch=True,
    )
    assert parts == ["checkout-latest:feature/latest"]


def test_fleet_action_parts_reports_missing_latest_branch():
    row = {"state": "in-sync", "latest_branch": "-"}
    parts = cli._fleet_action_parts_for_row(
        row=row,
        clone_missing=False,
        pull_behind=False,
        push_ahead=False,
        checkout_branch="",
        checkout_pr="",
        checkout_latest_branch=True,
    )
    assert parts == ["checkout-latest:skip-no-latest"]


def test_fleet_latest_branch_is_actionable_only_for_real_rollout_candidates():
    assert cli._fleet_latest_branch_is_actionable({"state": "missing-local", "branch": "-", "latest_branch": "release/1.0"}) is True
    assert cli._fleet_latest_branch_is_actionable({"state": "missing-local", "branch": "-", "latest_branch": "release/1.0"}, include_missing_local=False) is False
    assert cli._fleet_latest_branch_is_actionable({"state": "in-sync", "branch": "main", "latest_branch": "release/1.0"}) is True
    assert cli._fleet_latest_branch_is_actionable({"state": "behind-remote", "branch": "release/1.0", "latest_branch": "release/1.0"}) is True
    assert cli._fleet_latest_branch_is_actionable({"state": "in-sync", "branch": "release/1.0", "latest_branch": "release/1.0"}) is False
    assert cli._fleet_latest_branch_is_actionable({"state": "ahead-remote", "branch": "release/1.0", "latest_branch": "release/1.0"}) is False
    assert cli._fleet_latest_branch_is_actionable({"state": "in-sync", "branch": "main", "latest_branch": "-"}) is False


def test_fleet_apply_candidates_for_latest_filters_to_actionable_rows():
    rows = [
        {"repo": "alpha", "state": "in-sync", "branch": "main", "latest_branch": "release/1.0", "action": "-"},
        {"repo": "beta", "state": "in-sync", "branch": "release/1.0", "latest_branch": "release/1.0", "action": "-"},
        {"repo": "gamma", "state": "behind-remote", "branch": "release/2.0", "latest_branch": "release/2.0", "action": "pull"},
        {"repo": "delta", "state": "missing-local", "branch": "-", "latest_branch": "release/3.0", "action": "clone"},
    ]

    selected = cli._fleet_apply_candidates_for_mode(rows, "latest")

    assert [row["repo"] for row in selected] == ["alpha", "gamma", "delta"]



def test_fleet_plan_records_prefers_missing_local_repo_basename(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "find_repos", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(cli, "_fleet_server_context", lambda _args: ("github", "", "", "", {}, {}))

    args = argparse.Namespace(
        root=str(tmp_path),
        max_depth=1,
        include_hidden=False,
        fetch=False,
        with_prs=False,
        pr_stale_days=30,
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

    assert rows == [
        {
            "repo": "my-namespace/my-repo",
            "state": "missing-local",
            "branch": "-",
            "up": "-",
            "clean": "-",
            "action": "clone",
            "latest_branch": "-",
            "prs": "-",
            "path": str(tmp_path / "my-repo"),
        }
    ]

def test_fleet_plan_records_falls_back_to_namespaced_path_when_repo_basename_collides(monkeypatch, tmp_path):
    repo_path = tmp_path / "my-repo"
    repo_path.mkdir()
    monkeypatch.setattr(cli, "find_repos", lambda *_args, **_kwargs: [str(repo_path)])
    monkeypatch.setattr(cli, "_fleet_server_context", lambda _args: ("github", "", "", "", {}, {}))
    monkeypatch.setattr(
        cli,
        "_collect_repo_records_with_progress",
        lambda *_args, **_kwargs: [
            {
                "name": "my-repo",
                "path": str(repo_path),
                "origin": "git@example.com:someone-else/my-repo.git",
                "branch": "main",
                "upstream": "origin/main",
                "up": "≡",
                "main": "≡",
                "main_ref": "origin/main",
            }
        ],
    )
    monkeypatch.setattr(cli, "add_divergence_fields", lambda record: record)
    monkeypatch.setattr(cli.git, "get_working_tree_state", lambda _path: {})
    monkeypatch.setattr(cli.git, "is_operation_free", lambda _path: True)

    args = argparse.Namespace(
        root=str(tmp_path),
        max_depth=1,
        include_hidden=False,
        fetch=False,
        with_prs=False,
        pr_stale_days=30,
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
    missing_row = next(row for row in rows if row["repo"] == "my-namespace/my-repo")

    assert missing_row["path"] == str(tmp_path / "my-namespace%2Fmy-repo")


def test_fleet_plan_records_assigns_missing_local_destinations_deterministically(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "find_repos", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(cli, "_fleet_server_context", lambda _args: ("github", "", "", "", {}, {}))

    args = argparse.Namespace(
        root=str(tmp_path),
        max_depth=1,
        include_hidden=False,
        fetch=False,
        with_prs=False,
        pr_stale_days=30,
    )
    payload = {
        "repos": [
            {
                "name": "beta/shared-repo",
                "ssh_url": "git@example.com:beta/shared-repo.git",
            },
            {
                "name": "alpha/shared-repo",
                "ssh_url": "git@example.com:alpha/shared-repo.git",
            },
        ]
    }

    rows, _meta = cli._fleet_plan_records(args, payload=payload)

    path_by_repo = {row["repo"]: row["path"] for row in rows}
    assert path_by_repo["alpha/shared-repo"] == str(tmp_path / "shared-repo")
    assert path_by_repo["beta/shared-repo"] == str(tmp_path / "beta%2Fshared-repo")


def test_fleet_missing_local_destination_prefers_repo_basename():
    assert cli._fleet_missing_local_destination('/tmp/root', 'org-a/service') == '/tmp/root/service'
    assert cli._fleet_missing_local_destination('/tmp/root', 'org-b/service') == '/tmp/root/service'


def test_fleet_missing_local_destination_flat_layout():
    # Flat layout uses only the basename
    assert cli._fleet_missing_local_destination('/tmp/root', 'org-a/service', flat=True) == '/tmp/root/service'
    assert cli._fleet_missing_local_destination('/tmp/root', 'org/sub/repo', flat=True) == '/tmp/root/repo'


def test_fleet_missing_local_destination_flat_layout_quotes_special_characters():
    assert cli._fleet_missing_local_destination('/tmp/root', 'org/repo with space', flat=True) == '/tmp/root/repo%20with%20space'


def test_fleet_missing_local_destination_normalizes_trailing_separators():
    assert cli._fleet_missing_local_destination('/tmp/root', 'org/repo/') == '/tmp/root/repo'
    assert cli._fleet_missing_local_destination('/tmp/root', r'org\repo') == '/tmp/root/repo'


def test_fleet_missing_local_destination_falls_back_to_encoded_namespace_on_collision(tmp_path):
    reserved = {str(tmp_path / 'repo')}
    assert cli._fleet_missing_local_destination(str(tmp_path), 'org/repo', reserved) == str(tmp_path / 'org%2Frepo')


def test_fleet_missing_local_destination_uses_suffix_when_primary_candidates_are_unavailable(tmp_path):
    (tmp_path / "repo").mkdir()
    (tmp_path / "org%2Frepo").mkdir()

    assert cli._fleet_missing_local_destination(str(tmp_path), 'org/repo') == str(tmp_path / 'repo-2')


def test_fleet_missing_local_destination_escapes_safe_characters():
    assert cli._fleet_missing_local_destination('/tmp/root', 'org/repo with space') == '/tmp/root/repo%20with%20space'
    assert cli._fleet_missing_local_destination('/tmp/root', 'org__repo') == '/tmp/root/org__repo'


def test_cmd_fleet_apply_rejects_multiple_checkout_modes(capsys):
    args = _make_apply_args(checkout_branch="main", checkout_latest_branch=True)
    rc = cli.cmd_fleet_apply(args)
    err = capsys.readouterr().err
    assert rc == 1
    assert "Use only one checkout mode" in err


def test_cmd_fleet_apply_uses_latest_branch_hint_in_dry_run(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_fleet_load_remote", lambda _args: {"repos": []})
    monkeypatch.setattr(cli, "_fleet_server_context", lambda _args: ("github", "", "", "", {}, {}))
    monkeypatch.setattr(
        cli,
        "_fleet_plan_records",
        lambda _args, payload=None: (
            [{"repo": "demo", "state": "in-sync", "path": "/tmp/demo", "clean": "yes", "branch": "main", "latest_branch": "feature/latest", "action": "-"}],
            {},
        ),
    )
    monkeypatch.setattr(cli, "_is_valid_git_branch_name", lambda _branch: True)
    monkeypatch.setattr(
        cli.git,
        "get_working_tree_state",
        lambda _path: (_ for _ in ()).throw(AssertionError("dry-run should not inspect worktree state")),
    )
    args = _make_apply_args(checkout_latest_branch=True)

    rc = cli.cmd_fleet_apply(args)

    out = capsys.readouterr().out
    assert rc == 0
    assert "checkout-latest:feature/latest:dry-run" in out


def test_cmd_fleet_apply_skips_when_latest_branch_missing(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_fleet_load_remote", lambda _args: {"repos": []})
    monkeypatch.setattr(cli, "_fleet_server_context", lambda _args: ("github", "", "", "", {}, {}))
    monkeypatch.setattr(
        cli,
        "_fleet_plan_records",
        lambda _args, payload=None: (
            [{"repo": "demo", "state": "in-sync", "path": "/tmp/demo", "clean": "yes", "branch": "main", "latest_branch": "-", "action": "-"}],
            {},
        ),
    )
    args = _make_apply_args(checkout_latest_branch=True, repos="demo")

    rc = cli.cmd_fleet_apply(args)

    out = capsys.readouterr().out
    assert rc == 0
    assert "checkout-latest:skip-no-latest" in out


def test_cmd_fleet_apply_allows_latest_checkout_with_untracked_only(monkeypatch, tmp_path, capsys):
    repo_path = tmp_path / "demo"
    repo_path.mkdir()
    monkeypatch.setattr(cli, "render_table", lambda rows, _cols: rows[0]["result"])
    monkeypatch.setattr(cli, "_fleet_load_remote", lambda _args: {"repos": []})
    monkeypatch.setattr(cli, "_fleet_server_context", lambda _args: ("github", "", "", "", {}, {}))
    monkeypatch.setattr(
        cli,
        "_fleet_plan_records",
        lambda _args, payload=None: (
            [
                {
                    "repo": "demo",
                    "state": "in-sync",
                    "path": str(repo_path),
                    "clean": "yes",
                    "branch": "main",
                    "latest_branch": "feature/latest",
                    "action": "-",
                }
            ],
            {},
        ),
    )
    monkeypatch.setattr(cli, "_is_valid_git_branch_name", lambda _branch: True)
    monkeypatch.setattr(
        cli.git,
        "get_working_tree_state",
        lambda _path: {
            "status_ok": True,
            "is_clean": False,
            "has_untracked": True,
            "has_tracked_changes": False,
            "allows_checkout_latest": True,
            "error": "",
        },
    )
    monkeypatch.setattr(cli, "_run_git_op", lambda _path, _args, quiet=True: 0)
    monkeypatch.setattr(
        cli.git,
        "run_git",
        lambda _path, args: "origin/feature/latest" if args[:3] == ["rev-parse", "--verify", "origin/feature/latest"] else "",
    )
    args = _make_apply_args(checkout_latest_branch=True, dry_run=False)

    rc = cli.cmd_fleet_apply(args)

    out = capsys.readouterr().out
    assert rc == 0
    assert "checkout-latest:feature/latest:ok" in out


def test_cmd_fleet_apply_skips_latest_checkout_with_tracked_changes(monkeypatch, tmp_path, capsys):
    repo_path = tmp_path / "demo"
    repo_path.mkdir()
    monkeypatch.setattr(cli, "render_table", lambda rows, _cols: rows[0]["result"])
    monkeypatch.setattr(cli, "_fleet_load_remote", lambda _args: {"repos": []})
    monkeypatch.setattr(cli, "_fleet_server_context", lambda _args: ("github", "", "", "", {}, {}))
    monkeypatch.setattr(
        cli,
        "_fleet_plan_records",
        lambda _args, payload=None: (
            [
                {
                    "repo": "demo",
                    "state": "in-sync",
                    "path": str(repo_path),
                    "clean": "yes",
                    "branch": "main",
                    "latest_branch": "feature/latest",
                    "action": "-",
                }
            ],
            {},
        ),
    )
    monkeypatch.setattr(cli, "_is_valid_git_branch_name", lambda _branch: True)
    monkeypatch.setattr(
        cli.git,
        "get_working_tree_state",
        lambda _path: {
            "status_ok": True,
            "is_clean": False,
            "has_untracked": True,
            "has_tracked_changes": True,
            "allows_checkout_latest": False,
            "error": "",
        },
    )
    args = _make_apply_args(checkout_latest_branch=True, dry_run=False)

    rc = cli.cmd_fleet_apply(args)

    out = capsys.readouterr().out
    assert rc == 0
    assert "checkout-latest:feature/latest:skip-dirty-tracked" in out


def test_cmd_fleet_apply_skips_latest_checkout_when_git_status_fails(monkeypatch, tmp_path, capsys):
    repo_path = tmp_path / "demo"
    repo_path.mkdir()
    monkeypatch.setattr(cli, "render_table", lambda rows, _cols: rows[0]["result"])
    monkeypatch.setattr(cli, "_fleet_load_remote", lambda _args: {"repos": []})
    monkeypatch.setattr(cli, "_fleet_server_context", lambda _args: ("github", "", "", "", {}, {}))
    monkeypatch.setattr(
        cli,
        "_fleet_plan_records",
        lambda _args, payload=None: (
            [
                {
                    "repo": "demo",
                    "state": "in-sync",
                    "path": str(repo_path),
                    "clean": "yes",
                    "branch": "main",
                    "latest_branch": "feature/latest",
                    "action": "-",
                }
            ],
            {},
        ),
    )
    monkeypatch.setattr(cli, "_is_valid_git_branch_name", lambda _branch: True)
    monkeypatch.setattr(
        cli.git,
        "get_working_tree_state",
        lambda _path: {
            "status_ok": False,
            "is_clean": False,
            "has_untracked": False,
            "has_tracked_changes": False,
            "allows_checkout_latest": None,
            "error": "git status failed",
        },
    )
    args = _make_apply_args(checkout_latest_branch=True, dry_run=False)

    rc = cli.cmd_fleet_apply(args)

    out = capsys.readouterr().out
    assert rc == 0
    assert "checkout-latest:feature/latest:skip-git-error" in out


def test_checkout_remote_branch_short_circuits_when_fetch_fails(monkeypatch):
    monkeypatch.setattr(cli, "_run_git_op", lambda _path, args, quiet=True: 1 if args == ["fetch", "--prune"] else 0)

    statuses, records = cli._checkout_remote_branch(
        path="/tmp/demo",
        branch="feature/latest",
        checkout_action="checkout-latest",
        original_head="abc123",
        original_branch="main",
    )

    assert statuses == ["checkout-latest:feature/latest:skip-git-error"]
    assert records == [
        {
            "action": "checkout-latest",
            "status": "skip-git-error",
            "branch": "feature/latest",
            "detail": "git fetch failed",
        }
    ]


def test_cmd_fleet_apply_checkout_latest_branch_skips_missing_local_without_clone(monkeypatch, tmp_path, capsys):
    alpha = tmp_path / "alpha"
    gamma = tmp_path / "gamma"
    alpha.mkdir()
    gamma.mkdir()
    seen = []

    monkeypatch.setattr(cli, "render_table", lambda rows, _cols: "\n".join(row["result"] for row in rows))
    monkeypatch.setattr(cli, "_fleet_load_remote", lambda _args: {"repos": []})
    monkeypatch.setattr(cli, "_fleet_server_context", lambda _args: ("github", "", "", "", {}, {}))
    monkeypatch.setattr(
        cli,
        "_fleet_plan_records",
        lambda _args, payload=None: (
            [
                {"repo": "alpha", "state": "in-sync", "path": str(alpha), "clean": "yes", "branch": "main", "latest_branch": "release/1.0", "action": "-"},
                {"repo": "beta", "state": "missing-local", "path": str(tmp_path / "beta"), "clean": "-", "branch": "-", "latest_branch": "release/2.0", "action": "clone"},
                {"repo": "gamma", "state": "behind-remote", "path": str(gamma), "clean": "yes", "branch": "release/3.0", "latest_branch": "release/3.0", "action": "pull"},
            ],
            {},
        ),
    )
    monkeypatch.setattr(cli, "_is_valid_git_branch_name", lambda _branch: True)
    monkeypatch.setattr(
        cli.git,
        "get_working_tree_state",
        lambda _path: {
            "status_ok": True,
            "is_clean": True,
            "has_untracked": False,
            "has_tracked_changes": False,
            "allows_checkout_latest": True,
            "error": "",
        },
    )
    monkeypatch.setattr(
        cli,
        "_checkout_remote_branch",
        lambda path, branch, checkout_action, original_head, original_branch, fetch_first=False: (
            [f"{checkout_action}:{branch}:ok"],
            seen.append((path, branch, checkout_action)) or [{"action": checkout_action, "status": "ok", "branch": branch}],
        ),
    )
    args = _make_apply_args(checkout_latest_branch=True, dry_run=False)

    rc = cli.cmd_fleet_apply(args)

    out = capsys.readouterr().out
    assert rc == 0
    assert seen == [
        (str(alpha), "release/1.0", "checkout-latest"),
        (str(gamma), "release/3.0", "checkout-latest"),
    ]
    assert "release/2.0" not in out


def test_cmd_fleet_apply_checkout_latest_branch_targets_only_actionable_rows_when_no_filter(monkeypatch, tmp_path, capsys):
    alpha = tmp_path / "alpha"
    beta = tmp_path / "beta"
    gamma = tmp_path / "gamma"
    alpha.mkdir()
    beta.mkdir()
    gamma.mkdir()
    seen = []

    monkeypatch.setattr(cli, "render_table", lambda rows, _cols: "\n".join(row["result"] for row in rows))
    monkeypatch.setattr(cli, "_fleet_load_remote", lambda _args: {"repos": []})
    monkeypatch.setattr(cli, "_fleet_server_context", lambda _args: ("github", "", "", "", {}, {}))
    monkeypatch.setattr(
        cli,
        "_fleet_plan_records",
        lambda _args, payload=None: (
            [
                {"repo": "alpha", "state": "in-sync", "path": str(alpha), "clean": "yes", "branch": "main", "latest_branch": "release/1.0", "action": "-"},
                {"repo": "beta", "state": "in-sync", "path": str(beta), "clean": "yes", "branch": "release/1.0", "latest_branch": "release/1.0", "action": "-"},
                {"repo": "gamma", "state": "behind-remote", "path": str(gamma), "clean": "yes", "branch": "release/2.0", "latest_branch": "release/2.0", "action": "pull"},
            ],
            {},
        ),
    )
    monkeypatch.setattr(cli, "_is_valid_git_branch_name", lambda _branch: True)
    monkeypatch.setattr(
        cli.git,
        "get_working_tree_state",
        lambda _path: {
            "status_ok": True,
            "is_clean": True,
            "has_untracked": False,
            "has_tracked_changes": False,
            "allows_checkout_latest": True,
            "error": "",
        },
    )
    monkeypatch.setattr(
        cli,
        "_checkout_remote_branch",
        lambda path, branch, checkout_action, original_head, original_branch, fetch_first=False: (
            [f"{checkout_action}:{branch}:ok"],
            seen.append((path, branch, checkout_action)) or [{"action": checkout_action, "status": "ok", "branch": branch}],
        ),
    )
    args = _make_apply_args(checkout_latest_branch=True, dry_run=False)

    rc = cli.cmd_fleet_apply(args)

    out = capsys.readouterr().out
    assert rc == 0
    assert seen == [
        (str(alpha), "release/1.0", "checkout-latest"),
        (str(gamma), "release/2.0", "checkout-latest"),
    ]
    assert "checkout-latest:release/1.0:ok" in out
    assert "checkout-latest:release/2.0:ok" in out


def test_checkout_remote_branch_verifies_local_branch_ref(monkeypatch):
    seen_args = []
    seen_ops = []

    def _fake_run_git(_path, args):
        seen_args.append(args)
        if args == ["rev-parse", "--verify", "origin/feature/latest"]:
            return "origin/feature/latest"
        if args == ["rev-parse", "--verify", "refs/heads/feature/latest"]:
            return ""
        return ""

    monkeypatch.setattr(
        cli,
        "_run_git_op",
        lambda _path, args, quiet=True: (seen_ops.append(args), 0)[1],
    )
    monkeypatch.setattr(cli.git, "run_git", _fake_run_git)

    statuses, records = cli._checkout_remote_branch(
        path="/tmp/demo",
        branch="feature/latest",
        checkout_action="checkout-latest",
        original_head="abc123",
        original_branch="main",
    )

    assert statuses == ["checkout-latest:feature/latest:ok"]
    assert records == [
        {
            "action": "checkout-latest",
            "status": "ok",
            "branch": "feature/latest",
        }
    ]
    assert ["rev-parse", "--verify", "refs/heads/feature/latest"] in seen_args
    assert ["pull", "--ff-only", "origin", "feature/latest"] in seen_ops


def test_fleet_checkout_transition_display_uses_latest_branch_only_when_requested():
    row = {"state": "in-sync", "branch": "main", "latest_branch": "release/latest"}

    assert cli._fleet_checkout_transition_display(row, checkout_branch="", checkout_pr="", checkout_latest_branch=True) == "main -> release/latest"
    assert cli._fleet_checkout_transition_display(row, checkout_branch="release/x", checkout_pr="", checkout_latest_branch=False) == "main -> release/x"
    assert cli._fleet_checkout_transition_display(row, checkout_branch="", checkout_pr="77", checkout_latest_branch=False) == "main -> pr-77"
    assert cli._fleet_checkout_transition_display(row, checkout_branch="", checkout_pr="", checkout_latest_branch=False) == "-"


def test_fleet_checkout_transition_display_hides_missing_latest_branch():
    row = {"state": "in-sync", "branch": "main", "latest_branch": "-"}

    assert cli._fleet_checkout_transition_display(row, checkout_branch="", checkout_pr="", checkout_latest_branch=True) == "-"


def test_cmd_fleet_apply_reports_no_actionable_latest_branch_updates(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_fleet_load_remote", lambda _args: {"repos": []})
    monkeypatch.setattr(cli, "_fleet_server_context", lambda _args: ("github", "", "", "", {}, {}))
    monkeypatch.setattr(
        cli,
        "_fleet_plan_records",
        lambda _args, payload=None: (
            [{"repo": "demo", "state": "in-sync", "path": "/tmp/demo", "clean": "yes", "branch": "release/latest", "latest_branch": "release/latest", "action": "-"}],
            {},
        ),
    )

    rc = cli.cmd_fleet_apply(_make_apply_args(checkout_latest_branch=True))

    out = capsys.readouterr().out
    assert rc == 0
    assert "No repositories have actionable latest-branch updates." in out


def test_cmd_fleet_apply_snapshot_reuse_skips_remote_reload(monkeypatch, tmp_path, capsys):
    snapshot_path = tmp_path / "fleet-snapshot.json"
    repo_path = tmp_path / "demo"
    snapshot_path.write_text(
        f"""{{
  "root": "{tmp_path}",
  "repos": [
    {{
      "repo": "demo",
      "path": "{repo_path}",
      "state": "in-sync",
      "current_branch": "main",
      "current_vs_upstream": "≡",
      "git_operation_in_progress": "no",
      "latest_remote_branch": "main",
      "open_pr_numbers": "-",
      "origin_url": "git@example.com:demo.git",
      "primary_action": "-"
    }}
  ]
}}
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        cli,
        "_fleet_load_remote",
        lambda _args: (_ for _ in ()).throw(AssertionError("snapshot apply should not reload remote state")),
    )
    monkeypatch.setattr(cli, "_fleet_server_context", lambda _args: ("github", "", "", "", {}, {}))
    monkeypatch.setattr(cli, "render_table", lambda rows, _cols: rows[0]["result"])

    rc = cli.cmd_fleet_apply(_make_apply_args(snapshot=str(snapshot_path), root=str(tmp_path)))

    out = capsys.readouterr().out
    assert rc == 0
    assert "skip" in out


def test_cmd_fleet_apply_snapshot_clone_uses_snapshot_origin(monkeypatch, tmp_path, capsys):
    snapshot_path = tmp_path / "fleet-snapshot.json"
    repo_path = tmp_path / "demo"
    snapshot_path.write_text(
        f"""{{
  "root": "{tmp_path}",
  "repos": [
    {{
      "repo": "demo",
      "path": "{repo_path}",
      "state": "missing-local",
      "current_branch": "-",
      "current_vs_upstream": "-",
      "git_operation_in_progress": "no",
      "latest_remote_branch": "main",
      "open_pr_numbers": "-",
      "origin_url": "git@example.com:demo.git",
      "primary_action": "clone"
    }}
  ]
}}
""",
        encoding="utf-8",
    )
    seen = []
    monkeypatch.setattr(
        cli,
        "_fleet_load_remote",
        lambda _args: (_ for _ in ()).throw(AssertionError("snapshot clone should not reload remote state")),
    )
    monkeypatch.setattr(cli, "_fleet_server_context", lambda _args: ("github", "", "", "", {}, {}))
    monkeypatch.setattr(cli, "render_table", lambda rows, _cols: rows[0]["result"])
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda args, check=False, **kwargs: seen.append(args) or type("Proc", (), {"returncode": 0})(),
    )

    rc = cli.cmd_fleet_apply(
        _make_apply_args(snapshot=str(snapshot_path), clone_missing=True, dry_run=False, root=str(tmp_path))
    )

    out = capsys.readouterr().out
    assert rc == 0
    assert seen == [["git", "clone", "git@example.com:demo.git", str(repo_path)]]
    assert "clone:ok" in out


def test_cmd_fleet_apply_uses_explicit_origin_pull_when_no_upstream(monkeypatch, tmp_path, capsys):
    repo_path = tmp_path / "demo"
    repo_path.mkdir()
    seen_cmds = []

    monkeypatch.setattr(cli, "render_table", lambda rows, _cols: rows[0]["result"])
    monkeypatch.setattr(cli, "_fleet_load_remote", lambda _args: {"repos": []})
    monkeypatch.setattr(cli, "_fleet_server_context", lambda _args: ("github", "", "", "", {}, {}))
    monkeypatch.setattr(
        cli,
        "_fleet_plan_records",
        lambda _args, payload=None: (
            [
                {
                    "repo": "demo",
                    "state": "behind-remote",
                    "path": str(repo_path),
                    "clean": "yes",
                    "branch": "main",
                    "latest_branch": "main",
                    "action": "pull",
                }
            ],
            {},
        ),
    )
    monkeypatch.setattr(cli.git, "get_upstream", lambda _path: None)

    def fake_run(args, check=False, **kwargs):
        seen_cmds.append(list(args))
        return type("Proc", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    rc = cli.cmd_fleet_apply(_make_apply_args(pull_behind=True, dry_run=False))

    out = capsys.readouterr().out
    assert rc == 0
    assert "pull:ok" in out
    assert ["git", "-C", str(repo_path), "pull", "--ff-only", "--", "origin", "main"] in seen_cmds


def test_cmd_fleet_apply_skips_explicit_origin_pull_when_branch_name_invalid(monkeypatch, tmp_path, capsys):
    repo_path = tmp_path / "demo"
    repo_path.mkdir()
    seen_cmds = []

    monkeypatch.setattr(cli, "render_table", lambda rows, _cols: rows[0]["result"])
    monkeypatch.setattr(cli, "_fleet_load_remote", lambda _args: {"repos": []})
    monkeypatch.setattr(cli, "_fleet_server_context", lambda _args: ("github", "", "", "", {}, {}))
    monkeypatch.setattr(
        cli,
        "_fleet_plan_records",
        lambda _args, payload=None: (
            [
                {
                    "repo": "demo",
                    "state": "behind-remote",
                    "path": str(repo_path),
                    "clean": "yes",
                    "branch": "--upload-pack=sh",
                    "latest_branch": "main",
                    "action": "pull",
                }
            ],
            {},
        ),
    )
    monkeypatch.setattr(cli.git, "get_upstream", lambda _path: None)
    monkeypatch.setattr(cli, "_is_valid_git_branch_name", lambda _branch: False)

    def fake_run(args, check=False, **kwargs):
        seen_cmds.append(list(args))
        return type("Proc", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    rc = cli.cmd_fleet_apply(_make_apply_args(pull_behind=True, dry_run=False))

    out = capsys.readouterr().out
    assert rc == 0
    assert "pull:skip-invalid-branch" in out
    assert ["git", "-C", str(repo_path), "pull", "--ff-only"] not in seen_cmds
    assert ["git", "-C", str(repo_path), "pull", "--ff-only", "--", "origin", "--upload-pack=sh"] not in seen_cmds


def test_cmd_fleet_apply_uses_explicit_origin_push_when_no_upstream(monkeypatch, tmp_path, capsys):
    repo_path = tmp_path / "demo"
    repo_path.mkdir()
    seen_cmds = []

    monkeypatch.setattr(cli, "render_table", lambda rows, _cols: rows[0]["result"])
    monkeypatch.setattr(cli, "_fleet_load_remote", lambda _args: {"repos": []})
    monkeypatch.setattr(cli, "_fleet_server_context", lambda _args: ("github", "", "", "", {}, {}))
    monkeypatch.setattr(
        cli,
        "_fleet_plan_records",
        lambda _args, payload=None: (
            [
                {
                    "repo": "demo",
                    "state": "ahead-remote",
                    "path": str(repo_path),
                    "clean": "yes",
                    "branch": "main",
                    "latest_branch": "main",
                    "action": "push",
                }
            ],
            {},
        ),
    )
    monkeypatch.setattr(cli.git, "get_upstream", lambda _path: None)

    def fake_run(args, check=False, **kwargs):
        seen_cmds.append(list(args))
        return type("Proc", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    rc = cli.cmd_fleet_apply(_make_apply_args(push_ahead=True, dry_run=False))

    out = capsys.readouterr().out
    assert rc == 0
    assert "push:ok" in out
    assert ["git", "-C", str(repo_path), "push", "--", "origin", "main"] in seen_cmds


def test_cmd_fleet_apply_skips_explicit_origin_push_when_branch_name_invalid(monkeypatch, tmp_path, capsys):
    repo_path = tmp_path / "demo"
    repo_path.mkdir()
    seen_cmds = []

    monkeypatch.setattr(cli, "render_table", lambda rows, _cols: rows[0]["result"])
    monkeypatch.setattr(cli, "_fleet_load_remote", lambda _args: {"repos": []})
    monkeypatch.setattr(cli, "_fleet_server_context", lambda _args: ("github", "", "", "", {}, {}))
    monkeypatch.setattr(
        cli,
        "_fleet_plan_records",
        lambda _args, payload=None: (
            [
                {
                    "repo": "demo",
                    "state": "ahead-remote",
                    "path": str(repo_path),
                    "clean": "yes",
                    "branch": "--force",
                    "latest_branch": "main",
                    "action": "push",
                }
            ],
            {},
        ),
    )
    monkeypatch.setattr(cli.git, "get_upstream", lambda _path: None)
    monkeypatch.setattr(cli, "_is_valid_git_branch_name", lambda _branch: False)

    def fake_run(args, check=False, **kwargs):
        seen_cmds.append(list(args))
        return type("Proc", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    rc = cli.cmd_fleet_apply(_make_apply_args(push_ahead=True, dry_run=False))

    out = capsys.readouterr().out
    assert rc == 0
    assert "push:skip-invalid-branch" in out
    assert ["git", "-C", str(repo_path), "push"] not in seen_cmds
    assert ["git", "-C", str(repo_path), "push", "origin", "--force"] not in seen_cmds
