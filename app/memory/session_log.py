"""Automatic per-turn session log.

Records every turn and offers a condensed recap of the last few for prompt
context. This is a summary layer, not transcript replay — injecting raw
history would defeat the point by bloating the very context it is meant to
economise.

Distinct from a deliberate memory tool: this is always-on and automatic,
capturing continuity. A memory tool would be the agent choosing to store a
durable fact. Both can coexist.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Turns included in the recap. Enough for continuity, small enough that the
# injection stays cheap — measured in the tests.
DEFAULT_RECAP_TURNS = 5

# Per-field caps so one enormous turn cannot dominate the recap.
_MESSAGE_CAP = 90
_RESPONSE_CAP = 110


def _condense(text: str, limit: int) -> str:
    """Collapse to a single line, truncated on a word boundary."""
    flat = " ".join((text or "").split())
    if len(flat) <= limit:
        return flat
    cut = flat[:limit]
    spaced = cut.rsplit(" ", 1)[0]
    return (spaced if len(spaced) > limit * 0.6 else cut) + "…"


@dataclass
class TurnRecord:
    """One completed turn."""

    turn_number: int
    task_type: str
    user_message: str
    response_summary: str
    timestamp: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class SessionLog:
    """Turns for the current session, in memory by default."""

    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    persist: bool = False
    persist_dir: Path | None = None
    turns: list[TurnRecord] = field(default_factory=list)

    @classmethod
    def from_env(cls, workspace: Path | None = None) -> "SessionLog":
        """Build from VEKTOR_PERSIST_SESSION; in-memory unless it is set."""
        enabled = os.getenv("VEKTOR_PERSIST_SESSION", "").strip().lower() in (
            "1", "true", "yes", "on",
        )
        base = Path(workspace) / ".vektorcode" if workspace else None
        return cls(persist=enabled, persist_dir=base)

    # ── recording ───────────────────────────────────────────────────
    def record(self, task_type: str, user_message: str, response_summary: str) -> TurnRecord:
        """Append a turn. Never raises — logging must not break a turn."""
        record = TurnRecord(
            turn_number=len(self.turns) + 1,
            task_type=str(task_type),
            user_message=_condense(user_message, _MESSAGE_CAP),
            response_summary=_condense(response_summary, _RESPONSE_CAP),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self.turns.append(record)
        if self.persist:
            self._append_to_disk(record)
        return record

    def _append_to_disk(self, record: TurnRecord) -> None:
        if self.persist_dir is None:
            return
        try:
            self.persist_dir.mkdir(parents=True, exist_ok=True)
            target = self.persist_dir / f"session_{self.session_id}.jsonl"
            with open(target, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record.to_dict()) + "\n")
        except OSError:
            # A session log that breaks the agent is worse than no log.
            pass

    # ── retrieval ───────────────────────────────────────────────────
    def recent(self, count: int = DEFAULT_RECAP_TURNS) -> list[TurnRecord]:
        return self.turns[-count:] if count > 0 else []

    def recap(self, count: int = DEFAULT_RECAP_TURNS) -> str:
        """A condensed recap for prompt injection, or "" when empty."""
        recent = self.recent(count)
        if not recent:
            return ""
        lines = ["Earlier in this session:"]
        for record in recent:
            lines.append(
                f"{record.turn_number}. [{record.task_type}] "
                f"{record.user_message} → {record.response_summary}"
            )
        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self.turns)
