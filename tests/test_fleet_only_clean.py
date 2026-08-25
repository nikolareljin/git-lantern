from lantern import cli


def test_only_clean_rejects_tracked_changes_and_git_operations():
    assert cli._is_only_clean_eligible({"clean": "yes", "tracked_dirty": "no"}) is True
    assert cli._is_only_clean_eligible({"clean": "yes", "tracked_dirty": "yes"}) is False
    assert cli._is_only_clean_eligible(
        {
            "clean": "yes",
            "tracked_dirty": "no",
            "git_operation_in_progress": "yes",
        }
    ) is False
    assert cli._is_only_clean_eligible({"clean": "no", "tracked_dirty": "no"}) is False
