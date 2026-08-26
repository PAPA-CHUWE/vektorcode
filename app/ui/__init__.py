"""VektorCode presentation layer — Rich-based terminal UI."""

from __future__ import annotations

from app.ui.console import console, theme
from app.ui.banner import show_banner
from app.ui.panels import (
    show_tool_panel,
    show_tool_line,
    show_error_panel,
    show_change_panel,
    show_command_panel,
    show_confirmation,
    show_final_summary,
    show_plan,
    show_plan_update,
    show_markdown,
    show_code_block,
    show_truncated,
    show_context_bar,
)
from app.ui.progress import spinner, status_line
from app.ui.diff import format_diff

__all__ = [
    "console",
    "theme",
    "show_banner",
    "show_tool_panel",
    "show_tool_line",
    "show_error_panel",
    "show_change_panel",
    "show_command_panel",
    "show_confirmation",
    "show_final_summary",
    "show_plan",
    "show_plan_update",
    "show_markdown",
    "show_code_block",
    "show_truncated",
    "show_context_bar",
    "spinner",
    "status_line",
    "format_diff",
]
