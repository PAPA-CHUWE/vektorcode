"""Task-type classification for routing, not dispatch.

One structured LLM call tags each request. The tag selects a system-prompt
variant and a confidence threshold — it does not fork the agent into
per-type code paths, which would multiply the maintenance surface for no
behavioural gain.

Every failure mode resolves to CODING, the pre-existing behaviour, so a bad
classification degrades to exactly what the agent did before.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


class TaskType(str, Enum):
    CODING = "coding"
    SEARCH = "search"
    SUMMARIZATION = "summarization"
    CREATIVE_WRITING = "creative_writing"
    QA = "qa"
    CONVERSATIONAL = "conversational"


# Unclassifiable input falls back to the loop that already existed.
DEFAULT_TASK_TYPE = TaskType.CODING

# Types whose output has a checkable shape (see schemas.py). Creative writing
# and open conversation are deliberately absent: forcing them into JSON is
# the fastest way to make the agent feel worse.
STRUCTURED_TYPES = frozenset({
    TaskType.SUMMARIZATION,
    TaskType.SEARCH,
    TaskType.QA,
})

# Deterministic tool work does not need a soft confidence signal — it either
# succeeds or returns a real exit code. Ambiguity gating applies to the rest.
CONFIDENCE_GATED_TYPES = frozenset({
    TaskType.SUMMARIZATION,
    TaskType.SEARCH,
    TaskType.QA,
    TaskType.CREATIVE_WRITING,
    TaskType.CONVERSATIONAL,
})

_CLASSIFY_PROMPT = """\
Classify the user's request into exactly one task type.

coding           — inspect, write, modify, run, test or debug code; git operations
search           — find where something is, locate files or symbols, grep-style lookup
summarization    — condense or explain existing material into a shorter form
creative_writing — compose prose, naming, copy, documentation written from scratch
qa               — a factual question with a determinate answer
conversational   — greetings, thanks, chit-chat, meta questions about the agent

Reply with ONLY this JSON object and nothing else:
{"task_type": "<one of the six>", "confidence": <0.0-1.0>}

Request:
"""

_JSON_RE = re.compile(r"\{[^{}]*\}", re.DOTALL)


@dataclass(frozen=True)
class Classification:
    """The routing decision for one turn."""

    task_type: TaskType
    confidence: float
    fell_back: bool = False

    @property
    def is_structured(self) -> bool:
        return self.task_type in STRUCTURED_TYPES

    @property
    def is_confidence_gated(self) -> bool:
        return self.task_type in CONFIDENCE_GATED_TYPES


def parse_classification(text: str) -> Classification | None:
    """Read a classification out of a model reply, or None if unusable."""
    if not text or not text.strip():
        return None

    candidates: list[str] = []
    stripped = text.strip()
    if stripped.startswith("{"):
        candidates.append(stripped)
    candidates.extend(_JSON_RE.findall(text))

    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        raw = str(payload.get("task_type", "")).strip().lower()
        try:
            task_type = TaskType(raw)
        except ValueError:
            continue
        try:
            confidence = float(payload.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        return Classification(task_type, max(0.0, min(1.0, confidence)))

    # Last resort: the model named a type in prose rather than JSON.
    lowered = text.lower()
    for task_type in TaskType:
        if task_type.value in lowered:
            return Classification(task_type, 0.5)
    return None


def classify(message: str, chat_fn: Any, model: str | None = None) -> Classification:
    """Classify one request. Never raises; falls back to CODING.

    chat_fn is injected so this is testable without a live endpoint and so
    the agent can pass its own configured client.
    """
    if not message or not message.strip():
        return Classification(DEFAULT_TASK_TYPE, 0.0, fell_back=True)

    try:
        response = chat_fn([
            {"role": "system", "content": "You are a request classifier. Reply with JSON only."},
            {"role": "user", "content": _CLASSIFY_PROMPT + message.strip()[:2000]},
        ])
        message_obj = response.choices[0].message
        text = message_obj.content or ""
        if not text.strip():
            text = getattr(message_obj, "reasoning_content", None) or ""
    except Exception:
        return Classification(DEFAULT_TASK_TYPE, 0.0, fell_back=True)

    parsed = parse_classification(text)
    if parsed is None:
        return Classification(DEFAULT_TASK_TYPE, 0.0, fell_back=True)
    return parsed
