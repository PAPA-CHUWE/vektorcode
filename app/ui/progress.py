"""Spinners and status indicators with dynamic activity support."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from rich.status import Status

from app.ui.console import console


@contextmanager
def spinner(message: str = "Working...") -> Iterator[Status]:
    """Show a Rich spinner that disappears when done.

    The returned Status object has an update() method
    for changing the message dynamically.
    """
    status = console.status(f"[bold blue]●[/] {message}", spinner="dots")
    status.start()
    try:
        yield status
    finally:
        status.stop()


def activity_spinner() -> Iterator[Status]:
    """Show a Rich spinner with a dynamic activity label.

    The caller updates the message via status.update() as
    agent events arrive.
    """
    status = console.status("[bold blue]●[/] initializing", spinner="dots")
    status.start()
    try:
        yield status
    finally:
        status.stop()


def status_line(message: str) -> None:
    """Print a subtle status line."""
    console.print(f"  ● {message}", style="dim")
