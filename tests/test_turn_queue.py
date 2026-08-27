"""Unit tests for the pending-prompt queue.

Plain synchronous state machine — no asyncio, no prompt_toolkit — so the
submit/drain behaviour is testable without a terminal or an event loop.

Run directly: python3 tests/test_turn_queue.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ui.queue import MessageQueue as TurnQueue

_passed, _failed = 0, 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS: {label}")
    else:
        _failed += 1
        print(f"  FAIL: {label}")


def test_submit_returns_queue_position():
    q = TurnQueue()
    check(q.submit("first") == 0, "first submission is position 0")
    check(q.submit("second") == 1, "second submission is position 1")
    check(q.submit("third") == 2, "third submission is position 2")


def test_pop_next_returns_fifo_order():
    q = TurnQueue()
    q.submit("a")
    q.submit("b")
    q.submit("c")
    check(q.pop_next() == "a", "pops 'a' first")
    check(q.pop_next() == "b", "pops 'b' second")
    check(q.pop_next() == "c", "pops 'c' third")
    check(q.pop_next() is None, "empty queue pops None")


def test_len_reflects_pending_count():
    q = TurnQueue()
    check(len(q) == 0, "new queue has length 0")
    q.submit("x")
    check(len(q) == 1, "length 1 after one submission")
    q.pop_next()
    check(len(q) == 0, "length 0 after popping it")


def test_blank_submissions_are_rejected():
    q = TurnQueue()
    check(q.submit("   ") == -1, "whitespace-only submission returns -1")
    check(q.submit("") == -1, "empty submission returns -1")
    check(len(q) == 0, "blank submissions do not enter the queue")
    check(q.submit(" real ") == 0, "surrounding whitespace is stripped")
    check(q.pop_next() == "real", "stored text is stripped")


def test_busy_flag_defaults_false():
    q = TurnQueue()
    check(q.busy is False, "queue starts idle")
    q.busy = True
    check(q.busy is True, "busy flag is settable by the drain loop")


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        fn()
    print(f"\n{'=' * 40}\nResults: {_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
