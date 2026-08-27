"""Live tool-execution feed.

Renders each tool call as it happens instead of only reporting a final
count. A running step shows a spinner; on completion it is replaced by a
permanent line recording what ran and how long it took:

    ✓ list_files   .                        0.02s
    ✓ read_file    app/main.py              0.05s
    ⚙ search_code  "def main"

Purely presentational — the feed observes AgentEvents and never drives
tool execution.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from rich.text import Text

from app.ui.console import console

# Column width for the tool name, so targets line up down the feed.
_NAME_WIDTH = 13

# Targets longer than this are middle-truncated to keep one step per line.
_TARGET_WIDTH = 44


def _shorten(text: str, width: int = _TARGET_WIDTH) -> str:
    """Middle-truncate so both the start and the filename stay readable."""
    if len(text) <= width:
        return text
    keep = width - 1
    head = keep // 2
    tail = keep - head
    return f"{text[:head]}…{text[-tail:]}"


@dataclass
class ToolStep:
    """One tool invocation observed by the feed."""

    tool: str
    target: str
    started: float
    ok: bool | None = None
    detail: str = ""
    elapsed: float = 0.0


@dataclass
class ToolFeed:
    """Renders tool steps live, then leaves them on screen.

    Completed steps are printed above the spinner, so the terminal keeps a
    scrollable record of the whole chain rather than a single counter.
    """

    steps: list[ToolStep] = field(default_factory=list)
    _status: object | None = None
    _active: ToolStep | None = None

    # ── lifecycle ───────────────────────────────────────────────────
    def attach(self, status: object) -> None:
        """Bind the spinner this feed updates while a tool is running."""
        self._status = status

    def _set_label(self, text: str) -> None:
        if self._status is not None and hasattr(self._status, "update"):
            self._status.update(text)

    # ── events ──────────────────────────────────────────────────────
    def start(self, tool: str, target: str) -> None:
        """A tool began executing."""
        self._active = ToolStep(tool=tool, target=target, started=time.monotonic())
        self.steps.append(self._active)
        label = f"[bold blue]⚙[/] running [tool]{tool}[/]"
        if target:
            label += f" [dim]{_shorten(target)}[/]"
        self._set_label(label)

    def finish(self, ok: bool, detail: str = "") -> None:
        """The active tool completed. Prints a permanent result line."""
        step = self._active
        if step is None:
            return
        step.ok = ok
        step.detail = detail
        step.elapsed = time.monotonic() - step.started
        self._active = None
        console.print(self.render_step(step))

    # ── rendering ───────────────────────────────────────────────────
    def render_step(self, step: ToolStep) -> Text:
        """One completed step: mark, tool, target, and timing."""
        line = Text("  ")
        if step.ok:
            line.append("✓ ", style="success")
        else:
            line.append("✗ ", style="error")

        line.append(f"{step.tool:<{_NAME_WIDTH}}", style="tool")

        if step.ok:
            if step.target:
                line.append(_shorten(step.target), style="file.path")
            line.append(f"  {step.elapsed:.2f}s", style="dim")
        else:
            reason = step.detail.strip().splitlines()[0] if step.detail.strip() else "failed"
            line.append(_shorten(reason, 52), style="error")

        return line

    def summary(self) -> Text | None:
        """A one-line tally of the chain, or None if no tools ran."""
        if not self.steps:
            return None
        failed = sum(1 for s in self.steps if s.ok is False)
        total_time = sum(s.elapsed for s in self.steps)
        line = Text("  ")
        line.append(f"{len(self.steps)} tool{'s' if len(self.steps) != 1 else ''}", style="dim")
        if failed:
            line.append(f" · {failed} failed", style="error")
        line.append(f" · {total_time:.2f}s", style="dim")
        return line
