"""Code search tool — grep-like search across the workspace."""

from __future__ import annotations

import subprocess

from app.config import WORKSPACE


def search_code(
    pattern: str,
    path: str = ".",
    include: str = "",
    max_results: int = 50,
) -> str:
    """Search file contents for a regex pattern (like grep).

    Returns matching lines with file paths and line numbers.
    """
    try:
        target = (WORKSPACE / path).resolve()
        if not str(target).startswith(str(WORKSPACE)):
            return "Error: path escapes workspace"
        if not target.exists():
            return f"Error: path does not exist: {path}"

        cmd = ["grep", "-rn", "--include", include or "*", "-E", pattern, str(target)]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 1:
            return "No matches found."
        if result.returncode > 1:
            return f"Error: {result.stderr.strip()}"

        lines = result.stdout.strip().split("\n")
        if len(lines) > max_results:
            truncated = lines[:max_results]
            truncated.append(f"\n... ({len(lines) - max_results} more results truncated)")
            return "\n".join(truncated)

        return result.stdout.strip() if result.stdout.strip() else "No matches found."
    except subprocess.TimeoutExpired:
        return "Error: search timed out after 30 seconds"
    except Exception as exc:
        return f"Error: {exc}"
