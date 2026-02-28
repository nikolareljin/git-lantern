#!/usr/bin/env python3
"""Compatibility wrapper for TODO-to-issues workflow."""

from __future__ import annotations

import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(SCRIPT_DIR)
SRC_DIR = os.path.join(REPO_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from lantern.todo_issues import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
