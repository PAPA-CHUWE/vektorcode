"""Plan-before-execute for non-trivial tasks.

A short numbered plan, shown before any work starts, is the cheapest point
at which to redirect the agent. It only pays for itself on tasks that would
otherwise take several tool calls.

The triviality threshold matters more than the plan itself. Over-asking
trains people to rubber-stamp without reading, which is worse than not
asking: "read this file" must never produce a plan.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.agent.classify import Classification, TaskType

# Fewer estimated steps than this and planning is pure friction.
PLAN_STEP_THRESHOLD = 3

# A plan longer than this is a sign the task should be broken up, not that
# the plan should be exhaustive.
MAX_PLAN_STEPS = 8

# Only work that changes things benefits from advance agreement. Answering a
# question or writing a paragraph does not.
PLANNABLE_TYPES = frozenset({TaskType.CODING})

_PLAN_PROMPT = """\
Produce a short plan for this request. Concrete steps, each one action.

Rules:
- Between 2 and {max_steps} steps.
- One line per step, numbered "1." "2." and so on.
- Name the actual files, commands or tools you intend to use.
- No preamble, no explanation, no closing remarks — only the numbered list.

Request:
"""

_STEP_RE = re.compile(r"^\s*(\d+)[.)]\s+(.*\S)\s*$", re.MULTILINE)


@dataclass
class Plan:
    """A proposed sequence of steps, open to editing before execution."""

    steps: list[str] = field(default_factory=list)
    revisions: int = 0

    def __len__(self) -> int:
        return len(self.steps)

    def __bool__(self) -> bool:
        return bool(self.steps)

    def as_text(self) -> str:
        return "\n".join(f"{i}. {s}" for i, s in enumerate(self.steps, 1))

    def as_instruction(self) -> str:
        """The agreed plan, phrased for injection into the working prompt."""
        return (
            "The user approved this plan. Follow it, and say so if you need "
            "to deviate:\n" + self.as_text()
        )


def parse_plan(text: str) -> Plan:
    """Extract numbered steps from a model reply."""
    steps = [step.strip() for _, step in _STEP_RE.findall(text or "")]
    # Fall back to non-empty lines if the model skipped the numbering.
    if not steps:
        steps = [
            line.strip(" -•\t")
            for line in (text or "").splitlines()
            if line.strip(" -•\t") and not line.strip().endswith(":")
        ]
    return Plan(steps=steps[:MAX_PLAN_STEPS])


def should_plan(classification: Classification, message: str) -> bool:
    """Whether this request is worth planning first.

    Deliberately conservative. A false positive costs the user a
    confirmation they did not need, and enough of those and they stop
    reading plans altogether.
    """
    if classification.task_type not in PLANNABLE_TYPES:
        return False

    estimated = estimate_steps(message)
    return estimated >= PLAN_STEP_THRESHOLD


# Phrases that reliably indicate multi-step work.
_MULTI_STEP_MARKERS = (
    " and then ", " then ", " after that ", " followed by ",
    " also ", " as well as ", " plus ",
)
_BROAD_SCOPE_MARKERS = (
    "refactor", "migrate", "rewrite", "restructure", "reorganise",
    "reorganize", "rename all", "across", "every file", "all files",
    "throughout", "end to end", "set up", "implement", "add support",
    "overhaul",
)
# Single-action verbs that, alone, mean one tool call.
_TRIVIAL_OPENERS = (
    "read ", "show ", "cat ", "open ", "print ", "list ", "what ", "where ",
    "who ", "when ", "which ", "is ", "does ", "how many ",
)


def estimate_steps(message: str) -> int:
    """Rough count of distinct actions implied by a request.

    A heuristic on purpose: spending an LLM call to decide whether to spend
    an LLM call on a plan is worse than occasionally guessing wrong, and the
    cost of guessing wrong is one unnecessary confirmation.
    """
    text = " " + (message or "").strip().lower() + " "
    if not text.strip():
        return 0

    if any(text.lstrip().startswith(opener) for opener in _TRIVIAL_OPENERS):
        # "read X and tell me Y" is still one read.
        if not any(marker in text for marker in _MULTI_STEP_MARKERS):
            return 1

    steps = 1
    steps += sum(text.count(marker) for marker in _MULTI_STEP_MARKERS)
    if any(marker in text for marker in _BROAD_SCOPE_MARKERS):
        steps += 2
    # Explicit enumeration in the request itself.
    steps += len(re.findall(r"(?m)^\s*[-*\d]+[.)]?\s+\S", message or ""))
    return steps


def propose(message: str, chat_fn: Any) -> Plan:
    """Ask the model for a plan. Returns an empty Plan on any failure."""
    try:
        response = chat_fn([
            {"role": "system", "content": "You produce short, concrete plans. Numbered list only."},
            {"role": "user", "content": _PLAN_PROMPT.format(max_steps=MAX_PLAN_STEPS) + message.strip()[:2000]},
        ])
        reply = response.choices[0].message
        text = reply.content or ""
        if not text.strip():
            text = getattr(reply, "reasoning_content", None) or ""
    except Exception:
        return Plan()
    return parse_plan(text)
