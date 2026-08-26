"""Compact startup banner."""

from __future__ import annotations

from rich.panel import Panel
from rich.text import Text

from app.ui.console import console


def show_banner(model: str = "", workspace: str = "") -> None:
    """Render the startup banner inside a rounded panel."""
    lines = Text()
    lines.append("\n")
    lines.append("                     VEKTORCODE\n", style="bold blue")
    lines.append("                   AI CODING AGENT\n\n", style="dim")
    if model:
        lines.append(f"              ● Model: {model}\n", style="blue")
    if workspace:
        lines.append(f"              ● Workspace: {workspace}\n", style="blue")
    lines.append("              ● Status: READY\n", style="green")
    lines.append("\n")
    lines.append("                 Type ", style="dim")
    lines.append("/help", style="bold gold")
    lines.append(" for commands\n", style="dim")
    lines.append("\n")

    console.print(
        Panel(
            lines,
            border_style="blue",
            padding=(0, 2),
            width=62,
        )
    )
