"""Slash-command completer for VektorCode's interactive prompt.

Provides real-time command suggestions via prompt_toolkit when the user
types a `/` at the start of input. Filters as the user types, supports
keyboard navigation, and integrates seamlessly with the normal prompt.
"""

from __future__ import annotations

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document

from app.commands import COMMAND_registry


class VektorCompleter(Completer):
    """Slash-command completer for the interactive prompt.

    Activates only when the current word starts with ``/``.
    Filters commands by prefix as the user types and returns
    styled completions for prompt_toolkit's completion menu.
    """

    def get_completions(
        self, document: Document, complete_event
    ) -> list[Completion]:
        word = document.get_word_before_cursor(WORD=True)
        if not word.startswith("/"):
            return []

        typed = word
        completions: list[Completion] = []

        for cmd in COMMAND_registry:
            if cmd.name.startswith(typed):
                display = f"  {cmd.name:<14}{cmd.description}"
                completions.append(
                    Completion(
                        text=cmd.name,
                        start_position=-len(word),
                        display=display,
                    )
                )

        return completions
