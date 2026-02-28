from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "todo_to_issues.py"
SPEC = spec_from_file_location("todo_to_issues", SCRIPT_PATH)
todo_to_issues = module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = todo_to_issues
SPEC.loader.exec_module(todo_to_issues)


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

    items = todo_to_issues.parse_todo_items(content)
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

    items = todo_to_issues.parse_todo_items(content)
    assert len(items) == 1
    assert items[0].description == "Line one\nLine two\n\nLine three"


def test_is_duplicate_by_title_or_description():
    item = todo_to_issues.TodoItem(
        item_id="001",
        title="Detect branch",
        description="Display latest branch",
    )
    body = todo_to_issues.build_issue_body(item)

    title_hit = todo_to_issues.is_duplicate(
        item,
        {todo_to_issues.normalize_text("detect BRANCH")},
        set(),
    )
    assert title_hit

    description_hit = todo_to_issues.is_duplicate(
        item,
        set(),
        {todo_to_issues.normalize_text(body)},
    )
    assert description_hit
