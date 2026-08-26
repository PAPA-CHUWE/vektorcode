"""Safe filesystem tools restricted to the active workspace."""

from __future__ import annotations

from app.config import WORKSPACE


def _resolve_safe(path: str) -> "Path":
    """Resolve *path* and ensure it stays inside the workspace."""
    from pathlib import Path

    target = (WORKSPACE / path).resolve()
    if not str(target).startswith(str(WORKSPACE)):
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


def read_file(path: str) -> str:
    """Return the text contents of a file."""
    try:
        target = _resolve_safe(path)
        if not target.exists():
            return f"Error: file does not exist: {path}"
        if not target.is_file():
            return f"Error: not a file: {path}"
        return target.read_text(encoding="utf-8")
    except PermissionError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error: {exc}"


def write_file(path: str, content: str) -> str:
    """Write *content* to a file, creating parent directories as needed."""
    try:
        target = _resolve_safe(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} bytes to {path}"
    except PermissionError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error: {exc}"
