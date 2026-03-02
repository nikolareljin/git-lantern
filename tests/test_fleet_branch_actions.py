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
            [{"repo": "demo", "state": "in-sync", "path": "/tmp/demo", "clean": "yes", "latest_branch": "feature/latest", "action": "-"}],
            {},
        ),
    )
    monkeypatch.setattr(cli, "_is_valid_git_branch_name", lambda _branch: True)
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
            [{"repo": "demo", "state": "in-sync", "path": "/tmp/demo", "clean": "yes", "latest_branch": "-", "action": "-"}],
            {},
        ),
    )
    args = _make_apply_args(checkout_latest_branch=True)

    rc = cli.cmd_fleet_apply(args)

    out = capsys.readouterr().out
    assert rc == 0
    assert "checkout-latest:skip-no-latest" in out
