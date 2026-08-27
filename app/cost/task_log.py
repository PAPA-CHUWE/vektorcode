"""Per-task audit log.

One JSON file per turn under logs/<date>/task-NNN.json, recording what the
agent touched, what it spent, and whether it failed.

All file I/O is best-effort: an audit log that crashes the agent loop is
worse than a missing audit log.
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

# Tool names whose path argument counts as "read".
_READ_TOOLS = {"read_file", "list_files", "search_code"}

# run_command invocations containing these look like test runs.
_TEST_MARKERS = ("pytest", "test")


class TaskLogger:
    """Accumulates what one turn did, then writes it to disk."""

    def __init__(self, logs_dir: Path | str) -> None:
        self.logs_dir = Path(logs_dir)
        self.started = time.monotonic()
        self.files_accessed: list[str] = []
        self.files_modified: list[str] = []
        self.tools_called: list[str] = []
        self.tests_executed: list[str] = []
        self.errors: list[str] = []

    # ── collection ──────────────────────────────────────────────────
    def record_tool_call(self, name: str, args: dict[str, Any], status: str) -> None:
        """Note one tool invocation and its outcome."""
        self.tools_called.append(name)

        path = args.get("path") if isinstance(args, dict) else None
        if name in _READ_TOOLS and path:
            self._add(self.files_accessed, str(path))
        elif name == "write_file" and path:
            self._add(self.files_modified, str(path))
        elif name == "run_command":
            command = str(args.get("command", "")) if isinstance(args, dict) else ""
            if any(marker in command.lower() for marker in _TEST_MARKERS):
                self._add(self.tests_executed, command)

        if status == "error":
            self.errors.append(f"{name} failed")

    @staticmethod
    def _add(target: list[str], value: str) -> None:
        """Append without duplicating."""
        if value not in target:
            target.append(value)

    # ── persistence ─────────────────────────────────────────────────
    def finalize(
        self,
        task: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        result: str,
        had_error: bool,
    ) -> Path | None:
        """Write the task log. Returns the path, or None if writing failed."""
        payload = {
            "task": task,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "estimated_cost_usd": round(cost_usd, 6),
            "files_accessed": list(self.files_accessed),
            "files_modified": list(self.files_modified),
            "tools_called": list(self.tools_called),
            "tests_executed": list(self.tests_executed),
            "result": result,
            "errors": list(self.errors),
            "had_error": had_error,
            "duration_seconds": round(time.monotonic() - self.started, 3),
        }

        try:
            day_dir = self.logs_dir / date.today().isoformat()
            day_dir.mkdir(parents=True, exist_ok=True)
            target = day_dir / f"task-{self._next_index(day_dir):03d}.json"
            target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            return target
        except OSError:
            # Auditing is never worth failing a turn over.
            return None

    @staticmethod
    def _next_index(day_dir: Path) -> int:
        """Next task number for the day, starting at 1."""
        highest = 0
        try:
            existing = day_dir.glob("task-*.json")
        except OSError:
            return 1
        for entry in existing:
            stem = entry.stem.removeprefix("task-")
            if stem.isdigit():
                highest = max(highest, int(stem))
        return highest + 1
