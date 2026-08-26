"""Terminal tool for executing shell commands inside the workspace."""

from __future__ import annotations

import re
import subprocess

from app.config import get_workspace

DESTRUCTIVE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\brm\s+(-[a-zA-Z]*r|-[a-zA-Z]*f|-[a-zA-Z]*rf)"),
    re.compile(r"\brm\b"),
    re.compile(r"\bgit\s+reset\s+--hard\b"),
    re.compile(r"\bgit\s+clean\b"),
    re.compile(r"\bgit\s+push\s+.*--force\b"),
    re.compile(r"\bdrop\s+table\b", re.IGNORECASE),
    re.compile(r"\bdrop\s+database\b", re.IGNORECASE),
    re.compile(r"\bdelete\s+from\b", re.IGNORECASE),
    re.compile(r"\btruncate\b", re.IGNORECASE),
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bdd\s+if="),
    re.compile(r"\bformat\b.*\b[A-Z]:\\"),
]


def _is_destructive(command: str) -> bool:
    """Check if a command matches known destructive patterns."""
    for pattern in DESTRUCTIVE_PATTERNS:
        if pattern.search(command):
            return True
    return False


def run_command(
    command: str,
    working_directory: str = ".",
    confirm: bool = False,
) -> str:
    """Execute a command inside the workspace.

    Returns stdout, stderr, and exit code as a formatted string.
    Blocks destructive commands unless confirm=True.
    """
    try:
        ws = get_workspace()
        cwd = (ws / working_directory).resolve()
        if not str(cwd).startswith(str(ws)):
            return "Error: working directory escapes workspace"
        if not cwd.exists():
            return f"Error: working directory does not exist: {working_directory}"

        if _is_destructive(command) and not confirm:
            return (
                f"BLOCKED: This command looks destructive: {command}\n"
                "Re-run with confirm=true to proceed."
            )

        result = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=60,
        )
        parts: list[str] = [f"Exit code: {result.returncode}"]
        if result.stdout:
            parts.append(f"STDOUT:\n{result.stdout}")
        if result.stderr:
            parts.append(f"STDERR:\n{result.stderr}")
        return "\n".join(parts)
    except subprocess.TimeoutExpired:
        return "Error: command timed out after 60 seconds"
    except Exception as exc:
        return f"Error: {exc}"
