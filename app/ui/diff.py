"""Unified diff formatting with syntax highlighting."""

from __future__ import annotations

import difflib
from typing import Optional


def format_diff(
    old: str,
    new: str,
    filename: str = "",
    max_lines: int = 40,
) -> str:
    """Return a compact unified diff string.

    The diff is formatted for terminal display with line prefixes:
      + added (green)
      - removed (red)
      @ hunk header (blue)
      context lines (dim)
    """
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    diff = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{filename}" if filename else "a/original",
            tofile=f"b/{filename}" if filename else "b/modified",
            lineterm="",
        )
    )

    if not diff:
        return "(no changes)"

    output: list[str] = []
    for line in diff[: max_lines + 2]:
        stripped = line.rstrip("\n")
        if stripped.startswith("+++") or stripped.startswith("---"):
            continue
        output.append(stripped)

    result = "\n".join(output)
    if len(diff) > max_lines + 2:
        remaining = len(diff) - max_lines - 2
        result += f"\n  ... ({remaining} more lines)"

    return result
