"""Daily spend budget.

Today's spend is rebuilt from the persisted task logs on startup, so
restarting the CLI part-way through a day does not hand the agent a fresh
budget it has already used.
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

DEFAULT_DAILY_BUDGET_USD = float(os.getenv("VEKTOR_DAILY_BUDGET_USD", "1.00"))


class BudgetTracker:
    """Tracks USD spent today against a daily ceiling."""

    def __init__(self, daily_budget_usd: float | None = None) -> None:
        self.daily_budget_usd = (
            DEFAULT_DAILY_BUDGET_USD if daily_budget_usd is None else daily_budget_usd
        )
        self.spent_today_usd: float = 0.0
        self._day: date = date.today()

    # ── day handling ────────────────────────────────────────────────
    def _roll_over(self) -> None:
        """Reset spend when the calendar day changes."""
        today = date.today()
        if today != self._day:
            self._day = today
            self.spent_today_usd = 0.0

    # ── accounting ──────────────────────────────────────────────────
    def record(self, cost_usd: float) -> float:
        """Add the cost of one call. Returns the new daily total."""
        self._roll_over()
        if cost_usd > 0:
            self.spent_today_usd += cost_usd
        return self.spent_today_usd

    def would_exceed(self, projected_cost_usd: float = 0.0) -> bool:
        """True if spending this much more would breach the budget.

        Uses >= deliberately. Called with 0.0 as a pre-flight "is there any
        room left?" check, so spend landing exactly on the ceiling must block
        the next call rather than waiting to go strictly over.
        """
        self._roll_over()
        if self.daily_budget_usd <= 0:
            return False
        return self.spent_today_usd + projected_cost_usd >= self.daily_budget_usd

    @property
    def remaining_usd(self) -> float:
        """Budget left today, never negative."""
        self._roll_over()
        return max(0.0, self.daily_budget_usd - self.spent_today_usd)

    # ── persistence ─────────────────────────────────────────────────
    def load_today(self, logs_dir: Path | str) -> float:
        """Rebuild today's spend from persisted task logs.

        Never raises: a missing, unreadable, or malformed log directory just
        means we start from zero rather than crashing the CLI.
        """
        self._roll_over()
        day_dir = Path(logs_dir) / date.today().isoformat()
        total = 0.0
        try:
            entries = sorted(day_dir.glob("task-*.json"))
        except OSError:
            return self.spent_today_usd

        for entry in entries:
            try:
                payload = json.loads(entry.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            cost = payload.get("estimated_cost_usd", 0.0)
            if isinstance(cost, (int, float)):
                total += float(cost)

        self.spent_today_usd = total
        return self.spent_today_usd
