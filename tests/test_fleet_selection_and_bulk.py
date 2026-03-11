import json

from lantern import cli


def test_resolve_selected_records_returns_all_when_filter_empty(tmp_path):
    records = [
        {"name": "alpha", "path": str(tmp_path / "alpha")},
        {"name": "beta", "path": str(tmp_path / "beta")},
    ]

    selected, error = cli._resolve_selected_records(records, "")

    assert error is None
    assert selected == records


def test_resolve_selected_records_accepts_unique_names_and_full_paths(tmp_path):
    records = [
        {"name": "alpha", "path": str(tmp_path / "alpha")},
        {"name": "beta", "path": str(tmp_path / "beta")},
    ]

    selected, error = cli._resolve_selected_records(records, f"alpha,{tmp_path / 'beta'}")

    assert error is None
    assert selected == [records[0], records[1]]


def test_resolve_selected_records_reports_missing_repo(tmp_path):
    records = [{"name": "alpha", "path": str(tmp_path / "alpha")}]

    selected, error = cli._resolve_selected_records(records, "missing")

    assert selected == []
    assert error == "Repository not found: missing"


def test_resolve_selected_records_reports_ambiguous_name(tmp_path):
    records = [
        {"name": "alpha", "path": str(tmp_path / "alpha-1")},
        {"name": "alpha", "path": str(tmp_path / "alpha-2")},
    ]

    selected, error = cli._resolve_selected_records(records, "alpha")

    assert selected == []
    assert error == "Repository name is ambiguous: alpha. Use full path."


def test_apply_bulk_action_update_respects_only_clean(tmp_path):
    records = [
        {"name": "alpha", "path": str(tmp_path / "alpha"), "upstream": "origin/main", "clean": "no"},
    ]

    results = cli._apply_bulk_action(records, action="update", dry_run=False, only_clean=True)

    assert results == [
        {"repo": "alpha", "action": "update", "result": "skip-dirty", "path": str(tmp_path / "alpha")}
    ]


def test_apply_bulk_action_update_skips_missing_upstream(tmp_path):
    records = [
        {"name": "alpha", "path": str(tmp_path / "alpha"), "upstream": "", "clean": "yes"},
    ]

    results = cli._apply_bulk_action(records, action="update", dry_run=False, only_clean=False)

    assert results == [
        {"repo": "alpha", "action": "update", "result": "skip-no-upstream", "path": str(tmp_path / "alpha")}
    ]


def test_apply_bulk_action_checkout_main_uses_remote_main_ref(monkeypatch, tmp_path):
    seen_ops = []
    monkeypatch.setattr(cli, "_remote_main_ref", lambda _path: "origin/main")
    monkeypatch.setattr(
        cli,
        "_run_git_op",
        lambda _path, args: (seen_ops.append(args), 0)[1],
    )

    results = cli._apply_bulk_action(
        [{"name": "alpha", "path": str(tmp_path / "alpha"), "upstream": "origin/main", "clean": "yes"}],
        action="checkout-main",
        dry_run=False,
        only_clean=False,
    )

    assert results == [
        {
            "repo": "alpha",
            "action": "checkout-main",
            "result": "ok:main <= origin/main",
            "path": str(tmp_path / "alpha"),
        }
    ]
    assert seen_ops == [
        ["fetch", "--prune"],
        ["checkout", "main"],
        ["pull", "--ff-only"],
    ]


def test_confirm_fleet_apply_selection_returns_checked_rows(monkeypatch, tmp_path):
    rows = [
        {
            "repo": "beta",
            "state": "behind-remote",
            "clean": "yes",
            "path": str(tmp_path / "beta"),
            "latest_branch": "release/b",
        },
        {
            "repo": "alpha",
            "state": "in-sync",
            "clean": "yes",
            "path": str(tmp_path / "alpha"),
            "latest_branch": "release/a",
        },
    ]
    captured = {}

    def _render(summary_rows, columns):
        captured["table"] = (summary_rows, columns)
        return "table"

    monkeypatch.setattr(cli, "render_table", _render)
    monkeypatch.setattr(cli, "_dialog_textbox_from_text", lambda *args: captured.setdefault("textbox", args))
    monkeypatch.setattr(cli, "_dialog_checklist", lambda *_args: ["1"])

    selected = cli._fleet_preflight_confirm(
        title="Fleet Apply",
        rows=rows,
        clone_missing=False,
        pull_behind=True,
        push_ahead=False,
        checkout_branch="release/x",
        checkout_pr="",
        checkout_latest_branch=False,
        dry_run=True,
        only_clean=False,
        height=20,
        width=80,
    )

    assert [row["repo"] for row in selected] == ["alpha"]
    summary_rows, columns = captured["table"]
    assert columns == ["repo", "state", "plan", "clean", "path"]
    assert [row["repo"] for row in summary_rows] == ["alpha", "beta"]
    assert summary_rows[0]["plan"] == "checkout:release/x"
    assert "Repos selected: 2" in captured["textbox"][1]
    assert "Checkout branch: release/x" in captured["textbox"][1]


def test_confirm_fleet_apply_selection_returns_empty_when_user_cancels(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "render_table", lambda *_args, **_kwargs: "table")
    monkeypatch.setattr(cli, "_dialog_textbox_from_text", lambda *args: None)
    monkeypatch.setattr(cli, "_dialog_checklist", lambda *_args: [])

    selected = cli._fleet_preflight_confirm(
        title="Fleet Apply",
        rows=[
            {
                "repo": "alpha",
                "state": "in-sync",
                "clean": "yes",
                "path": str(tmp_path / "alpha"),
                "latest_branch": "main",
            }
        ],
        clone_missing=False,
        pull_behind=False,
        push_ahead=False,
        checkout_branch="",
        checkout_pr="77",
        checkout_latest_branch=False,
        dry_run=False,
        only_clean=False,
        height=20,
        width=80,
    )

    assert selected == []


def test_fleet_short_summary_counts_checkout_pr_updates(tmp_path):
    payload = {
        "summary": {"repos_processed": 2},
        "results": [
            {
                "repo": "alpha",
                "actions": [
                    {"action": "checkout", "status": "ok", "branch": "pr-77"},
                ],
            },
            {
                "repo": "beta",
                "actions": [
                    {"action": "pull", "status": "dry-run"},
                ],
            },
        ],
    }
    path = tmp_path / "fleet-log.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    summary = cli._fleet_short_summary_from_log(str(path))

    assert "Total repos processed: 2" in summary
    assert "Repos updated: 2" in summary
    assert "Branch updates: 1" in summary
    assert "- alpha:pr-77" in summary
