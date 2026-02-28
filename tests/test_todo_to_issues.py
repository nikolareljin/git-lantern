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


def test_is_duplicate_by_title_or_description():
    item = todo_issues.TodoItem(
        item_id="001",
        title="Detect branch",
        description="Display latest branch",
    )
    body = todo_issues.build_issue_body(item)

    title_hit = todo_issues.is_duplicate(
        item,
        {todo_issues.normalize_text("detect BRANCH")},
        set(),
    )
    assert title_hit

    description_hit = todo_issues.is_duplicate(
        item,
        set(),
        {todo_issues.normalize_text(body)},
    )
    assert description_hit


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
