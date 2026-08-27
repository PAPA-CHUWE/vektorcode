"""FIFO queue for messages typed while a turn is still running.

The REPL reads input concurrently with turn execution. Anything submitted
mid-turn lands here instead of being dropped or interleaved, and is promoted
automatically when the turn finishes.
"""

from __future__ import annotations

from collections import deque

from rich.text import Text

from app.ui.console import console


class MessageQueue:
    """Pending user messages, oldest first."""

    def __init__(self) -> None:
        self._items: deque[str] = deque()
        # True while a turn is executing; the drain guard.
        self.busy: bool = False

    def __len__(self) -> int:
        return len(self._items)

    def __bool__(self) -> bool:
        return bool(self._items)

    def items(self) -> list[str]:
        """Pending messages, oldest first."""
        return list(self._items)

    def submit(self, message: str) -> int:
        """Queue a message and return its 0-based position at submission.

        0 means it runs next once the current turn, if any, finishes.
        Returns -1 for blank input, which is not queued.
        """
        text = message.strip()
        if not text:
            return -1
        self._items.append(text)
        return len(self._items) - 1

    def push(self, message: str) -> None:
        """Queue a message, ignoring its position."""
        self.submit(message)

    def pop_next(self) -> str | None:
        """Alias of pop(), for callers that read as a state machine."""
        return self.pop()

    def pop(self) -> str | None:
        """Take the next message, or None if empty."""
        return self._items.popleft() if self._items else None

    def take_matching(self, predicate) -> str | None:
        """Remove and return the oldest message satisfying predicate.

        Deliberately out of FIFO order: a correction is time-sensitive and a
        plain follow-up is not, so pulling "wait, wrong file" past "also
        update the README" is the behaviour you want.
        """
        for index, message in enumerate(self._items):
            if predicate(message):
                del self._items[index]
                return message
        return None

    def pop_last(self) -> str | None:
        """Take the most recently queued message back off the end.

        Used to recall a queued item into the input line.
        """
        return self._items.pop() if self._items else None

    def clear(self) -> int:
        """Drop everything. Returns how many were discarded."""
        count = len(self._items)
        self._items.clear()
        return count

    # ── rendering ───────────────────────────────────────────────────
    def show_queued(self, message: str, position: int | None = None) -> None:
        """Confirm a message was queued rather than sent, and where."""
        label = "  ⏳ queued: " if position is None else f"  ⏳ queued (position {position + 1}): "
        line = Text(label, style="dim")
        line.append(_one_line(message), style="dim")
        console.print(line)

    def show_pending(self) -> None:
        """List everything still waiting, for /queue."""
        if not self._items:
            console.print("\n  Nothing queued.\n", style="dim")
            return
        console.print()
        console.print(f"  {len(self._items)} queued", style="bold")
        for index, message in enumerate(self._items, 1):
            line = Text(f"  {index}. ", style="dim")
            line.append(_one_line(message), style="dim")
            console.print(line)
        console.print()


def _one_line(message: str, width: int = 68) -> str:
    """Collapse a message to a single line for compact display."""
    flat = " ".join(message.split())
    return flat if len(flat) <= width else flat[: width - 1] + "…"
