"""Telling a mid-turn correction apart from a queued follow-up.

Queueing and steering are different things. "also update the README" can
wait until the current work finishes. "wait, don't touch that file" cannot —
by the time the turn ends the damage is done.

The distinction is drawn with a heuristic rather than a model call. A
classification call between every pair of tool calls would add latency to
exactly the moment that must stay responsive, and the cost of getting it
wrong is small in both directions: a missed correction still runs as the
next turn, and a false positive only means the agent reads a follow-up
slightly early.
"""

from __future__ import annotations

import re

# Openers that read as "stop what you are doing".
_INTERRUPTS = (
    "wait", "stop", "hold on", "hold up", "actually", "scratch that",
    "never mind", "nevermind", "cancel", "abort", "no,", "no -", "no—",
    "don't", "dont", "do not", "instead", "forget",
)

# Corrective phrasings that can appear anywhere in the message.
_CORRECTIONS = (
    "not that", "wrong file", "wrong one", "that's wrong", "thats wrong",
    "use .* not ", "rather than", "instead of", "leave .* alone",
    "revert", "undo that", "back out", "stop touching", "don't touch",
    "dont touch", "do not touch", "skip that", "not the",
)

_CORRECTION_RE = re.compile("|".join(_CORRECTIONS), re.IGNORECASE)

# "stop after you finish" is a note about the end state, not an interrupt.
_DEFERRED = (
    "after you", "when you're done", "when you are done", "once you",
    "afterwards", "at the end", "when finished", "then also", "and also",
)


def is_steering(message: str) -> bool:
    """True if a queued message should redirect the turn already running."""
    text = (message or "").strip().lower()
    if not text:
        return False

    # A deferral wins outright: it explicitly describes later work.
    if any(marker in text for marker in _DEFERRED):
        return False

    first = text.lstrip("\"'([ ")
    if any(first.startswith(opener) for opener in _INTERRUPTS):
        return True

    return bool(_CORRECTION_RE.search(text))


def as_injection(message: str) -> str:
    """Phrase a steering message for injection before the next tool call."""
    return (
        f"The user just said: {message.strip()}\n"
        "Adjust your approach accordingly before your next action. If this "
        "contradicts what you were about to do, do the new thing instead and "
        "say briefly what you changed."
    )
