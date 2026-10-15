import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from lantern import todo_issues  # noqa: E402


def test_md_file_two_items_with_ids(tmp_path):
    todo = tmp_path / "TODO.md"
    todo.write_text(
        "# Backlog\n\nIntro text is ignored.\n\n"
        "## Add login rate limit\n"
        "ID: 001\n"
        "Limit failed logins per IP.\n\n"
        "Return 429 after five attempts.\n\n"
        "## Cache user settings\n"
        "**ID:** 002\n"
        "Cache settings for 60 seconds.\n"
        "### Notes\n"
        "Sub-headings stay in the description.\n"
        "## Empty item\n"
        "ID: 003\n",
        encoding="utf-8",
    )

    items = todo_issues.read_todo_file(todo)
    assert [(i.item_id, i.title) for i in items] == [
        ("001", "Add login rate limit"),
        ("002", "Cache user settings"),
    ]
    assert items[0].description == (
        "Limit failed logins per IP.\n\nReturn 429 after five attempts."
    )
    assert items[1].description == (
        "Cache settings for 60 seconds.\n### Notes\nSub-headings stay in the description."
    )
    assert items[0].labels == []


def test_md_file_without_id(tmp_path):
    todo = tmp_path / "todo.MD"
    todo.write_text("## Add audit log\n\nRecord admin actions.\n", encoding="utf-8")
    items = todo_issues.read_todo_file(todo)
    assert len(items) == 1
    assert items[0].item_id == ""
    assert items[0].description == "Record admin actions."


def test_md_heading_inside_code_fence_is_description(tmp_path):
    todo = tmp_path / "TODO.md"
    todo.write_text(
        "## Add login rate limit\nExample:\n```\n## not a heading\n```\n",
        encoding="utf-8",
    )
    items = todo_issues.read_todo_file(todo)
    assert len(items) == 1
    assert items[0].description == "Example:\n```\n## not a heading\n```"


def test_md_file_parses_only_inside_todo_block(tmp_path):
    todo = tmp_path / "TODO.md"
    todo.write_text(
        "## Outside before\nNot an issue.\n"
        "[TODO]\n"
        "## Add login rate limit\nID: 7\nLimit failed logins.\n"
        "[/TODO]\n"
        "## Outside after\nNot an issue either.\n",
        encoding="utf-8",
    )
    items = todo_issues.read_todo_file(todo)
    assert [(i.item_id, i.title, i.description) for i in items] == [
        ("7", "Add login rate limit", "Limit failed logins.")
    ]


def test_issue_directory(tmp_path, capsys):
    issues = tmp_path / "issues"
    issues.mkdir()
    (issues / "b-inline.md").write_text(
        "---\n"
        "title: \"Add login rate limit\"\n"
        "labels: [security, 'backend']\n"
        "id: 12\n"
        "---\n"
        "Limit failed logins per IP.\n",
        encoding="utf-8",
    )
    (issues / "a-block.md").write_text(
        "---\n"
        "title: Cache user settings\n"
        "labels:\n"
        "  - performance\n"
        "  - \"backend\"\n"
        "---\n\n"
        "Cache settings for 60 seconds.\n",
        encoding="utf-8",
    )
    (issues / "c-heading.md").write_text(
        "# Add audit log\n\nRecord admin actions.\n", encoding="utf-8"
    )
    (issues / "d-untitled.md").write_text("Just some text.\n", encoding="utf-8")
    (issues / "notes.txt").write_text("ignored\n", encoding="utf-8")
    nested = issues / "nested"
    nested.mkdir()
    (nested / "deep.md").write_text("# Deep\n\nNot read.\n", encoding="utf-8")

    items = todo_issues.read_todo_file(issues)
    out = capsys.readouterr().out

    assert [(i.title, i.item_id, i.labels) for i in items] == [
        ("Cache user settings", "", ["performance", "backend"]),
        ("Add login rate limit", "12", ["security", "backend"]),
        ("Add audit log", "", []),
    ]
    assert items[0].description == "Cache settings for 60 seconds."
    assert items[2].description == "Record admin actions."
    assert "d-untitled.md" in out
    assert "no title" in out


def test_issue_file_skips_shell_comment_in_fence(tmp_path, capsys):
    issues = tmp_path / "issues"
    issues.mkdir()
    (issues / "x.md").write_text(
        "Run:\n```\n# install deps\nmake\n```\n", encoding="utf-8"
    )
    assert todo_issues.read_todo_file(issues) == []
    assert "x.md: no title" in capsys.readouterr().out


def test_main_passes_item_labels_to_gh(tmp_path, monkeypatch, capsys):
    issues = tmp_path / "issues"
    issues.mkdir()
    (issues / "one.md").write_text(
        "---\ntitle: Add login rate limit\nlabels: [security, todo]\n---\nLimit logins.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(todo_issues, "fetch_existing_issues", lambda *_a, **_k: set())
    calls = []

    def _fake_run(cmd, **_kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="https://example.invalid/1\n", stderr="")

    monkeypatch.setattr(todo_issues.subprocess, "run", _fake_run)
    rc = todo_issues.main(["--todo-file", str(issues), "--label", "todo"])
    assert rc == 0
    assert len(calls) == 1
    cmd = calls[0]
    labels = [cmd[i + 1] for i, part in enumerate(cmd) if part == "--label"]
    assert labels == ["todo", "security"]
    assert cmd[cmd.index("--title") + 1] == "Add login rate limit"
    assert cmd[cmd.index("--body") + 1] == "Limit logins."


def test_txt_file_parsing_unchanged(tmp_path):
    todo = tmp_path / "TODO.txt"
    todo.write_text(
        "[TODO]\nID: 001\nTitle: First\nDescription: One\n\n"
        "## Not a heading for txt\n[/TODO]\n",
        encoding="utf-8",
    )
    items = todo_issues.read_todo_file(todo)
    assert len(items) == 1
    assert items[0].title == "First"
    assert items[0].description == "One\n\n## Not a heading for txt"
    assert items[0].labels == []


def test_front_matter_parser():
    meta = todo_issues.parse_front_matter(
        ["title: 'A: b'", "labels:", "- x", "- 'y'", "id: \"5\"", "empty: []"]
    )
    assert meta == {"title": "A: b", "labels": ["x", "y"], "id": "5", "empty": []}


def test_md_bom_crlf_and_mixed_fences(tmp_path):
    todo = tmp_path / "TODO.md"
    todo.write_bytes(
        b"\xef\xbb\xbf## First\r\nbody1\r\n~~~\r\n```\r\n## not a heading\r\n~~~\r\n"
        b"### sub\r\n## Fix `foo` ##\r\n\r\nID: 7\r\nbody2\r\n"
    )
    items = todo_issues.read_todo_file(todo)
    assert [(i.item_id, i.title) for i in items] == [("", "First"), ("7", "Fix `foo`")]
    assert "## not a heading" in items[0].description
    assert "### sub" in items[0].description


def test_issue_dir_bad_files_are_skipped(tmp_path, capsys):
    (tmp_path / "bad.md").write_bytes(b"# T\nhi \xff\n")
    (tmp_path / "open.md").write_text("---\ntitle: x\nbody\n", encoding="utf-8")
    (tmp_path / "UP.MD").write_text("# Upper\nbody\n", encoding="utf-8")
    (tmp_path / "bom.md").write_bytes(
        b"\xef\xbb\xbf---\r\ntitle: \"a: b\"\r\nlabels: x, y z\r\n---\r\nbody\r\n"
    )
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "s.md").write_text("# Sub\nbody\n", encoding="utf-8")
    items = todo_issues.read_todo_file(tmp_path)
    assert [(i.title, i.labels) for i in items] == [("Upper", []), ("a: b", ["x", "y z"])]
    out = capsys.readouterr().out
    assert "bad.md: not valid UTF-8" in out
    assert "open.md: front matter" in out


def test_non_utf8_md_file_reports_error(tmp_path, capsys):
    todo = tmp_path / "TODO.md"
    todo.write_bytes(b"## T\n\xff\n")
    assert todo_issues.main(["--todo-file", str(todo)]) == 1
    assert "not valid UTF-8" in capsys.readouterr().err


def test_dry_run_shows_labels(capsys):
    item = todo_issues.TodoItem(item_id="", title="T", description="d", labels=["two words"])
    todo_issues.create_issue(item, None, ["cli"], dry_run=True)
    assert "[labels: cli, two words]" in capsys.readouterr().out
