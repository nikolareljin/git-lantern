from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lantern import cleanup, cleanup_cmd, cli, progress  # noqa: E402


def test_busy_shows_initial_frame_and_heartbeat_then_stops():
    frames = []
    updated = threading.Event()

    def render(text):
        frames.append(text)
        if "Fetching origin" in text and "page 2" in text:
            updated.set()

    with progress.Busy("Repository 1/2", render, interval=0.01) as task:
        assert "Repository 1/2" in frames[0]
        assert "Elapsed: 0s" in frames[0]
        progress.report("Fetching origin")
        progress.detail("page 2")
        assert updated.wait(2), frames
    assert not task.thread.is_alive()
    assert not progress.active()
    assert progress.report("outside task") is False


def test_busy_stops_before_error_dialog_and_does_not_hide_exception():
    task = progress.Busy("Loading", lambda text: None, interval=0.01)
    with pytest.raises(ValueError, match="failed"):
        with task:
            raise ValueError("failed")
    assert not task.thread.is_alive()
    assert not progress.active()


def test_failed_indicator_start_does_not_leak_task_context(monkeypatch):
    def fail_start(thread):
        raise RuntimeError("thread unavailable")

    monkeypatch.setattr(progress.threading.Thread, "start", fail_start)
    with pytest.raises(RuntimeError, match="thread unavailable"):
        with progress.Busy("Loading", lambda text: None):
            pytest.fail("work started after indicator failure")
    assert not progress.active()


def test_nested_progress_reuses_display():
    frames = []
    with progress.Busy("Outer", frames.append) as outer:
        with progress.Busy("Inner", lambda _: pytest.fail("nested display started")) as inner:
            assert inner is outer
            progress.detail("working")
        assert progress.active()
    assert not progress.active()


def setup_dialog(monkeypatch):
    frames = []
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli, "_dialog_available", lambda: True)
    monkeypatch.setattr(cli, "_dialog_init", lambda: (24, 100))
    monkeypatch.setattr(cli, "_dialog_infobox", lambda title, text, *args: frames.append(text))
    monkeypatch.setattr(cli, "_dialog_taskbox", lambda title, text, *args: frames.append(text) or False)
    return frames


def test_background_feedback_precedes_work_and_ends_before_prompt(monkeypatch):
    frames = setup_dialog(monkeypatch)

    def work():
        assert frames and "Scanning" in frames[0]
        assert progress.active()
        return "done"

    assert cli._dialog_work("Repos", "Scanning", work) == "done"
    assert not progress.active()


@pytest.mark.parametrize("capture,live", [(True, True), (False, False)])
def test_hidden_subprocess_has_progress_without_showing_arguments(monkeypatch, capture, live):
    frames = setup_dialog(monkeypatch)
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        assert progress.active()
        return SimpleNamespace(returncode=0, stdout="done", stderr="")

    monkeypatch.setattr(cli.subprocess, "run", run)
    cli._run_lantern_subprocess(["lantern", "forge", "list", "--user", "PRIVATE-ARG"], 24, 100,
                               capture=capture, show_live_output=live)
    assert frames
    assert not any("PRIVATE-ARG" in frame for frame in frames)
    assert len(calls) == 1
    assert not progress.active()


def test_live_subprocess_is_not_covered_by_progress(monkeypatch):
    monkeypatch.setattr(cli, "_dialog_work", lambda *args, **kwargs: pytest.fail("live terminal covered"))
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))
    cli._run_lantern_subprocess(["lantern", "forge", "clone"], 24, 100, capture=False)


def test_cleanup_two_repos_show_kept_branches_and_summary(monkeypatch, tmp_path):
    frames = setup_dialog(monkeypatch)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(cleanup_cmd.config, "load_config", lambda: {})
    monkeypatch.setattr(cli, "_tui_select_server", lambda *args: "github.com")
    menus = iter(["user", "30d"])
    monkeypatch.setattr(cli, "_dialog_menu", lambda *args: next(menus))
    monkeypatch.setattr(cli, "_dialog_checklist_result", lambda *args: [])
    monkeypatch.setattr(cleanup_cmd, "choose", lambda *args: ["0", "1"])

    def discover(args):
        assert progress.active()
        return [{"name": "team/a", "path": "a", "adapter": object()},
                {"name": "team/b", "path": "b", "adapter": object()}]

    def scan(path, *args):
        assert progress.active()
        progress.report("Reading branch metadata")
        return [cleanup.Candidate("old-" + path, "remote", "a", first_seen="2026-11-10",
                                  reason="branch age not established; waiting since first observation")]

    monkeypatch.setattr(cleanup_cmd, "repositories", discover)
    monkeypatch.setattr(cleanup, "scan", scan)
    monkeypatch.setattr(cleanup, "delete", lambda *args: pytest.fail("new branches deleted"))
    output = []

    def results(title, text, *args):
        assert not progress.active()
        assert title == "Cleanup results (read-only)"
        assert "No eligible branches: no branch checkbox list was opened" in text
        output.append(text)
        return True

    monkeypatch.setattr(cli, "_dialog_textbox_from_text", results)
    cleanup_cmd.tui({"root": str(tmp_path), "max_depth": 6, "include_hidden": False, "include_forks": False}, 24, 100)
    assert len(output) == 1
    assert "old-a" in output[0] and "old-b" in output[0]
    assert "2 repositories selected; 2 branches inspected" in output[0]
    assert "0 deleted" in output[0]
    assert "First-seen observations must be older than 30d" in output[0]
    assert any("Preparing repository selection" in frame for frame in frames)
    assert any("Repository 1/2" in frame for frame in frames)
    assert any("Repository 2/2" in frame for frame in frames)


def test_cleanup_options_cancel_does_not_start_discovery(monkeypatch, tmp_path):
    monkeypatch.setattr(cleanup_cmd.config, "load_config", lambda: {})
    monkeypatch.setattr(cli, "_tui_select_server", lambda *args: "github.com")
    menus = iter(["user", "30d"])
    monkeypatch.setattr(cli, "_dialog_menu", lambda *args: next(menus))
    monkeypatch.setattr(cli, "_dialog_checklist_result", lambda *args: None)
    monkeypatch.setattr(cleanup_cmd, "command", lambda *args: pytest.fail("cancel started cleanup"))
    cleanup_cmd.tui({}, 24, 100)


def test_empty_scope_has_an_explicit_result(monkeypatch, capsys):
    setup_dialog(monkeypatch)
    args = cli.build_parser().parse_args(["fleet", "cleanup", "--select"])
    monkeypatch.setattr(cleanup_cmd, "repositories", lambda *args: [])
    monkeypatch.setattr(cleanup_cmd, "choose", lambda *args: pytest.fail("empty selector opened"))
    assert cleanup_cmd.command(args) == 0
    assert "No repositories found" in capsys.readouterr().out


def test_checklist_cancel_differs_from_unchecked_options(monkeypatch):
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=""))
    assert cli._dialog_checklist_result("Options", "Choose", [], 24, 100) == []
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=""))
    assert cli._dialog_checklist_result("Options", "Choose", [], 24, 100) is None


@pytest.mark.parametrize("command", ["fleet cleanup", "fleet cleanup --repos team/a --confirm-first", "tui", "config setup"])
def test_command_menu_gives_interactive_child_its_terminal(monkeypatch, command):
    monkeypatch.setattr(cli, "_dialog_inputbox", lambda *args: command)
    monkeypatch.setattr(cli, "_dialog_yesno", lambda *args: True)
    monkeypatch.setattr(cli, "_dialog_msgbox", lambda *args: None)
    monkeypatch.setattr(cli, "_dialog_textbox_from_text", lambda *args: True)
    calls = []

    def run(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(returncode=0, stdout="Branch report")

    monkeypatch.setattr(cli, "_run_lantern_subprocess", run)
    cli._handle_tui_command_action(24, 100)
    assert calls[0]["background"] is False
    assert calls[0]["capture"] is (command != "tui")


def test_interactive_captured_child_keeps_report_without_parent_indicator(monkeypatch):
    monkeypatch.setattr(cli, "_dialog_work", lambda *args, **kwargs: pytest.fail("child dialog covered by indicator"))
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="Branch report", stderr=""))
    result = cli._run_lantern_subprocess(["lantern", "fleet", "cleanup", "--select"], 24, 100, background=False)
    assert result.stdout == "Branch report"


def test_cleanup_tui_confirm_first_selects_each_repo_before_deleting(monkeypatch, tmp_path):
    setup_dialog(monkeypatch)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(cleanup_cmd.config, "load_config", lambda: {})
    monkeypatch.setattr(cli, "_tui_select_server", lambda *args: "github.com")
    menus = iter(["user", "30d"])
    monkeypatch.setattr(cli, "_dialog_menu", lambda *args: next(menus))
    monkeypatch.setattr(cli, "_dialog_checklist_result", lambda *args: ["confirm"])
    monkeypatch.setattr(cleanup_cmd, "repositories", lambda args: [
        {"name": "team/a", "path": "a", "adapter": object()},
        {"name": "team/b", "path": "b", "adapter": object()}])
    monkeypatch.setattr(cleanup, "scan", lambda path, *args: [
        cleanup.Candidate("old-" + path, "remote", "a", merged=True),
        cleanup.Candidate("new-" + path, "remote", "b", reason="too new")])
    events = []

    def choose(title, text, rows, **kwargs):
        if title == "Branch cleanup":
            return ["0", "1"]
        assert len(rows) == 1
        assert "creator=" in kwargs["details"]
        events.append(title)
        return ["0"]

    monkeypatch.setattr(cleanup_cmd, "choose", choose)
    monkeypatch.setattr(cli, "_dialog_yesno", lambda *args, **kwargs: True)
    monkeypatch.setattr(cleanup, "delete", lambda path, adapter, row: events.append("deleted " + row.name))
    monkeypatch.setattr(cli, "_dialog_textbox_from_text", lambda *args: events.append("results"))
    cleanup_cmd.tui({"root": str(tmp_path), "max_depth": 6, "include_hidden": False, "include_forks": False}, 24, 100)
    assert events == ["Branch cleanup: team/a", "deleted old-a", "Branch cleanup: team/b", "deleted old-b", "results"]


@pytest.mark.parametrize("status,stop", [(0, True), (255, True), (124, False), (254, False)])
def test_task_stop_button_and_escape_differ_from_refresh_timeout(monkeypatch, status, stop):
    def dialog(args, **kwargs):
        assert args[args.index("--ok-label") + 1] == "Stop task"
        assert kwargs["env"]["DIALOG_TIMEOUT"] == "124"
        return SimpleNamespace(returncode=status)
    monkeypatch.setattr(cli.subprocess, "run", dialog)
    assert cli._dialog_taskbox("Task", "Checking", 24, 100) is stop


@pytest.mark.skipif(sys.platform == "win32", reason="task signals require a POSIX terminal")
def test_task_stop_unwinds_work_restores_signal_and_returns_to_same_session(monkeypatch):
    import signal

    session = {"root": "workspace", "setting": "keep", "include_hidden": False,
               "include_forks": False, "max_depth": 6, "scan_path": "scan.json"}
    setup_dialog(monkeypatch)
    previous = signal.getsignal(signal.SIGUSR1)
    monkeypatch.setattr(cli, "_dialog_taskbox", lambda *args: True)
    with pytest.raises(progress.TaskCancelled):
        cli._dialog_work("Task", "Working", lambda: threading.Event().wait(5))
    assert not progress.active()
    assert signal.getsignal(signal.SIGUSR1) == previous
    actions = iter(["branch_cleanup", "exit"])
    monkeypatch.setattr(cli, "_dialog_menu", lambda *args: next(actions))
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))
    def cancel(*args):
        assert args[0] is session
        raise progress.TaskCancelled
    monkeypatch.setattr(cleanup_cmd, "tui", cancel)
    messages = []
    monkeypatch.setattr(cli, "_dialog_msgbox", lambda *args: messages.append(args))
    assert cli._run_tui_session(session, 24, 100) == 0
    assert session["setting"] == "keep"
    assert messages[0][0] == "Task stopped"


@pytest.mark.parametrize("symlink", [False, True])
def test_stopping_scan_save_keeps_previous_json(monkeypatch, tmp_path, symlink):
    target = tmp_path / "scan.json"
    target.write_text('{"repos": []}', encoding="utf-8")
    path = target
    if symlink:
        path = tmp_path / "scan-link.json"
        path.symlink_to(target)
    def interrupted_dump(payload, handle, **kwargs):
        handle.write('{"repos": [')
        raise progress.TaskCancelled
    monkeypatch.setattr(cli.json, "dump", interrupted_dump)
    with pytest.raises(progress.TaskCancelled):
        cli._tui_save_scan(str(path), {"repos": ["new"]})
    assert target.read_text() == '{"repos": []}'
    assert not list(tmp_path.glob(".lantern.*"))
    assert path.is_symlink() is symlink


def test_scan_save_updates_symlink_target(tmp_path):
    import json

    target = tmp_path / "scan.json"
    target.write_text('{"repos": []}', encoding="utf-8")
    path = tmp_path / "scan-link.json"
    path.symlink_to(target)
    cli._tui_save_scan(str(path), {"repos": ["new"]})
    assert path.is_symlink()
    assert json.loads(target.read_text()) == {"repos": ["new"]}
