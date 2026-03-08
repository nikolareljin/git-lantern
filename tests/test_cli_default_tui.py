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
