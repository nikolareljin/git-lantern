import json

from lantern import cli


def test_fleet_apply_candidates_for_latest_mode_filters_to_actionable_rows():
    rows = [
        {"repo": "alpha", "action": "-", "state": "in-sync", "branch": "main", "latest_branch": "release/1.0"},
        {"repo": "beta", "action": "pull", "state": "behind-remote", "branch": "release/1.0", "latest_branch": "release/1.0"},
        {"repo": "gamma", "action": "-", "state": "in-sync", "branch": "release/1.0", "latest_branch": "release/1.0"},
        {"repo": "delta", "action": "clone", "state": "missing-local", "branch": "-", "latest_branch": "release/2.0"},
    ]
    selected = cli._fleet_apply_candidates_for_mode(rows, "latest")
    assert [r["repo"] for r in selected] == ["alpha", "beta", "delta"]

    selected_without_clone = cli._fleet_apply_candidates_for_mode(rows, "latest", include_missing_local=False)
    assert [r["repo"] for r in selected_without_clone] == ["alpha", "beta"]


def test_fleet_apply_candidates_for_sync_mode_filters_to_actionable_rows():
    rows = [
        {"repo": "alpha", "action": "-", "state": "in-sync"},
        {"repo": "beta", "action": "clone", "state": "missing"},
        {"repo": "gamma", "action": "push", "state": "ahead-remote"},
    ]
    selected = cli._fleet_apply_candidates_for_mode(rows, "sync")
    assert [r["repo"] for r in selected] == ["beta", "gamma"]


def test_fleet_short_summary_counts_checkout_latest_branch_updates(tmp_path):
    payload = {
        "summary": {"repos_processed": 1},
        "results": [
            {
                "repo": "demo",
                "actions": [
                    {"action": "checkout-latest", "status": "ok", "branch": "release/x"},
                ],
            }
        ],
    }
    path = tmp_path / "fleet-log.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    summary = cli._fleet_short_summary_from_log(str(path))
    assert "Repos updated: 1" in summary
    assert "Branch updates: 1" in summary
    assert "- demo:release/x" in summary
