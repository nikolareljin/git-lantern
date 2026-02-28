import argparse
import os
import sys


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from lantern import cli  # noqa: E402


def test_todo_issues_parser_defaults():
    parser = cli.build_parser()
    args = parser.parse_args(["todo", "issues"])
    assert isinstance(args, argparse.Namespace)
    assert args.command == "todo"
    assert args.todo_command == "issues"
    assert args.todo_file == "TODO.txt"
    assert args.limit == 1000
    assert args.label == []
    assert args.repo == ""
    assert args.dry_run is False
    assert args.func == cli.cmd_todo_issues


def test_todo_issues_parser_flags():
    parser = cli.build_parser()
    args = parser.parse_args(
        [
            "todo",
            "issues",
            "--todo-file",
            "work/TODO.txt",
            "--repo",
            "owner/repo",
            "--limit",
            "250",
            "--label",
            "todo",
            "--label",
            "backlog",
            "--dry-run",
        ]
    )
    assert args.todo_file == "work/TODO.txt"
    assert args.repo == "owner/repo"
    assert args.limit == 250
    assert args.label == ["todo", "backlog"]
    assert args.dry_run is True
