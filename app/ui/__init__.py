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
    review_plan,
    MAX_PLAN_ROUNDS,
    show_markdown,
    show_code_block,
    show_truncated,
    show_context_bar,
)
from app.ui.progress import spinner, status_line, activity_spinner
from app.ui.diff import format_diff
from app.ui.input import VektorInput, InputResult, PasteInfo, analyze_paste
from app.ui.completer import VektorCompleter
from app.ui.activity import ActivityEngine, Activity, ActivityState

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
    "review_plan",
    "MAX_PLAN_ROUNDS",
    "show_markdown",
    "show_code_block",
    "show_truncated",
    "show_context_bar",
    "spinner",
    "activity_spinner",
    "status_line",
    "format_diff",
    "VektorInput",
    "InputResult",
    "PasteInfo",
    "analyze_paste",
    "VektorCompleter",
    "ActivityEngine",
    "Activity",
    "ActivityState",
]
