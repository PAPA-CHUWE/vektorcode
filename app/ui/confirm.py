"""Selectable approval dialog.

Replaces the typed `[y/N]` prompt with options you move through with the
arrow keys, click with the mouse, or pick by shortcut key.

Runs as a nested prompt_toolkit Application via `run(in_thread=True)`, which
is what lets it start while the REPL's own prompt is suspended by
`run_in_terminal`. Falls back to a plain input() prompt when there is no
terminal to draw on, so piped and one-shot runs still behave.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style

_DIALOG_STYLE = Style.from_dict({
    "dialog.title": "bold #c82828",
    "dialog.detail": "#cda716",
    "dialog.caution": "#cda716",
    "option": "#888888",
    "option.selected": "bold #ffffff bg:#273c88",
    "option.key": "#666666",
})


@dataclass(frozen=True)
class Choice:
    """One selectable answer."""

    label: str
    value: str
    key: str  # single-character shortcut


# Approve-once is the safe default, so it is selected on open.
APPROVE = Choice("Yes, allow it", "approve", "y")
DECLINE = Choice("No, decline", "decline", "n")
ALWAYS = Choice("Yes, and stop asking about edits", "always", "a")


def _interactive() -> bool:
    """True only if there is a real terminal on both ends."""
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except (OSError, ValueError, AttributeError):
        return False


def _render(title: str, detail: str, caution: str, choices, selected: int):
    """Build the formatted text for the dialog body."""
    lines: list[tuple[str, str]] = [
        ("class:dialog.title", f"  ⚠ {title}\n"),
        ("class:dialog.detail", f"  {detail}\n"),
        ("class:dialog.caution", f"  {caution}\n\n"),
    ]
    for index, choice in enumerate(choices):
        style = "class:option.selected" if index == selected else "class:option"
        marker = "▸ " if index == selected else "  "
        lines.append((style, f"  {marker}{choice.label}  "))
        lines.append(("class:option.key", f"({choice.key})\n"))
    lines.append(("class:option.key", "\n  ↑↓ or click to choose · enter to confirm\n"))
    return FormattedText(lines)


def ask_choice(
    title: str,
    detail: str,
    caution: str,
    choices: list[Choice],
) -> str:
    """Show the dialog and return the chosen value.

    Returns the last choice's value (the safe one) if the dialog cannot be
    shown or the user aborts.
    """
    state = {"selected": 0}
    safe_default = choices[-1].value

    # Application.run() reads stdin even when stdout is redirected, so a
    # piped run would render the dialog and then block forever waiting for a
    # keypress nobody can send. Check both ends before drawing anything.
    if not _interactive():
        return _fallback(title, detail, choices, safe_default)

    def body():
        return _render(title, detail, caution, choices, state["selected"])

    kb = KeyBindings()

    @kb.add("up")
    @kb.add("s-tab")
    def _(event) -> None:
        state["selected"] = (state["selected"] - 1) % len(choices)

    @kb.add("down")
    @kb.add("tab")
    def _(event) -> None:
        state["selected"] = (state["selected"] + 1) % len(choices)

    @kb.add("enter")
    def _(event) -> None:
        event.app.exit(result=choices[state["selected"]].value)

    @kb.add("c-c")
    @kb.add("escape")
    def _(event) -> None:
        event.app.exit(result=safe_default)

    for index, choice in enumerate(choices):
        @kb.add(choice.key, eager=True)
        def _(event, _value=choice.value) -> None:
            event.app.exit(result=_value)

    control = FormattedTextControl(body, focusable=True, show_cursor=False)
    application: Application = Application(
        layout=Layout(HSplit([Window(control, height=len(choices) + 6)])),
        key_bindings=kb,
        style=_DIALOG_STYLE,
        full_screen=False,
        mouse_support=True,
    )

    try:
        # in_thread=True: the REPL's event loop is already running.
        result = application.run(in_thread=True)
    except Exception:
        return _fallback(title, detail, choices, safe_default)
    return result if result is not None else safe_default


def _fallback(title: str, detail: str, choices: list[Choice], safe_default: str) -> str:
    """Typed prompt for terminals the dialog cannot draw on."""
    keys = "/".join(c.key for c in choices)
    try:
        if not sys.stdin.isatty():
            return safe_default
        answer = input(f"  ⚠ {title}: {detail} [{keys}] ").strip().lower()
    except (EOFError, KeyboardInterrupt, OSError, ValueError):
        return safe_default
    for choice in choices:
        if answer == choice.key or answer == choice.value:
            return choice.value
    return safe_default
