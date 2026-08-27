"""UI panels for tool calls, errors, changes, commands, and agent output."""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from rich.markdown import Markdown as RichMarkdown
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from app.ui.console import console


def show_tool_panel(tool: str, target: str = "") -> None:
    """Render a bordered tool panel."""
    header = Text(tool, style="blue")
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


_last_traceback: str = ""


def record_traceback(text: str) -> None:
    """Stash a traceback so /trace can show it on request."""
    global _last_traceback
    _last_traceback = text


def last_traceback() -> str:
    """The most recent stashed traceback, or an empty string."""
    return _last_traceback


def show_error(
    error_type: str,
    summary: str,
    traceback_text: str = "",
    hint: str = "",
) -> None:
    """Render an error as type + one-line summary, traceback collapsed.

    Full stack traces bury the useful line, so the traceback is stored and
    surfaced only when the user asks for it with /trace.
    """
    record_traceback(traceback_text)

    body = Text()
    body.append(summary.strip() or "unknown error")
    if hint:
        body.append(f"\n\n{hint}", style="dim")
    # Only advertise /trace when it would actually reveal something the
    # panel is not already showing.
    extra = [
        line for line in traceback_text.strip().splitlines()
        if line.strip() and line.strip() != summary.strip()
    ]
    if extra:
        noun = "line" if len(extra) == 1 else "lines"
        body.append(f"\n\n{len(extra)} more {noun} — /trace to expand", style="dim")

    console.print()
    console.print(
        Panel(
            body,
            title=Text(error_type, style="red"),
            title_align="left",
            border_style="red",
            width=min(76, console.width),
        )
    )
    console.print()


def show_traceback(text: str) -> None:
    """Print a previously stashed traceback in full."""
    if not text.strip():
        console.print("\n  No traceback recorded.\n", style="dim")
        return
    console.print()
    console.print(
        Panel(
            Text(text.rstrip(), style="dim"),
            title=Text("Traceback", style="red"),
            title_align="left",
            border_style="red",
        )
    )
    console.print()


def show_error_panel(title: str, detail: str = "") -> None:
    """Render a red error panel."""
    header = Text(title, style="bold #c82828")
    body = Text(detail, style="#c82828") if detail else Text("")
    console.print(Panel(body, title=header, border_style="#c82828", width=60))


def show_change_panel(path: str, old: str, new: str, max_lines: int = 40) -> None:
    """Render a unified diff for a file change.

    Additions green, deletions red, hunk headers blue, context dimmed —
    the convention every other modern CLI agent uses.
    """
    diff = list(
        difflib.unified_diff(
            old.splitlines(),
            new.splitlines(),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
        )
    )

    if not diff:
        console.print(f"  = {path} (no changes)", style="dim")
        return

    added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))

    body = Text()
    shown = 0
    for line in diff:
        if line.startswith("+++") or line.startswith("---"):
            continue
        if shown >= max_lines:
            break
        if line.startswith("@@"):
            style = "blue"
        elif line.startswith("+"):
            style = "success"
        elif line.startswith("-"):
            style = "error"
        else:
            style = "dim"
        body.append(f"{line}\n", style=style)
        shown += 1

    header = Text()
    header.append(path, style="file.path")
    header.append("  ")
    header.append(f"+{added}", style="success")
    header.append(" ")
    header.append(f"-{removed}", style="error")

    console.print()
    console.print(
        Panel(
            body,
            title=header,
            title_align="left",
            border_style="gold",
            width=min(88, console.width),
        )
    )
    if shown >= max_lines and len(diff) > shown:
        show_truncated(len(diff) - shown, "diff lines")
    console.print()


def show_command_panel(command: str) -> None:
    """Render a command execution panel."""
    header = Text("COMMAND", style="blue")
    body = Text(command, style="bold")
    console.print(Panel(body, title=header, border_style="blue", width=60))


_CONFIRM_TITLES = {
    "destructive": ("⚠ Destructive Command", "This may discard uncommitted changes or data."),
    "shell": ("⚠ Shell Command", "This will run a command in your workspace."),
    "write": ("⚠ File Write", "This will create or overwrite a file."),
}


def show_confirmation(detail: str, kind: str = "destructive") -> bool:
    """Ask the user to approve a privileged action. True if approved.

    Installed as the permission layer's approver, so this is the only path
    by which a write, shell, or destructive action can be allowed.
    """
    from app.permissions.mode import PermissionMode, get_mode, set_mode
    from app.ui.confirm import ALWAYS, APPROVE, DECLINE, ask_choice

    title, caution = _CONFIRM_TITLES.get(kind, _CONFIRM_TITLES["destructive"])

    choices = [APPROVE]
    # Only offer "stop asking" where a mode exists that actually stops asking.
    if kind == "write" and get_mode() is PermissionMode.DEFAULT:
        choices.append(ALWAYS)
    choices.append(DECLINE)

    console.print()
    try:
        console.file.flush()
    except (OSError, ValueError, AttributeError):
        pass

    answer = ask_choice(title, _one_line_detail(detail, 64), caution, choices)

    if answer == "always":
        set_mode(PermissionMode.AUTO_EDIT)
        console.print("  ✓ approved — switched to auto-edit mode", style="success")
        return True

    approved = answer == "approve"
    console.print(
        "  ✓ approved" if approved else "  ✗ declined",
        style="success" if approved else "error",
    )
    return approved


def _one_line_detail(detail: str, width: int = 52) -> str:
    """Collapse the action detail to a single readable fragment."""
    flat = " ".join(detail.split())
    return flat if len(flat) <= width else "…" + flat[-(width - 1):]


def show_final_summary(
    files_changed: list[str] | None = None,
    tests_passed: int | None = None,
    tests_failed: int | None = None,
    summary: str = "",
    workspace: str = "",
) -> None:
    """Render a compact task completion summary."""
    lines = Text()
    lines.append("\n✓ Task completed\n\n", style="green")

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
    console.print(f"\n  [output truncated — {count} {label}]", style="dim", markup=False)


# Token counts are noise during normal use; /tokens toggles them.
_show_tokens = False


def set_token_display(enabled: bool) -> bool:
    """Enable or disable token counts in the status bar. Returns new state."""
    global _show_tokens
    _show_tokens = enabled
    return _show_tokens


def token_display_enabled() -> bool:
    """True if the status bar is currently showing token counts."""
    return _show_tokens


# Riskier modes read louder, so the mode is never ambiguous.
_MODE_STYLES = {
    "default": "success",
    "auto-edit": "warning",
    "full-auto": "error",
}

_STATUS_DOTS = {
    "ready": "success",
    "running": "warning",
    "error": "error",
}


def show_context_bar(
    workspace: str,
    model: str,
    tool_count: int,
    input_tokens: int = 0,
    output_tokens: int = 0,
    total_tokens: int = 0,
    status: str = "ready",
    mode: str = "",
    budget_remaining_usd: float | None = None,
) -> None:
    """Render the footer status bar.

    Weight carries the hierarchy: the workspace is what changes between
    sessions and is bolded; the model is effectively static and is dimmed.
    A coloured dot replaces the word "ready". Token counts are right-aligned
    and hidden unless /tokens turns them on.
    """
    dot_style = _STATUS_DOTS.get(status, "success")

    left = Text()
    left.append("● ", style=dot_style)
    left.append(workspace, style="bold")
    left.append("   ")
    left.append(model or "no model", style="dim")
    left.append(f"  ·  {tool_count} tools", style="dim")
    if mode:
        left.append("  ·  ", style="dim")
        left.append(mode, style=_MODE_STYLES.get(mode, "dim"))

    # Right-hand segments, least important last: if the terminal is too
    # narrow they are dropped from the end rather than wrapping the bar.
    segments: list[str] = []
    if _show_tokens and total_tokens > 0:
        # Opt-in via /tokens, so it outranks the always-on budget readout.
        segments.append(f"↑{input_tokens:,} ↓{output_tokens:,} Σ{total_tokens:,}")
    if budget_remaining_usd is not None:
        segments.append(f"budget ${budget_remaining_usd:.4f}")

    gap = "   "
    while segments:
        right_text = gap.join(segments)
        if len(left.plain) + len(right_text) + 2 <= console.width:
            break
        segments.pop()
    else:
        right_text = ""

    right = Text(right_text, style="dim")
    pad = max(2, console.width - len(left.plain) - len(right.plain))
    bar = Text()
    bar.append_text(left)
    bar.append(" " * pad)
    bar.append_text(right)

    console.print(Rule(style="dim"))
    console.print(bar)


def show_tools_table(grouped: dict[str, list[tuple[str, str]]], total: int) -> None:
    """Render the tool inventory grouped by category.

    A table beats the previous indented text dump: the name column is a
    fixed width, so the eye can scan straight down it.
    """
    table = Table(
        show_header=True,
        header_style="bold",
        box=None,
        padding=(0, 2),
        title=f"Available tools · {total}",
        title_style="bold blue",
        title_justify="left",
    )
    table.add_column("TOOL", style="tool", no_wrap=True)
    table.add_column("DESCRIPTION", style="dim")

    for index, (category, tools) in enumerate(grouped.items()):
        if index:
            table.add_row("", "")
        table.add_row(Text(category.upper(), style="gold"), "")
        for name, description in tools:
            table.add_row(f"  {name}", description)

    console.print()
    console.print(table)
    console.print()


def show_commands_table(rows: list[tuple[str, str]]) -> None:
    """Render slash commands as a two-column table."""
    table = Table(
        show_header=True,
        header_style="bold",
        box=None,
        padding=(0, 2),
        title="Commands",
        title_style="bold blue",
        title_justify="left",
    )
    table.add_column("COMMAND", style="tool", no_wrap=True)
    table.add_column("DESCRIPTION", style="dim")
    for name, description in rows:
        table.add_row(name, description)

    console.print()
    console.print(table)
    console.print()


def show_status_table(rows: list[tuple[str, str]]) -> None:
    """Render key/value session status."""
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="dim", no_wrap=True)
    table.add_column(style="bold")
    for key, value in rows:
        table.add_row(key, value)
    console.print()
    console.print(table)
    console.print()


# An unbounded plan-editing loop is itself a steerability failure.
MAX_PLAN_ROUNDS = 3


def review_plan(steps: list[str], round_number: int = 1) -> tuple[str, str]:
    """Show a plan and ask what to do with it.

    Returns (decision, text) where decision is "approve", "edit" or
    "reject". For "edit", text is the user's natural-language change.
    """
    from app.ui.confirm import Choice, ask_choice

    show_plan(steps)
    if round_number > 1:
        console.print(f"  revision {round_number} of {MAX_PLAN_ROUNDS}", style="dim")

    decision = ask_choice(
        "Proceed with this plan?",
        f"{len(steps)} step{'s' if len(steps) != 1 else ''}",
        "You can change it before anything runs.",
        [
            Choice("Yes, go ahead", "approve", "y"),
            Choice("Change something", "edit", "e"),
            Choice("No, let me restate it", "reject", "n"),
        ],
    )

    if decision != "edit":
        return decision, ""

    try:
        console.file.flush()
    except (OSError, ValueError, AttributeError):
        pass
    try:
        edit = input("  What should change? ").strip()
    except (EOFError, KeyboardInterrupt, OSError, ValueError):
        return "reject", ""
    return ("edit", edit) if edit else ("approve", "")
