"""Reversible checkpoints for file-modifying tool calls.

Every write snapshots the previous contents so a change can be undone with
one command. This is what makes it safe to let the agent try something
experimental: you can back out cheaply instead of defensively approving
every edit.

Deliberately not git. It works in a workspace with no repository, and
requires no understanding of git internals. It does record the git HEAD at
snapshot time, so a rollback that would contradict a commit the agent
already made can be flagged rather than silently applied.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Keeping every version of every file for a long session would grow without
# bound; this is a safety net for recent work, not version control.
MAX_CHECKPOINTS = 50


def _git_head(cwd: Path) -> str | None:
    """Current commit sha, or None outside a repository."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(cwd), capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


@dataclass
class Checkpoint:
    """One file's state immediately before a write."""

    path: str
    previous: str | None          # None when the file did not exist
    turn: int
    timestamp: str
    git_head: str | None = None

    @property
    def created_file(self) -> bool:
        """True if the write created the file, so undo means deleting it."""
        return self.previous is None

    def describe(self) -> str:
        action = "created" if self.created_file else "modified"
        return f"{self.path} ({action})"


@dataclass
class CheckpointStore:
    """Recent pre-write snapshots, newest last."""

    workspace: Path
    checkpoints: list[Checkpoint] = field(default_factory=list)
    turn: int = 0

    def begin_turn(self) -> None:
        self.turn += 1

    # ── recording ───────────────────────────────────────────────────
    def snapshot(self, path: str | Path) -> Checkpoint | None:
        """Capture a file's state before it is written.

        Never raises: failing to snapshot must not block the write, though
        it does mean that particular change cannot be undone.
        """
        target = Path(path)
        if not target.is_absolute():
            target = self.workspace / target

        try:
            existed = target.is_file()
            previous = target.read_text(encoding="utf-8") if existed else None
        except (OSError, UnicodeDecodeError):
            return None  # binary or unreadable: not safely reversible

        checkpoint = Checkpoint(
            path=str(path),
            previous=previous,
            turn=self.turn,
            timestamp=datetime.now(timezone.utc).isoformat(),
            git_head=_git_head(self.workspace),
        )
        self.checkpoints.append(checkpoint)
        if len(self.checkpoints) > MAX_CHECKPOINTS:
            self.checkpoints = self.checkpoints[-MAX_CHECKPOINTS:]
        return checkpoint

    # ── inspection ──────────────────────────────────────────────────
    def __len__(self) -> int:
        return len(self.checkpoints)

    def recent(self, count: int = 10) -> list[Checkpoint]:
        """Most recent first, for display."""
        return list(reversed(self.checkpoints[-count:]))

    def conflicts_with_git(self, count: int = 1) -> str | None:
        """Warn if undoing would contradict a commit made since the snapshot.

        Returns a message, or None when there is nothing to flag.
        """
        pending = self.checkpoints[-count:] if count > 0 else []
        if not pending:
            return None
        current = _git_head(self.workspace)
        if current is None:
            return None  # not a repository; nothing to contradict
        stale = [c for c in pending if c.git_head and c.git_head != current]
        if not stale:
            return None
        paths = ", ".join(sorted({c.path for c in stale}))
        return (
            f"A commit has been made since {paths} was changed. Undoing will "
            "revert the working tree but not the commit, leaving them out of "
            "step."
        )

    # ── reverting ───────────────────────────────────────────────────
    def undo(self, count: int = 1) -> tuple[list[str], list[str]]:
        """Revert the last `count` changes, newest first.

        Returns (reverted, failed) as human-readable descriptions.
        """
        reverted: list[str] = []
        failed: list[str] = []

        for _ in range(max(1, count)):
            if not self.checkpoints:
                break
            checkpoint = self.checkpoints.pop()
            target = Path(checkpoint.path)
            if not target.is_absolute():
                target = self.workspace / target

            try:
                if checkpoint.created_file:
                    if target.is_file():
                        target.unlink()
                    reverted.append(f"removed {checkpoint.path}")
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(checkpoint.previous or "", encoding="utf-8")
                    reverted.append(f"restored {checkpoint.path}")
            except OSError as exc:
                failed.append(f"{checkpoint.path}: {exc}")

        return reverted, failed


# One store per session, created by the agent.
_store: CheckpointStore | None = None


def get_store() -> CheckpointStore | None:
    """The active store, or None when checkpointing is not set up."""
    return _store


def set_store(store: CheckpointStore | None) -> None:
    """Install the session's store. The filesystem tool snapshots through it."""
    global _store
    _store = store
