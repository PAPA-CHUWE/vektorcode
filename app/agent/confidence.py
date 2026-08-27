"""Confidence gating for ambiguous natural-language requests.

When the model is unsure what was asked, one clarifying question beats a
confident guess. This applies only to open-ended requests — a git or
filesystem call either succeeds or returns a real exit code, and gating
those on a soft signal would add friction with nothing to show for it.

False-positive gating is as damaging as none: an agent that asks "what do
you mean?" about a clear request is worse than one that occasionally
guesses. The threshold is deliberately low, and clear requests are expected
to sail past it.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

# Below this, ask rather than guess. Configurable so it can be tuned per
# deployment without a code change.
DEFAULT_CONFIDENCE_THRESHOLD = 0.6

# At or above this, a needs_clarification flag is disregarded: the model
# cannot be both this sure and unable to proceed.
CONFIDENT_ENOUGH = 0.85


def confidence_threshold() -> float:
    """Threshold from CONFIDENCE_THRESHOLD, or the default."""
    raw = os.getenv("CONFIDENCE_THRESHOLD", "").strip()
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_CONFIDENCE_THRESHOLD
    return value if 0.0 <= value <= 1.0 else DEFAULT_CONFIDENCE_THRESHOLD


CONFIDENCE_INSTRUCTION = """\
Only ask for clarification when no amount of looking would resolve the
ambiguity. If you could find the answer with list_files, read_file or
search_code, use the tool instead of asking. "This project" means the
workspace you are running in — go and look at it.

If the request is genuinely ambiguous in a way tools cannot settle, say so
instead of guessing. Prepend exactly one line:

    {"confidence": <0.0-1.0>, "needs_clarification": <true|false>, "question": "<what you need to know>"}

Then your answer on the following lines. Omit the line entirely when the
request is clear.
"""

_SIGNAL_RE = re.compile(r'\{[^{}]*"confidence"\s*:[^{}]*\}', re.DOTALL)


@dataclass(frozen=True)
class ConfidenceSignal:
    """A self-reported confidence reading extracted from a reply."""

    confidence: float
    needs_clarification: bool
    question: str
    body: str  # the reply with the signal line removed

    def should_clarify(self, threshold: float | None = None) -> bool:
        limit = confidence_threshold() if threshold is None else threshold
        if not self.question.strip():
            # Nothing coherent to ask; guessing beats an empty question.
            return False
        if self.confidence < limit:
            return True
        # needs_clarification alone is not enough. Models set it while also
        # reporting 95% confidence, which is incoherent — and honouring it
        # produced "Which project?" for a plainly-scoped request, twice in a
        # row. A stated confidence this high contradicts the flag.
        return self.needs_clarification and self.confidence < CONFIDENT_ENOUGH


def extract(text: str) -> ConfidenceSignal | None:
    """Pull a confidence line out of a reply, or None if absent.

    Absence means the model considered the request clear, which is the
    common case — no signal is not low confidence.
    """
    if not text or not text.strip():
        return None

    match = _SIGNAL_RE.search(text)
    if match is None:
        return None

    try:
        payload = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict) or "confidence" not in payload:
        return None

    try:
        confidence = float(payload.get("confidence", 1.0))
    except (TypeError, ValueError):
        confidence = 1.0

    body = (text[: match.start()] + text[match.end() :]).strip()
    return ConfidenceSignal(
        confidence=max(0.0, min(1.0, confidence)),
        needs_clarification=bool(payload.get("needs_clarification", False)),
        question=str(payload.get("question", "")).strip(),
        body=body,
    )
