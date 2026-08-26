"""Spinners and status indicators."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from rich.status import Status

from app.ui.console import console


@contextmanager
def spinner(message: str = "Working...") -> Iterator[Status]:
    """Show a Rich spinner that disappears when done."""
    status = console.status(f"[bold blue]●[/] {message}", spinner="dots")
    status.start()
    try:
        yield status
    finally:
        status.stop()


def status_line(message: str) -> None:
    """Print a subtle status line."""
    console.print(f"  ● {message}", style="dim")
