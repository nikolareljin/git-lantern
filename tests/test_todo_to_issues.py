import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from lantern import todo_issues  # noqa: E402


def test_parse_todo_items_basic():
    content = """
[TODO]
ID: 001
Title: First title
Description: First description

ID: 002
Title: Second title
Description: Second description
[/TODO]
""".strip()

    items = todo_issues.parse_todo_items(content)
    assert len(items) == 2
    assert items[0].item_id == "001"
    assert items[0].title == "First title"
    assert items[0].description == "First description"
    assert items[1].item_id == "002"
    assert items[1].title == "Second title"
    assert items[1].description == "Second description"


def test_parse_todo_items_multiline_description():
    content = """
[TODO]
ID: 010
Title: Multi line
Description: Line one
Line two

Line three
[/TODO]
""".strip()

    items = todo_issues.parse_todo_items(content)
    assert len(items) == 1
    assert items[0].description == "Line one\nLine two\n\nLine three"


def test_is_duplicate_requires_matching_title_and_description():
    item = todo_issues.TodoItem(
        item_id="001",
        title="Detect branch",
        description="Display latest branch",
    )
    body = todo_issues.build_issue_body(item)

    assert not todo_issues.is_duplicate(
        item,
        {(todo_issues.normalize_text("detect BRANCH"), todo_issues.normalize_text("other body"))},
    )
    assert not todo_issues.is_duplicate(
        item,
        {(todo_issues.normalize_text("other title"), todo_issues.normalize_text(body))},
    )


def test_is_duplicate_by_closed_issue_fingerprint():
    item = todo_issues.TodoItem(
        item_id="002",
        title="Sync TODO import",
        description="Avoid recreating closed issues",
    )
    body = todo_issues.normalize_text(todo_issues.build_issue_body(item))

    assert todo_issues.is_duplicate(
        item,
        {(todo_issues.normalize_text(item.title), body)},
    )


def test_extract_todo_block_malformed_order_falls_back():
    content = """
[/TODO]
noise
[TODO]
ID: 001
Title: Later
Description: Parse me
[/TODO]
""".strip()
    block = todo_issues.extract_todo_block(content)
    assert "Title: Later" in block


def test_fetch_existing_issues_rejects_non_list_payload(monkeypatch):
    monkeypatch.setattr(todo_issues, "run_gh_json", lambda _cmd: {"items": []})
    try:
        todo_issues.fetch_existing_issues(repo=None, limit=10)
    except ValueError as exc:
        assert "Expected a JSON list" in str(exc)
    else:
        raise AssertionError("Expected ValueError for non-list gh payload")


def test_fetch_existing_issues_tracks_closed_issue_fingerprints(monkeypatch):
    monkeypatch.setattr(
        todo_issues,
        "run_gh_json",
        lambda _cmd: [
            {
                "number": 17,
                "title": "Sync TODO import",
                "body": "ID: 002\n\nAvoid recreating closed issues",
                "state": "CLOSED",
            }
        ],
    )

    seen_fingerprints = todo_issues.fetch_existing_issues(
        repo=None,
        limit=10,
    )

    normalized_title = todo_issues.normalize_text("Sync TODO import")
    normalized_body = todo_issues.normalize_text("ID: 002\n\nAvoid recreating closed issues")
    assert (normalized_title, normalized_body) in seen_fingerprints


def test_run_gh_json_reports_missing_gh(monkeypatch):
    def _raise(*_args, **_kwargs):
        raise FileNotFoundError("gh not found")

    monkeypatch.setattr(todo_issues.subprocess, "run", _raise)
    try:
        todo_issues.run_gh_json(["gh", "issue", "list"])
    except RuntimeError as exc:
        assert "gh CLI not found" in str(exc)
    else:
        raise AssertionError("Expected RuntimeError when gh is missing")


def test_main_reports_missing_gh(tmp_path, monkeypatch, capsys):
    todo_file = tmp_path / "TODO.txt"
    todo_file.write_text(
        "\n".join(
            [
                "[TODO]",
                "ID: 001",
                "Title: Title",
                "Description: Desc",
                "[/TODO]",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        todo_issues,
        "fetch_existing_issues",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(todo_issues.GH_NOT_FOUND_ERROR)),
    )

    rc = todo_issues.main(["--todo-file", str(todo_file)])
    captured = capsys.readouterr()
    assert rc == 1
    assert "gh CLI not found" in captured.err


def test_main_executes_as_module_smoke(tmp_path):
    todo_file = tmp_path / "TODO.txt"
    todo_file.write_text("[TODO]\n[/TODO]\n", encoding="utf-8")
    cmd = [
        sys.executable,
        "-m",
        "lantern.todo_issues",
        "--todo-file",
        str(todo_file),
        "--dry-run",
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = SRC
    import subprocess

    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert result.returncode == 0


def test_main_skips_closed_issue_duplicate_by_matching_title_and_description(
    tmp_path, monkeypatch, capsys
):
    todo_file = tmp_path / "TODO.txt"
    todo_file.write_text(
        "\n".join(
            [
                "[TODO]",
                "ID: 002",
                "Title: Sync TODO import",
                "Description: Avoid recreating closed issues",
                "[/TODO]",
            ]
        ),
        encoding="utf-8",
    )

    normalized_title = todo_issues.normalize_text("Sync TODO import")
    normalized_body = todo_issues.normalize_text("ID: 002\n\nAvoid recreating closed issues")

    monkeypatch.setattr(
        todo_issues,
        "fetch_existing_issues",
        lambda *_args, **_kwargs: {(normalized_title, normalized_body)},
    )

    create_calls = []
    monkeypatch.setattr(
        todo_issues,
        "create_issue",
        lambda *args, **kwargs: create_calls.append((args, kwargs)),
    )

    rc = todo_issues.main(["--todo-file", str(todo_file)])
    captured = capsys.readouterr()

    assert rc == 0
    assert not create_calls
    assert "[SKIPPED] Duplicate title/body fingerprint: Sync TODO import" in captured.out
