#!/usr/bin/env python3
"""Create GitHub issues from TODO.txt, Markdown TODO files or issue directories."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union


TODO_START = "[TODO]"
TODO_END = "[/TODO]"
GH_NOT_FOUND_ERROR = "gh CLI not found. Install GitHub CLI and ensure 'gh' is in PATH."


@dataclass
class TodoItem:
    item_id: str
    title: str
    description: str
    labels: List[str] = field(default_factory=list)


def normalize_text(value: str) -> str:
    """Normalize text for duplicate checks."""
    collapsed = re.sub(r"\s+", " ", value.strip())
    return collapsed.casefold()


def extract_todo_block(text: str) -> str:
    """Return first well-formed [TODO]...[/TODO] block or original text."""
    start_index = text.find(TODO_START)
    if start_index == -1:
        return text

    content_start = start_index + len(TODO_START)
    end_index = text.find(TODO_END, content_start)
    if end_index == -1:
        return text

    return text[content_start:end_index]


def parse_todo_items(text: str) -> List[TodoItem]:
    block = extract_todo_block(text)
    lines = block.splitlines()

    items: List[TodoItem] = []
    current_id = ""
    current_title = ""
    desc_lines: List[str] = []
    collecting_desc = False

    def flush_item() -> None:
        nonlocal current_id, current_title, desc_lines, collecting_desc
        description = "\n".join(desc_lines).strip()
        if current_title and description:
            items.append(
                TodoItem(
                    item_id=current_id.strip(),
                    title=current_title.strip(),
                    description=description,
                )
            )
        current_id = ""
        current_title = ""
        desc_lines = []
        collecting_desc = False

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            if collecting_desc:
                desc_lines.append("")
            continue

        if line.startswith("ID:"):
            if current_title or desc_lines:
                flush_item()
            current_id = line.partition(":")[2].strip()
            collecting_desc = False
            continue

        if line.startswith("Title:"):
            current_title = line.partition(":")[2].strip()
            collecting_desc = False
            continue

        if line.startswith("Description:"):
            collecting_desc = True
            desc_lines = [line.partition(":")[2].strip()]
            continue

        if collecting_desc:
            desc_lines.append(raw_line.rstrip())

    if current_title or desc_lines:
        flush_item()

    return items


MD_ID_RE = re.compile(r"^(?:\*\*ID:\*\*|ID:)\s*(.*)$")


def parse_markdown_items(text: str) -> List[TodoItem]:
    """Parse one item per level-2 heading (`## Title`).

    An optional `ID: x` or `**ID:** x` line may follow the heading. Everything
    else until the next `## ` heading is the description.
    """
    block = extract_todo_block(text)
    items: List[TodoItem] = []
    title = ""
    item_id = ""
    desc_lines: List[str] = []
    expect_id = False
    in_fence = False

    def flush() -> None:
        description = "\n".join(desc_lines).strip()
        if title and description:
            items.append(TodoItem(item_id=item_id, title=title, description=description))

    for raw_line in block.splitlines():
        line = raw_line.strip()
        if line.startswith("```") or line.startswith("~~~"):
            in_fence = not in_fence
        if not in_fence and raw_line.startswith("## "):
            flush()
            title = raw_line[3:].strip()
            item_id = ""
            desc_lines = []
            expect_id = True
            continue
        if not title:
            continue
        if expect_id and line:
            expect_id = False
            match = MD_ID_RE.match(line)
            if match:
                item_id = match.group(1).strip()
                continue
        desc_lines.append(raw_line.rstrip())
    flush()
    return items


FrontMatterValue = Union[str, List[str]]


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def parse_front_matter(lines: Sequence[str]) -> Dict[str, FrontMatterValue]:
    """Parse a minimal YAML subset: `key: value`, `key: [a, b]`, `- item` lists."""
    data: Dict[str, FrontMatterValue] = {}
    current_list_key = ""
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("- ") or line == "-":
            if current_list_key:
                value = _strip_quotes(line[1:])
                existing = data.get(current_list_key)
                if not isinstance(existing, list):
                    existing = []
                    data[current_list_key] = existing
                if value:
                    existing.append(value)
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        current_list_key = ""
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1]
            data[key] = [_strip_quotes(part) for part in inner.split(",") if part.strip()]
        elif value:
            data[key] = _strip_quotes(value)
        else:
            data[key] = []
            current_list_key = key
    return data


def split_front_matter(text: str) -> Tuple[Dict[str, FrontMatterValue], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return parse_front_matter(lines[1:index]), "\n".join(lines[index + 1 :])
    return {}, text


def _as_text(value: Optional[FrontMatterValue]) -> str:
    if isinstance(value, list):
        return ", ".join(value)
    return (value or "").strip()


def parse_issue_file(path: Path) -> Optional[TodoItem]:
    """Parse one Markdown issue file; return None (with a message) when unusable."""
    meta, body = split_front_matter(path.read_text(encoding="utf-8"))
    title = _as_text(meta.get("title"))
    body_lines = body.splitlines()
    if not title:
        in_fence = False
        for index, line in enumerate(body_lines):
            if line.strip().startswith(("```", "~~~")):
                in_fence = not in_fence
            if not in_fence and line.startswith("# "):
                title = line[2:].strip()
                del body_lines[index]
                break
    if not title:
        print(f"[SKIPPED] {path.name}: no title (front matter 'title' or '# ' heading)")
        return None
    description = "\n".join(body_lines).strip()
    if not description:
        print(f"[SKIPPED] {path.name}: empty description")
        return None
    raw_labels = meta.get("labels", [])
    if isinstance(raw_labels, str):
        raw_labels = [raw_labels] if raw_labels else []
    return TodoItem(
        item_id=_as_text(meta.get("id")),
        title=title,
        description=description,
        labels=[label for label in raw_labels if label],
    )


def read_issue_dir(dir_path: Path) -> List[TodoItem]:
    items: List[TodoItem] = []
    for path in sorted(dir_path.glob("*.md")):
        if not path.is_file():
            continue
        item = parse_issue_file(path)
        if item is not None:
            items.append(item)
    return items


def read_todo_file(todo_path: Path) -> List[TodoItem]:
    if not todo_path.exists():
        raise FileNotFoundError(f"TODO file not found: {todo_path}")
    if todo_path.is_dir():
        return read_issue_dir(todo_path)
    text = todo_path.read_text(encoding="utf-8")
    if todo_path.suffix.lower() == ".md":
        return parse_markdown_items(text)
    return parse_todo_items(text)


def run_gh_json(command: Sequence[str]) -> object:
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError(GH_NOT_FOUND_ERROR) from exc
    if not result.stdout.strip():
        return []
    return json.loads(result.stdout)


def fetch_existing_issues(repo: Optional[str], limit: int) -> Set[Tuple[str, str]]:
    cmd = [
        "gh",
        "issue",
        "list",
        "--state",
        "all",
        "--limit",
        str(limit),
        "--json",
        "number,title,body",
    ]
    if repo:
        cmd.extend(["-R", repo])

    issues = run_gh_json(cmd)
    if not isinstance(issues, list):
        raise ValueError(
            f"Unexpected gh issue payload type: {type(issues).__name__}. Expected a JSON list."
        )
    fingerprint_set: Set[Tuple[str, str]] = set()
    for issue in issues:
        if not isinstance(issue, dict):
            raise ValueError(
                f"Unexpected gh issue entry type: {type(issue).__name__}. Expected an object."
            )
        title = normalize_text(issue.get("title") or "")
        body = normalize_text(issue.get("body") or "")
        if title and body:
            fingerprint_set.add((title, body))
    return fingerprint_set


def build_issue_body(item: TodoItem) -> str:
    if item.item_id:
        return f"ID: {item.item_id}\n\n{item.description}"
    return item.description


def is_duplicate(
    item: TodoItem,
    seen_fingerprints: Set[Tuple[str, str]],
) -> bool:
    normalized_title = normalize_text(item.title)
    normalized_body = normalize_text(build_issue_body(item))
    return (normalized_title, normalized_body) in seen_fingerprints


def create_issue(
    item: TodoItem,
    repo: Optional[str],
    labels: Iterable[str],
    dry_run: bool,
) -> None:
    body = build_issue_body(item)
    if dry_run:
        print(f"[DRY-RUN] Would create issue: {item.title}")
        return

    cmd = ["gh", "issue", "create", "--title", item.title, "--body", body]
    merged_labels: List[str] = []
    for label in list(labels) + item.labels:
        if label not in merged_labels:
            merged_labels.append(label)
    for label in merged_labels:
        cmd.extend(["--label", label])
    if repo:
        cmd.extend(["-R", repo])

    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError(GH_NOT_FOUND_ERROR) from exc
    created = result.stdout.strip() or item.title
    print(f"[CREATED] {created}")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create GitHub issues from TODO.txt in the current repository while "
            "skipping duplicates by normalized title and body fingerprint, "
            "including closed issues."
        )
    )
    parser.add_argument(
        "--todo-file",
        default="TODO.txt",
        help=(
            "Path to TODO file (default: TODO.txt). A .md file gives one issue per "
            "'## Title' heading; a directory gives one issue per *.md file."
        ),
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="Optional GitHub repo in OWNER/REPO format (defaults to current repo).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=1000,
        help="How many existing issues to inspect for duplicate detection.",
    )
    parser.add_argument(
        "--label",
        action="append",
        default=[],
        help="Issue label to apply. Repeat for multiple labels.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview creation without opening any issues.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)

    try:
        items = read_todo_file(Path(args.todo_file))
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not items:
        print("No TODO items found.")
        return 0

    try:
        seen_fingerprints = fetch_existing_issues(args.repo, args.limit)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        print("Failed to load existing issues via gh.", file=sys.stderr)
        if stderr:
            print(stderr, file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"Failed to parse gh JSON output: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Failed to parse gh issue payload: {exc}", file=sys.stderr)
        return 1

    created = 0
    skipped = 0
    for item in items:
        if is_duplicate(item, seen_fingerprints):
            print(f"[SKIPPED] Duplicate title/body fingerprint: {item.title}")
            skipped += 1
            continue

        try:
            create_issue(item, args.repo, args.label, args.dry_run)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            print(f"[ERROR] Failed to create issue: {item.title}", file=sys.stderr)
            if stderr:
                print(stderr, file=sys.stderr)
            continue

        normalized_body = normalize_text(build_issue_body(item))
        seen_fingerprints.add((normalize_text(item.title), normalized_body))
        created += 1

    print(f"Done. Created: {created}, Skipped duplicates: {skipped}, Parsed: {len(items)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
