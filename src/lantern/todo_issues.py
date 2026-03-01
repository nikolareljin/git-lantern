#!/usr/bin/env python3
"""Create GitHub issues from TODO.txt entries."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple


TODO_START = "[TODO]"
TODO_END = "[/TODO]"
GH_NOT_FOUND_ERROR = "gh CLI not found. Install GitHub CLI and ensure 'gh' is in PATH."


@dataclass
class TodoItem:
    item_id: str
    title: str
    description: str


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


def read_todo_file(todo_path: Path) -> List[TodoItem]:
    if not todo_path.exists():
        raise FileNotFoundError(f"TODO file not found: {todo_path}")
    return parse_todo_items(todo_path.read_text(encoding="utf-8"))


def run_gh_json(command: Sequence[str]) -> object:
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError(GH_NOT_FOUND_ERROR) from exc
    if not result.stdout.strip():
        return []
    return json.loads(result.stdout)


def fetch_existing_issues(repo: Optional[str], limit: int) -> Tuple[set, set]:
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
    title_set = set()
    description_set = set()
    for issue in issues:
        if not isinstance(issue, dict):
            raise ValueError(
                f"Unexpected gh issue entry type: {type(issue).__name__}. Expected an object."
            )
        title = normalize_text(issue.get("title", ""))
        body = normalize_text(issue.get("body", ""))
        if title:
            title_set.add(title)
        if body:
            description_set.add(body)
    return title_set, description_set


def build_issue_body(item: TodoItem) -> str:
    if item.item_id:
        return f"ID: {item.item_id}\n\n{item.description}"
    return item.description


def is_duplicate(item: TodoItem, seen_titles: set, seen_descriptions: set) -> bool:
    normalized_title = normalize_text(item.title)
    normalized_description = normalize_text(build_issue_body(item))
    return normalized_title in seen_titles or normalized_description in seen_descriptions


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
    for label in labels:
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
            "skipping duplicates by title or description."
        )
    )
    parser.add_argument(
        "--todo-file",
        default="TODO.txt",
        help="Path to TODO file (default: TODO.txt).",
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
        seen_titles, seen_descriptions = fetch_existing_issues(args.repo, args.limit)
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
        if is_duplicate(item, seen_titles, seen_descriptions):
            print(f"[SKIPPED] Duplicate title/description: {item.title}")
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

        seen_titles.add(normalize_text(item.title))
        seen_descriptions.add(normalize_text(build_issue_body(item)))
        created += 1

    print(f"Done. Created: {created}, Skipped duplicates: {skipped}, Parsed: {len(items)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
