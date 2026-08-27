"""Safe filesystem tools restricted to the active workspace."""

from __future__ import annotations

from app.config import get_workspace
from app.permissions.mode import WRITE, request_approval
from app.tools.tool_result import ToolResult


def _resolve_safe(path: str) -> "Path":
    """Resolve *path* and ensure it stays inside the workspace."""
    from pathlib import Path

    ws = get_workspace()
    target = (ws / path).resolve()
    if not str(target).startswith(str(ws)):
        raise PermissionError(f"Path escapes workspace: {path}")
    return target


def list_files(path: str = ".") -> str:
    """Return a listing of files and directories under *path*."""
    try:
        target = _resolve_safe(path)
        if not target.exists():
            return f"Error: path does not exist: {path}"
        entries: list[str] = []
        for entry in sorted(target.iterdir()):
            prefix = "  dir " if entry.is_dir() else "  file"
            entries.append(f"{prefix} {entry.name}")
        return "\n".join(entries) if entries else "(empty directory)"
    except PermissionError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error: {exc}"


def read_file(path: str, max_lines: int = 500) -> str:
    """Return the text contents of a file.

    Large files are truncated with metadata so the model knows
    it received partial output and can request specific ranges.
    """
    try:
        target = _resolve_safe(path)
        if not target.exists():
            return f"Error: file does not exist: {path}"
        if not target.is_file():
            return f"Error: not a file: {path}"
        content = target.read_text(encoding="utf-8")
        lines = content.split("\n")
        total = len(lines)
        if total <= max_lines:
            return content
        truncated = "\n".join(lines[:max_lines])
        return (
            f"{truncated}\n\n"
            f"[truncated — showing lines 1-{max_lines} of {total}. "
            f"Use search_code to find specific patterns, or "
            f"read_file with a specific path for focused reading.]"
        )
    except PermissionError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error: {exc}"


def write_file(path: str, content: str) -> "str | ToolResult":
    """Write *content* to a file, creating parent directories as needed.

    Gated by the permission layer: `default` mode asks a human first,
    `auto-edit` and `full-auto` allow it straight through.
    """
    try:
        target = _resolve_safe(path)
        if not request_approval(WRITE, f"{path} ({len(content)} bytes)"):
            return ToolResult.blocked(f"BLOCKED: write to {path} was not approved.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} bytes to {path}"
    except PermissionError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error: {exc}"
