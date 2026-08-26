"""Status and clear commands: /status, /clear."""

from __future__ import annotations

import time

_start_time = time.time()


def cmd_status(args: list[str]) -> str:
    """Show current session status."""
    elapsed = time.time() - _start_time
    minutes = int(elapsed // 60)
    seconds = int(elapsed % 60)
    return (
        f"Session uptime: {minutes}m {seconds}s\n"
        "Type /clear to reset conversation history."
    )


def cmd_clear(args: list[str]) -> str:
    """Signal to clear conversation history (handled in main.py)."""
    return "__CLEAR__"
