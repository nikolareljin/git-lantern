import argparse
import os
import sys
from types import SimpleNamespace

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from lantern import cli  # noqa: E402


def test_main_defaults_to_tui_when_no_subcommand(monkeypatch):
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)

    class _Parser:
        def parse_args(self):
            return SimpleNamespace(command=None, tui=False)

    monkeypatch.setattr(cli, "build_parser", lambda: _Parser())
    monkeypatch.setattr(cli, "argcomplete", None)

    called = {}

    def _fake_cmd_tui(args):
        called["args"] = args
        return 0

    monkeypatch.setattr(cli, "cmd_tui", _fake_cmd_tui)

    try:
        cli.main()
        assert False, "main() should raise SystemExit"
    except SystemExit as exc:
        assert exc.code == 0

    assert isinstance(called["args"], (argparse.Namespace, SimpleNamespace))
    assert called["args"].command is None


def test_parser_supports_explicit_tui_subcommand():
    parser = cli.build_parser()
    args = parser.parse_args(["tui"])
    assert args.command == "tui"
    assert args.func == cli.cmd_tui


def test_parser_supports_tui_subcommand_tui_root_flag():
    parser = cli.build_parser()
    args = parser.parse_args(["tui", "--tui-root", "/tmp/workspace"])
    assert args.command == "tui"
    assert args.tui_root == "/tmp/workspace"
    assert args.func == cli.cmd_tui


def test_main_dispatches_explicit_tui_subcommand(monkeypatch):
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)

    args = SimpleNamespace(command="tui", tui=False)
    args.func = lambda _args: 0

    class _Parser:
        def parse_args(self):
            return args

    monkeypatch.setattr(cli, "build_parser", lambda: _Parser())
    monkeypatch.setattr(cli, "argcomplete", None)

    called = {}

    def _fake_cmd_tui(parsed_args):
        called["args"] = parsed_args
        return 0

    args.func = _fake_cmd_tui

    try:
        cli.main()
        assert False, "main() should raise SystemExit"
    except SystemExit as exc:
        assert exc.code == 0

    assert called["args"].command == "tui"


def test_cmd_tui_about_dialog(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_dialog_available", lambda: True)
    monkeypatch.setattr(cli, "_dialog_init", lambda: (20, 84))
    monkeypatch.setattr(cli.lantern_config, "load_config", lambda: {
        "workspace_root": str(tmp_path),
        "scan_json_path": str(tmp_path / "repos.json"),
    })
    monkeypatch.setattr(cli.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=0))

    choices = iter(["about", "exit"])
    monkeypatch.setattr(cli, "_dialog_menu", lambda *_args, **_kwargs: next(choices))

    captured = {}

    def _fake_about(height=12, width=76):
        captured["size"] = (height, width)

    monkeypatch.setattr(cli, "_show_about_dialog", _fake_about)

    rc = cli.cmd_tui(SimpleNamespace(tui_root=""))

    assert rc == 0
    assert captured["size"] == (20, 84)


def test_cmd_tui_fleet_does_not_prompt_for_fetch_or_pr_info_before_operation(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    scan_path = workspace / "repos.json"
    scan_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(cli, "_dialog_available", lambda: True)
    monkeypatch.setattr(cli, "_dialog_init", lambda: (20, 84))
    monkeypatch.setattr(
        cli.lantern_config,
        "load_config",
        lambda: {
            "workspace_root": str(workspace),
            "scan_json_path": str(scan_path),
        },
    )
    monkeypatch.setattr(cli.lantern_config, "list_servers", lambda _config: [])
    monkeypatch.setattr(cli.lantern_config, "get_server_name", lambda _config, _fallback: "")
    monkeypatch.setattr(cli.lantern_config, "get_server", lambda _config, _server: {"provider": "github"})
    monkeypatch.setattr(cli.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=0))

    menu_choices = iter(["fleet", "back", "exit"])

    def fake_menu(_title, _text, _items, *_args, **_kwargs):
        return next(menu_choices)

    yesno_titles = []

    def fake_yesno(title, *_args, **_kwargs):
        yesno_titles.append(title)
        return False

    monkeypatch.setattr(cli, "_dialog_menu", fake_menu)
    monkeypatch.setattr(cli, "_dialog_yesno", fake_yesno)

    rc = cli.cmd_tui(SimpleNamespace(tui_root=""))

    assert rc == 0
    assert yesno_titles == ["Fleet Layout"]


def test_cmd_tui_custom_select_skips_scope_and_shows_full_checklist(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    scan_path = workspace / "repos.json"
    scan_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(cli, "_dialog_available", lambda: True)
    monkeypatch.setattr(cli, "_dialog_init", lambda: (20, 84))
    monkeypatch.setattr(
        cli.lantern_config,
        "load_config",
        lambda: {
            "workspace_root": str(workspace),
            "scan_json_path": str(scan_path),
        },
    )
    monkeypatch.setattr(cli.lantern_config, "list_servers", lambda _config: [])
    monkeypatch.setattr(cli.lantern_config, "get_server", lambda _config, _server: {"provider": "github"})
    monkeypatch.setattr(cli.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=0))
    monkeypatch.setattr(
        cli,
        "_fleet_plan_records",
        lambda _args: (
            [
                {"repo": "alpha", "state": "behind-remote", "branch": "main", "latest_branch": "main", "action": "pull", "clean": "yes"},
                {"repo": "beta", "state": "ahead-remote", "branch": "main", "latest_branch": "main", "action": "push", "clean": "yes"},
                {"repo": "gamma", "state": "in-sync", "branch": "main", "latest_branch": "feature/x", "action": "-", "clean": "yes"},
                {"repo": "delta", "state": "in-sync", "branch": "main", "latest_branch": "main", "action": "-", "clean": "yes"},
            ],
            {},
        ),
    )
    monkeypatch.setattr(cli, "_dialog_infobox", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_dialog_msgbox", lambda *_args, **_kwargs: None)

    menu_titles = []
    menu_choices = {
        "Git Lantern": iter(["fleet", "exit"]),
        "Fleet": iter(["smart_sync"]),
        "Smart Sync": iter(["custom_select"]),
    }

    def fake_menu(title, *_args, **_kwargs):
        menu_titles.append(title)
        assert title != "Scope"
        return next(menu_choices[title])

    checklist_calls = []

    def fake_checklist(_title, _text, items, *_args, **_kwargs):
        checklist_calls.append(items)
        return []

    monkeypatch.setattr(cli, "_dialog_menu", fake_menu)
    monkeypatch.setattr(cli, "_dialog_checklist", fake_checklist)
    monkeypatch.setattr(cli, "_dialog_yesno", lambda *_args, **_kwargs: False)

    rc = cli.cmd_tui(SimpleNamespace(tui_root=""))

    assert rc == 0
    assert "Scope" not in menu_titles
    assert len(checklist_calls) == 1
    assert [item[0] for item in checklist_calls[0]] == ["1", "2", "3", "4"]
    assert [item[2] for item in checklist_calls[0]] == [True, False, True, False]
    assert [item[1].split(" [", 1)[0] for item in checklist_calls[0]] == ["alpha", "beta", "gamma", "delta"]
    assert "pull" in checklist_calls[0][0][1]
    assert "feature/x" in checklist_calls[0][2][1]


def test_run_lantern_subprocess_shows_output_when_not_capturing(monkeypatch):
    captured = {}

    def _fake_run(cmd_args, **kwargs):
        captured["cmd_args"] = cmd_args
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cli.subprocess, "run", _fake_run)

    result = cli._run_lantern_subprocess(["lantern", "fleet", "apply"], 20, 80, capture=False)

    assert result.returncode == 0
    assert "stdout" not in captured["kwargs"]
    assert "stderr" not in captured["kwargs"]
    assert "capture_output" not in captured["kwargs"]


def test_run_lantern_subprocess_can_stream_output_when_requested(monkeypatch):
    captured = {}

    def _fake_run(cmd_args, **kwargs):
        captured["cmd_args"] = cmd_args
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cli.subprocess, "run", _fake_run)

    result = cli._run_lantern_subprocess(
        ["lantern", "fleet", "apply"],
        20,
        80,
        capture=False,
        show_live_output=True,
    )

    assert result.returncode == 0
    assert "stdout" not in captured["kwargs"]
    assert "stderr" not in captured["kwargs"]
    assert "capture_output" not in captured["kwargs"]


def test_run_lantern_subprocess_hides_output_when_requested(monkeypatch):
    captured = {}

    def _fake_run(cmd_args, **kwargs):
        captured["cmd_args"] = cmd_args
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cli.subprocess, "run", _fake_run)

    result = cli._run_lantern_subprocess(
        ["lantern", "fleet", "apply"],
        20,
        80,
        capture=False,
        show_live_output=False,
    )

    assert result.returncode == 0
    assert captured["kwargs"]["stdout"] is cli.subprocess.DEVNULL
    assert captured["kwargs"]["stderr"] is cli.subprocess.PIPE
    assert "capture_output" not in captured["kwargs"]


def test_run_lantern_subprocess_captures_stderr_when_hiding_output(monkeypatch):
    captured = {}

    def _fake_run(cmd_args, **kwargs):
        captured["cmd_args"] = cmd_args
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(cli.subprocess, "run", _fake_run)

    result = cli._run_lantern_subprocess(
        ["lantern", "fleet", "apply"],
        20,
        80,
        capture=False,
        show_live_output=False,
    )

    assert result.returncode == 1
    assert captured["kwargs"]["stdout"] is cli.subprocess.DEVNULL
    assert captured["kwargs"]["stderr"] is cli.subprocess.PIPE
