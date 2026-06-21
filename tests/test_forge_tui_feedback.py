"""Regression tests for forge TUI feedback (PR #62).

These cover the silent-failure paths that previously dead-ended to the main
menu with no dialog:
- ``_run_lantern_subprocess`` surfacing nonzero exits even when stderr is empty.
- forge ``list``/``snippets`` display modes showing a "no results" dialog when
  the command succeeds but returns empty output.
"""

import os
import sys
from types import SimpleNamespace

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from lantern import cli  # noqa: E402


def _capture_msgboxes(monkeypatch):
    """Record all ``_dialog_msgbox`` invocations as (title, text) tuples."""
    calls = []
    monkeypatch.setattr(
        cli, "_dialog_msgbox",
        lambda title, text, *a, **k: calls.append((title, text)),
    )
    return calls


def test_run_lantern_subprocess_reports_failure_with_empty_stderr_uses_stdout(monkeypatch):
    calls = _capture_msgboxes(monkeypatch)
    monkeypatch.setattr(
        cli.subprocess, "run",
        lambda *_a, **_k: SimpleNamespace(returncode=2, stderr="", stdout="boom on stdout"),
    )

    result = cli._run_lantern_subprocess(["lantern", "forge", "list"], 20, 84)

    assert result.returncode == 2
    assert len(calls) == 1
    title, text = calls[0]
    assert title == "Error"
    assert "boom on stdout" in text


def test_run_lantern_subprocess_reports_failure_with_no_output_uses_exit_status(monkeypatch):
    calls = _capture_msgboxes(monkeypatch)
    monkeypatch.setattr(
        cli.subprocess, "run",
        lambda *_a, **_k: SimpleNamespace(returncode=3, stderr="", stdout=""),
    )

    result = cli._run_lantern_subprocess(["lantern", "forge", "list"], 20, 84)

    assert result.returncode == 3
    assert len(calls) == 1
    title, text = calls[0]
    assert title == "Error"
    assert "status 3" in text


def test_run_lantern_subprocess_success_shows_no_dialog(monkeypatch):
    calls = _capture_msgboxes(monkeypatch)
    monkeypatch.setattr(
        cli.subprocess, "run",
        lambda *_a, **_k: SimpleNamespace(returncode=0, stderr="", stdout="ok"),
    )

    cli._run_lantern_subprocess(["lantern", "forge", "list"], 20, 84)

    assert calls == []


def _drive_forge_tui(monkeypatch, tmp_path, menu_choices):
    """Drive cmd_tui through a forge display flow and return captured msgboxes."""
    monkeypatch.setattr(cli, "_dialog_available", lambda: True)
    monkeypatch.setattr(cli, "_dialog_init", lambda: (20, 84))
    monkeypatch.setattr(cli.lantern_config, "load_config", lambda: {
        "workspace_root": str(tmp_path),
        "scan_json_path": str(tmp_path / "repos.json"),
    })
    monkeypatch.setattr(
        cli.lantern_config, "list_servers",
        lambda _config: [{"name": "github.com", "provider": "github"}],
    )
    # Top-of-loop `clear` and any other direct subprocess calls.
    monkeypatch.setattr(cli.subprocess, "run", lambda *_a, **_k: SimpleNamespace(returncode=0))
    # Command "succeeds" but returns no rows -> empty-result branch.
    monkeypatch.setattr(
        cli, "_run_lantern_subprocess",
        lambda *_a, **_k: SimpleNamespace(returncode=0, stdout="   ", stderr=""),
    )
    # If the textbox is reached it means the empty branch was missed.
    def _fail_textbox(*_a, **_k):
        raise AssertionError("_dialog_textbox_from_text should not be called for empty output")
    monkeypatch.setattr(cli, "_dialog_textbox_from_text", _fail_textbox)

    choices = iter(menu_choices)
    monkeypatch.setattr(cli, "_dialog_menu", lambda *_a, **_k: next(choices))

    calls = _capture_msgboxes(monkeypatch)
    rc = cli.cmd_tui(SimpleNamespace(tui_root=""))
    assert rc == 0
    return calls


def test_forge_list_empty_result_shows_no_repositories_message(monkeypatch, tmp_path):
    calls = _drive_forge_tui(
        monkeypatch, tmp_path,
        menu_choices=["forge", "list", "github.com", "exit"],
    )
    assert any(
        title == "Repositories" and "No repositories returned" in text
        for title, text in calls
    ), calls


def test_forge_snippets_empty_result_shows_no_snippets_message(monkeypatch, tmp_path):
    calls = _drive_forge_tui(
        monkeypatch, tmp_path,
        menu_choices=["forge", "snippets", "github.com", "exit"],
    )
    assert any(
        title == "Gists/Snippets" and "No gists/snippets returned" in text
        for title, text in calls
    ), calls
