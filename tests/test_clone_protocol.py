import argparse
import json

from lantern import cli


def _apply_args(tmp_path, snapshot_path, **overrides):
    base = dict(
        checkout_branch="",
        checkout_pr="",
        checkout_latest_branch=False,
        clone_missing=True,
        pull_behind=False,
        push_ahead=False,
        root=str(tmp_path),
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
        dry_run=False,
        only_clean=False,
        log_json="",
        with_prs=False,
        pr_stale_days=30,
        snapshot=str(snapshot_path),
        refresh=False,
        clone_protocol="auto",
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def _write_snapshot(tmp_path, names):
    snapshot_path = tmp_path / "fleet-snapshot.json"
    repos = [
        {
            "repo": f"owner/{name}",
            "path": str(tmp_path / "owner" / name),
            "state": "missing-local",
            "current_branch": "-",
            "current_vs_upstream": "-",
            "git_operation_in_progress": "no",
            "latest_remote_branch": "main",
            "open_pr_numbers": "-",
            "origin_url": f"git@github.com:owner/{name}.git",
            "primary_action": "clone",
        }
        for name in names
    ]
    snapshot_path.write_text(json.dumps({"root": str(tmp_path), "repos": repos}), encoding="utf-8")
    return snapshot_path


def _patch_common(monkeypatch):
    monkeypatch.setenv("COLUMNS", "400")
    monkeypatch.setattr(cli, "_fleet_server_context", lambda _args: ("github", "", "", "", {}, {}))
    monkeypatch.setattr(
        cli,
        "_fleet_load_remote",
        lambda _args: (_ for _ in ()).throw(AssertionError("snapshot clone should not reload remote state")),
    )


def test_fleet_apply_parser_defaults_clone_protocol_to_auto():
    parser = cli.build_parser()
    assert parser.parse_args(["fleet", "apply"]).clone_protocol == "auto"
    assert parser.parse_args(["fleet", "apply", "--clone-protocol", "https"]).clone_protocol == "https"
    assert parser.parse_args(["forge", "clone", "--clone-protocol", "ssh"]).clone_protocol == "ssh"


def test_https_url_from_ssh_handles_scp_and_ssh_scheme():
    assert cli._https_url_from_ssh("git@github.com:owner/repo.git") == "https://github.com/owner/repo.git"
    assert cli._https_url_from_ssh("ssh://git@gitlab.example.com:2222/group/repo.git") == (
        "https://gitlab.example.com/group/repo.git"
    )
    assert cli._https_url_from_ssh("https://github.com/owner/repo.git") == ""


def test_clone_failure_reason_classifies_common_git_errors():
    assert cli._clone_failure_reason("ssh", "git@github.com: Permission denied (publickey).") == "ssh-key-required"
    assert cli._clone_failure_reason("ssh", "Host key verification failed.") == "ssh-host-key-unknown"
    assert (
        cli._clone_failure_reason("https", "fatal: could not read Username for 'https://github.com': terminal prompts disabled")
        == "https-auth-required"
    )
    assert cli._clone_failure_reason("https", "fatal: repository 'x' not found") == "not-found-or-no-access"


def test_auto_falls_back_to_https_when_ssh_key_is_rejected(monkeypatch, tmp_path, capsys):
    _patch_common(monkeypatch)
    snapshot_path = _write_snapshot(tmp_path, ["alpha", "beta"])
    probes = []
    clones = []
    monkeypatch.setattr(
        cli.git,
        "check_remote_access",
        lambda url: probes.append(url) or (False, "git@github.com: Permission denied (publickey)."),
    )
    monkeypatch.setattr(cli.git, "clone_repo", lambda url, dest: clones.append(url) or (True, ""))
    log_path = tmp_path / "log.json"

    rc = cli.cmd_fleet_apply(_apply_args(tmp_path, snapshot_path, log_json=str(log_path)))

    captured = capsys.readouterr()
    assert rc == 0
    assert len(probes) == 1, "SSH access should be probed once per host"
    assert clones == ["https://github.com/owner/alpha.git", "https://github.com/owner/beta.git"]
    assert "clone:ok:https" in captured.out
    assert "SSH key required" in captured.err
    assert "Using HTTPS for github.com" in captured.err
    log = json.loads(log_path.read_text(encoding="utf-8"))
    assert log["clone_access"][0]["reason"] == "ssh-key-required"
    assert "Clone access problems" in cli._fleet_short_summary_from_log(str(log_path))


def test_ssh_mode_skips_clones_and_surfaces_missing_ssh_key(monkeypatch, tmp_path, capsys):
    _patch_common(monkeypatch)
    snapshot_path = _write_snapshot(tmp_path, ["alpha", "beta"])
    monkeypatch.setattr(
        cli.git, "check_remote_access", lambda url: (False, "git@github.com: Permission denied (publickey).")
    )
    monkeypatch.setattr(
        cli.git, "clone_repo", lambda url, dest: (_ for _ in ()).throw(AssertionError("clone must be skipped"))
    )
    log_path = tmp_path / "log.json"

    rc = cli.cmd_fleet_apply(_apply_args(tmp_path, snapshot_path, clone_protocol="ssh", log_json=str(log_path)))

    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out.count("clone:ssh-key-required") == 2
    assert captured.err.count("SSH key required") == 1
    assert "ssh-keygen -t ed25519" in captured.err
    assert "gh ssh-key add" in captured.err
    summary = cli._fleet_short_summary_from_log(str(log_path))
    assert "SSH key required" in summary
    assert "owner/alpha: ssh-key-required" in summary


def test_https_auth_failure_is_reported_with_hint(monkeypatch, tmp_path, capsys):
    _patch_common(monkeypatch)
    snapshot_path = _write_snapshot(tmp_path, ["alpha"])
    monkeypatch.setattr(
        cli.git, "check_remote_access", lambda url: (_ for _ in ()).throw(AssertionError("https must not probe ssh"))
    )
    monkeypatch.setattr(
        cli.git,
        "clone_repo",
        lambda url, dest: (False, "fatal: could not read Username for 'https://github.com': terminal prompts disabled"),
    )

    rc = cli.cmd_fleet_apply(_apply_args(tmp_path, snapshot_path, clone_protocol="https"))

    captured = capsys.readouterr()
    assert rc == 0
    assert "clone:https-auth-required" in captured.out
    assert "https-auth-required" in captured.out.split("Unsuccessful fleet operations:", 1)[1]
    assert "gh auth setup-git" in captured.err


def test_repo_specific_ssh_probe_failure_keeps_ssh(monkeypatch, tmp_path):
    access = cli._CloneAccess("auto")
    monkeypatch.setattr(cli.git, "check_remote_access", lambda url: (False, "ERROR: Repository not found."))

    plan = access.plan("git@github.com:owner/gone.git", "")

    assert plan == {"protocol": "ssh", "url": "git@github.com:owner/gone.git"}
    assert access.notices == []


def test_forge_clone_reports_failures_and_returns_nonzero(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("COLUMNS", "400")
    input_path = tmp_path / "repos.json"
    input_path.write_text(
        json.dumps({"repos": [{"name": "owner/alpha", "ssh_url": "git@github.com:owner/alpha.git"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        cli.git, "check_remote_access", lambda url: (False, "git@github.com: Permission denied (publickey).")
    )
    monkeypatch.setattr(
        cli.git,
        "clone_repo",
        lambda url, dest: (False, "fatal: could not read Username for 'https://github.com': terminal prompts disabled"),
    )
    args = argparse.Namespace(
        input=str(input_path),
        server="",
        root=str(tmp_path / "workspace"),
        tui=False,
        flat=False,
        dry_run=False,
        clone_protocol="auto",
    )

    rc = cli.cmd_github_clone(args)

    captured = capsys.readouterr()
    assert rc == 1
    assert "Cloning owner/alpha (https)" in captured.out
    assert "https-auth-required" in captured.out
    assert "SSH key required" in captured.err
    assert "HTTPS credentials for github.com are not configured" in captured.err


def test_noninteractive_env_adds_batch_mode_to_ssh(monkeypatch):
    monkeypatch.delenv("GIT_SSH", raising=False)
    monkeypatch.setenv("GIT_SSH_COMMAND", "ssh -i ~/.ssh/work")

    env = cli.git.noninteractive_env()

    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_SSH_COMMAND"].startswith("ssh -i ~/.ssh/work -o BatchMode=yes")


def test_noninteractive_env_leaves_custom_ssh_wrappers_alone(monkeypatch):
    monkeypatch.delenv("GIT_SSH", raising=False)
    monkeypatch.setenv("GIT_SSH_COMMAND", "/usr/local/bin/my-ssh-wrapper --flag")

    assert cli.git.noninteractive_env()["GIT_SSH_COMMAND"] == "/usr/local/bin/my-ssh-wrapper --flag"


def test_noninteractive_env_ignores_local_repo_ssh_command(monkeypatch):
    monkeypatch.delenv("GIT_SSH", raising=False)
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    seen = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        return type("Proc", (), {"returncode": 1, "stdout": ""})()

    monkeypatch.setattr(cli.git.subprocess, "run", fake_run)

    env = cli.git.noninteractive_env()

    assert env["GIT_SSH_COMMAND"].startswith("ssh -o BatchMode=yes")
    assert seen and all(cmd[2] in {"--global", "--system"} for cmd in seen)


def test_auto_falls_back_to_https_when_ssh_port_is_blocked(monkeypatch):
    access = cli._CloneAccess("auto")
    monkeypatch.setattr(
        cli.git,
        "check_remote_access",
        lambda url: (False, "kex_exchange_identification: Connection closed by remote host"),
    )

    plan = access.plan("git@github.com:owner/repo.git", "")

    assert plan == {"protocol": "https", "url": "https://github.com/owner/repo.git"}
    assert access.notices[0]["reason"] == "network-error"
    assert "--clone-protocol https" in access.notices[0]["message"]


def test_dry_run_does_not_probe_or_claim_a_protocol(monkeypatch, tmp_path, capsys):
    _patch_common(monkeypatch)
    snapshot_path = _write_snapshot(tmp_path, ["alpha"])
    monkeypatch.setattr(
        cli.git, "check_remote_access", lambda url: (_ for _ in ()).throw(AssertionError("dry run must not probe"))
    )
    log_path = tmp_path / "log.json"

    rc = cli.cmd_fleet_apply(_apply_args(tmp_path, snapshot_path, dry_run=True, log_json=str(log_path)))

    assert rc == 0
    clone_action = json.loads(log_path.read_text(encoding="utf-8"))["results"][0]["actions"][0]
    assert clone_action == {"action": "clone", "status": "dry-run"}


def test_summary_falls_back_to_status_when_failure_has_no_reason(tmp_path):
    log_path = tmp_path / "log.json"
    log_path.write_text(
        json.dumps(
            {
                "summary": {"repos_processed": 1},
                "results": [],
                "failures": [{"repo": "owner/alpha", "action": "clone", "status": "missing-url", "reason": "-"}],
            }
        ),
        encoding="utf-8",
    )

    assert "- owner/alpha: missing-url" in cli._fleet_short_summary_from_log(str(log_path))
