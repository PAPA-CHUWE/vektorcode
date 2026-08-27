"""Shared state describing the turn currently in flight.

The prompt's bottom toolbar reads this every keystroke, so it doubles as a
live status area while a turn runs. That matters because prompt_toolkit owns
the terminal during concurrent input — a Rich spinner would fight it for
cursor control, so the toolbar carries the "running" indication instead.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RunState:
    """What the agent is doing right now."""

    running: bool = False
    activity: str = ""
    steps: int = 0
    max_steps: int = 0
    queued: int = 0
    interrupted: bool = False

    def begin(self, max_steps: int) -> None:
        self.running = True
        self.activity = "thinking"
        self.steps = 0
        self.max_steps = max_steps
        self.interrupted = False

    def end(self) -> None:
        self.running = False
        self.activity = ""

    def tool_started(self, tool: str, target: str) -> None:
        self.steps += 1
        self.activity = f"{tool} {target}".strip()


# One turn runs at a time, so a module-level instance is sufficient.
run_state = RunState()
