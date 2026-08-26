"""UI panels for tool calls, errors, changes, commands, and agent output."""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from rich.markdown import Markdown as RichMarkdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from app.ui.console import console


def show_tool_panel(tool: str, target: str = "") -> None:
    """Render a bordered tool panel."""
    header = Text(tool, style="tool bold")
    body = Text(target, style="file.path") if target else Text("")
    console.print(Panel(body, title=header, border_style="blue", width=60))


def show_tool_line(action: str, path: str, style: str = "file.path") -> None:
    """Render a compact tool action line like '→ Reading  src/auth/login.ts'."""
    arrow = Text("  → ", style="dim")
    label = Text(f"{action}  ", style="bold")
    target = Text(path, style=style)
    line = Text()
    line.append_text(arrow)
    line.append_text(label)
    line.append_text(target)
    console.print(line)


def show_error_panel(title: str, detail: str = "") -> None:
    """Render a red error panel."""
    header = Text(title, style="error bold")
    body = Text(detail, style="error") if detail else Text("")
    console.print(Panel(body, title=header, border_style="error", width=60))


def show_change_panel(path: str, old: str, new: str) -> None:
    """Render a compact diff for a code change."""
    header = Text("CHANGE", style="bold gold")
    target = Text(path, style="file.path")
    console.print(Panel(target, title=header, border_style="gold", width=60))

    old_lines = old.splitlines()
    new_lines = new.splitlines()
    diff = list(difflib.unified_diff(old_lines, new_lines, lineterm=""))
    for line in diff[:40]:
        if line.startswith("+++") or line.startswith("---"):
            continue
        style = "dim"
        if line.startswith("+"):
            style = "green"
        elif line.startswith("-"):
            style = "red"
        console.print(Text(f"  {line}", style=style))
    if len(diff) > 40:
        show_truncated(len(diff) - 40, "diff lines")


def show_command_panel(command: str) -> None:
    """Render a command execution panel."""
    header = Text("COMMAND", style="cmd bold")
    body = Text(command, style="bold")
    console.print(Panel(body, title=header, border_style="blue", width=60))


def show_confirmation(command: str) -> bool:
    """Ask user to confirm a destructive operation. Returns True if approved."""
    console.print()
    show_error_panel("⚠ Destructive Command", command)
    console.print("  This may discard uncommitted changes or data.\n", style="warning")
    try:
        answer = input("  Continue? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        console.print("\n  Aborted.", style="dim")
        return False
    return answer in {"y", "yes"}


def show_final_summary(
    files_changed: list[str] | None = None,
    tests_passed: int | None = None,
    tests_failed: int | None = None,
    summary: str = "",
    workspace: str = "",
) -> None:
    """Render a compact task completion summary."""
    lines = Text()
    lines.append("\n✓ Task completed\n\n", style="success bold")

    if files_changed:
        lines.append("Changed\n", style="bold")
        for f in files_changed:
            lines.append(f"  • {f}\n", style="file.path")
        lines.append("\n")

    if tests_passed is not None or tests_failed is not None:
        lines.append("Tests\n", style="bold")
        if tests_passed:
            lines.append(f"  ✓ {tests_passed} passed\n", style="success")
        if tests_failed:
            lines.append(f"  ✗ {tests_failed} failed\n", style="error")
        lines.append("\n")

    if summary:
        lines.append("Summary\n", style="bold")
        lines.append(f"  {summary}\n\n")

    if workspace:
        lines.append("Workspace\n", style="bold")
        lines.append(f"  {workspace}\n", style="dim")

    console.print(lines)


def show_plan(steps: list[str]) -> None:
    """Render a numbered plan."""
    lines = Text()
    lines.append("Plan\n\n", style="bold")
    for i, step in enumerate(steps, 1):
        lines.append(f"  {i}. {step}\n")
    lines.append("\n✓ Plan ready\n", style="success")
    console.print(lines)


def show_plan_update(steps: list[str], completed: set[int] | None = None) -> None:
    """Render plan progress with checkmarks."""
    completed = completed or set()
    lines = Text()
    lines.append("Plan\n\n", style="bold")
    for i, step in enumerate(steps, 1):
        if i in completed:
            lines.append(f"  ✓ {step}\n", style="success")
        elif i == max(completed) + 1 if completed else False:
            lines.append(f"  ● {step}\n", style="gold")
        else:
            lines.append(f"  ○ {step}\n", style="dim")
    console.print(lines)


def show_markdown(text: str) -> None:
    """Render markdown text with Rich."""
    console.print(RichMarkdown(text))


def show_code_block(code: str, language: str = "python") -> None:
    """Render a syntax-highlighted code block."""
    from rich.syntax import Syntax

    console.print(Syntax(code, language, theme="monokai", line_numbers=False))


def show_truncated(count: int, label: str = "lines") -> None:
    """Show a truncation notice."""
    console.print(f"\n  [output truncated — {count} {label}]", style="dim")


def show_context_bar(workspace: str, model: str, tool_count: int) -> None:
    """Render a compact context bar between interactions."""
    sep = "─" * 60
    console.print(sep, style="dim")
    console.print(
        f" workspace: {workspace}   model: {model}   tools: {tool_count}   ready",
        style="dim",
    )
    console.print(sep, style="dim")
