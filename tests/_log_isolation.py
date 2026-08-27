"""Keep test runs from leaving synthetic task logs in the real repo.

Deleting the whole logs/<date>/ directory would destroy genuine audit logs
from real usage on the same day, so only files that appear during the run
are removed.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

_WORKSPACE = Path(__file__).resolve().parent.parent


def _day_dir() -> Path:
    return _WORKSPACE / "logs" / date.today().isoformat()


def snapshot() -> set[Path]:
    """Task logs that already existed before the run."""
    day = _day_dir()
    return set(day.glob("task-*.json")) if day.exists() else set()


def restore(before: set[Path]) -> int:
    """Delete only task logs created since the snapshot.

    Returns how many were removed. Also removes the day directory if the
    run created it and it is now empty.
    """
    day = _day_dir()
    if not day.exists():
        return 0

    removed = 0
    for entry in day.glob("task-*.json"):
        if entry not in before:
            try:
                entry.unlink()
                removed += 1
            except OSError:
                pass

    if not before:
        try:
            next(day.iterdir())
        except StopIteration:
            day.rmdir()
        except OSError:
            pass
    return removed
