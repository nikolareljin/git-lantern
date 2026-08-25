from lantern import cli


def test_only_clean_rejects_tracked_changes_and_git_operations():
    assert cli._is_only_clean_eligible({"clean": "yes", "tracked_dirty": "no", "git_operation_in_progress": "no"}) is True
    assert cli._is_only_clean_eligible({"clean": "yes", "tracked_dirty": "yes"}) is False
    assert cli._is_only_clean_eligible(
        {
            "clean": "yes",
            "tracked_dirty": "no",
            "git_operation_in_progress": "yes",
        }
    ) is False
    assert cli._is_only_clean_eligible({"clean": "no", "tracked_dirty": "no"}) is False

def test_snapshot_plan_row_treats_missing_worktree_state_as_not_clean():
    row = cli._snapshot_record_to_plan_row({"repo": "legacy", "local_missing": False})

    assert row["tracked_dirty"] == "unknown"
    assert row["git_operation_in_progress"] == "unknown"
    assert row["clean"] == "no"
    assert cli._is_only_clean_eligible(row) is False


def test_snapshot_plan_row_requires_explicitly_clean_worktree_state():
    row = cli._snapshot_record_to_plan_row(
        {"repo": "clean", "tracked_dirty": "no", "git_operation_in_progress": "no"}
    )

    assert row["clean"] == "yes"
    assert cli._is_only_clean_eligible(row) is True
