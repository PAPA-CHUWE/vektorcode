"""Shared Rich console and color theme."""

from __future__ import annotations

from rich.console import Console
from rich.theme import Theme

VEKTOR_THEME = Theme(
    {
        "blue": "bold #273c88",
        "gold": "bold #cda716",
        "green": "bold #007d59",
        "red": "bold #c82828",
        "dim": "dim",
        "bold": "bold",
        "info": "#273c88",
        "success": "#007d59",
        "warning": "#cda716",
        "error": "#c82828",
        "tool": "#273c88",
        "file.path": "#cda716",
        "file.created": "#007d59",
        "file.modified": "#cda716",
        "file.read": "#273c88",
        "cmd": "#273c88",
        "prompt.user": "bold #273c88",
        "prompt.agent": "bold #273c88",
    }
)

console = Console(theme=VEKTOR_THEME)


class ThemeColors:
    BLUE = "#273c88"
    GOLD = "#cda716"
    GREEN = "#007d59"
    RED = "#c82828"
    CREAM = "#ffefca"
    DIM = "dim"
    BOLD = "bold"


theme = ThemeColors()
