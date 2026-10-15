import os
import subprocess
import sys

import pytest

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


def test_md_heading_without_description_is_reported(tmp_path, capsys):
    todo = tmp_path / "TODO.md"
    todo.write_text("## Real\nBody.\n\n## Empty one\n", encoding="utf-8")
    items = todo_issues.read_todo_file(todo)
    assert [item.title for item in items] == ["Real"]
    assert "[SKIPPED] Empty one: empty description" in capsys.readouterr().out


def test_issue_dir_ignores_dotfiles(tmp_path):
    (tmp_path / ".draft.md").write_text("# Draft\nNot ready.\n", encoding="utf-8")
    (tmp_path / "real.md").write_text("# Real\nReady.\n", encoding="utf-8")
    items = todo_issues.read_todo_file(tmp_path)
    assert [item.title for item in items] == ["Real"]


def test_front_matter_keys_case_insensitive_and_list_comment():
    meta = todo_issues.parse_front_matter(
        ["Title: Fix #12", "LABELS: [a, b]  # team labels"]
    )
    assert meta == {"title": "Fix #12", "labels": ["a", "b"]}


def test_issue_dir_unreadable_file_is_skipped(tmp_path, monkeypatch, capsys):
    (tmp_path / "a.md").write_text("# A\nBody.\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("# B\nBody.\n", encoding="utf-8")
    real_read = todo_issues.read_markdown_text

    def fake_read(path):
        if path.name == "a.md":
            raise PermissionError(13, "Permission denied")
        return real_read(path)

    monkeypatch.setattr(todo_issues, "read_markdown_text", fake_read)
    items = todo_issues.read_todo_file(tmp_path)
    assert [item.title for item in items] == ["B"]
    assert "[SKIPPED] a.md: Permission denied" in capsys.readouterr().out


FAKE_GH = """#!/bin/sh
printf '%s\\n' "$*" >> "$GH_LOG"
if [ "$1 $2" = "issue list" ]; then cat "$GH_LIST"; fi
"""


@pytest.mark.skipif(os.name == "nt", reason="fake gh is a POSIX shell script")
def test_cli_end_to_end_with_fake_gh(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH, encoding="utf-8")
    gh.chmod(0o755)
    issues = tmp_path / "issues"
    issues.mkdir()
    (issues / "new.md").write_text(
        "---\ntitle: New item\nlabels: [backend]\n---\nDo it.\n", encoding="utf-8"
    )
    (issues / "old.md").write_text("# Old item\nAlready filed.\n", encoding="utf-8")
    gh_list = tmp_path / "list.json"
    gh_list.write_text(
        '[{"number": 1, "title": "Old item", "body": "Already  filed.\\n"}]',
        encoding="utf-8",
    )
    env = dict(os.environ)
    env.update(
        PATH=f"{bin_dir}{os.pathsep}{env.get('PATH', '')}",
        PYTHONPATH=SRC,
        GH_LOG=str(tmp_path / "gh.log"),
        GH_LIST=str(gh_list),
    )
    result = subprocess.run(
        [sys.executable, "-m", "lantern", "todo", "issues", "--todo-file", str(issues),
         "--label", "todo", "--repo", "o/r", "--limit", "5"],
        capture_output=True, text=True, env=env, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Created: 1, Skipped duplicates: 1, Parsed: 2" in result.stdout
    log = (tmp_path / "gh.log").read_text(encoding="utf-8").splitlines()
    assert log[0] == "issue list --state all --limit 5 --json number,title,body -R o/r"
    assert log[1] == (
        "issue create --title New item --body Do it. --label todo --label backend -R o/r"
    )


AGENTVAULT_BODY = (
    "Description: Users need login.\n\n"
    "Implementation Details:\n- add form\n- add route\n\n"
    "Acceptance Criteria:\n- user logs in\n"
)


def test_md_and_txt_same_item_give_same_issue_body(tmp_path):
    md = tmp_path / "TODO.md"
    md.write_text("# TODO\n\n## Add login\nID: T-1\n" + AGENTVAULT_BODY, encoding="utf-8")
    txt = tmp_path / "TODO.txt"
    txt.write_text(
        "[TODO]\nID: T-1\nTitle: Add login\n" + AGENTVAULT_BODY + "[/TODO]\n",
        encoding="utf-8",
    )

    (md_item,) = todo_issues.read_todo_file(md)
    (txt_item,) = todo_issues.read_todo_file(txt)
    body = todo_issues.build_issue_body(md_item)
    assert body == todo_issues.build_issue_body(txt_item)
    assert body.startswith("ID: T-1\n\nUsers need login.\n\nImplementation Details:\n- add form")
    assert "Acceptance Criteria:\n- user logs in" in body
    seen = {(todo_issues.normalize_text(txt_item.title), todo_issues.normalize_text(body))}
    assert todo_issues.is_duplicate(md_item, seen)


def test_md_description_label_without_id_and_on_own_line(tmp_path):
    md = tmp_path / "TODO.md"
    md.write_text(
        "## A\n**Description:**\nText A.\n\n## B\nDescription is not a label here.\n",
        encoding="utf-8",
    )
    items = todo_issues.read_todo_file(md)
    assert [i.description for i in items] == ["Text A.", "Description is not a label here."]


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="needs POSIX permissions")
def test_unreadable_single_file_exits_1_without_traceback(tmp_path, capsys):
    md = tmp_path / "TODO.md"
    md.write_text("## A\nText.\n", encoding="utf-8")
    md.chmod(0)
    try:
        rc = todo_issues.main(["--todo-file", str(md), "--dry-run"])
    finally:
        md.chmod(0o600)
    assert rc == 1
    assert "cannot read TODO file" in capsys.readouterr().err


def test_issue_dir_refuses_symlink_leaving_directory(tmp_path, capsys):
    secret = tmp_path / "secret.md"
    secret.write_text("# Key\n\nPRIVATE KEY\n", encoding="utf-8")
    issues = tmp_path / "issues"
    issues.mkdir()
    (issues / "a.md").write_text("# Kept\n\nBody.\n", encoding="utf-8")
    (issues / "b.md").write_text("# Linked inside\n\nBody b.\n", encoding="utf-8")
    (issues / "c.md").symlink_to(issues / "b.md")
    (issues / "leak.md").symlink_to(secret)

    items = todo_issues.read_todo_file(issues)

    assert [i.title for i in items] == ["Kept", "Linked inside", "Linked inside"]
    assert "PRIVATE KEY" not in "".join(i.description for i in items)
    assert "[SKIPPED] leak.md: symlink points outside" in capsys.readouterr().out


def test_oversized_body_is_skipped_without_calling_gh(tmp_path, monkeypatch, capsys):
    issues = tmp_path / "issues"
    issues.mkdir()
    (issues / "big.md").write_text("# Big\n\n" + "x" * 200_000 + "\n", encoding="utf-8")
    (issues / "ok.md").write_text("# Ok\n\nSmall.\n", encoding="utf-8")
    calls = []

    def _fake_run(cmd, **_kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="https://x/1\n", stderr="")

    monkeypatch.setattr(todo_issues, "fetch_existing_issues", lambda *_a, **_k: set())
    monkeypatch.setattr(todo_issues.subprocess, "run", _fake_run)

    assert todo_issues.main(["--todo-file", str(issues)]) == 0
    assert [c[c.index("--title") + 1] for c in calls] == ["Ok"]
    out = capsys.readouterr().out
    assert "[SKIPPED] Big: body is 200000 characters" in out
    assert "Created: 1" in out


def test_control_characters_in_printed_titles_are_escaped(tmp_path, capsys):
    todo = tmp_path / "TODO.md"
    todo.write_text("## Evil \x1b]0;pwn\x07title\n\nBody.\n", encoding="utf-8")
    items = todo_issues.read_todo_file(todo)
    todo_issues.create_issue(items[0], None, ["l\x1b[2J"], dry_run=True)
    out = capsys.readouterr().out
    assert "\x1b" not in out and "\x07" not in out
    assert "Evil \\x1b]0;pwn\\x07title" in out
