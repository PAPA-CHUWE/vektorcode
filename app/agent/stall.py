"""Detection of unproductive tool loops.

The existing loop detector catches *identical* repeated calls. It does not
catch a chain of technically-different calls that is going nowhere:

    write_file(test.txt) → write_file(test2.txt) → write_file(test_quote.txt)

Two conditions are detectable without guessing at intent:

**No novelty** — the result contains nothing the model has not already seen.

**Not grounded** — the target appears neither in the user's request nor in
anything the agent has already read. Comparing "fix the parser" against
"search.py" fails on words alone, but search.py turned up in a directory
listing, so it is demonstrably something the agent *found*. Files it
conjures from nowhere (test_quote.txt) match neither source.

Both fire on evidence rather than inference. Neither judges whether a task
"needed tools" — a real chain and a spiral are indistinguishable at call
four, and cutting off work that was about to succeed is worse than the
spiral it prevents. The intervention is a nudge costing one iteration; the
honest give-up at MAX_ITERATIONS still backs it up.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Consecutive unproductive calls before intervening. Well below
# MAX_ITERATIONS so the nudge has room to change the outcome.
STALL_THRESHOLD = 4

# A result with fewer new lines than this carries no real information.
NOVELTY_MIN_NEW_RATIO = 0.15

# Opening calls are allowed to look unrelated while the agent orients.
ORIENTATION_GRACE_CALLS = 2

# Words too common to be evidence that a call is on-topic.
_STOPWORDS = frozenset(
    "the a an and or but of to in on at for with from by is are was were be "
    "been being do does did this that these those it its as if then than so "
    "what which who whom how why when where all any some no not you your i me "
    "my we our they them he she his her can could should would will shall may "
    "might must have has had get got make made use used using file files code "
    "please just now here there about into over under out up down".split()
)

_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


def _tokens(text: str) -> set[str]:
    """Meaningful lowercase words, split on identifier boundaries."""
    words: set[str] = set()
    for raw in _WORD_RE.findall(text or ""):
        lowered = raw.lower()
        if lowered not in _STOPWORDS:
            words.add(lowered)
        for part in lowered.split("_"):
            if len(part) > 2 and part not in _STOPWORDS:
                words.add(part)
    return words


@dataclass
class StallDetector:
    """Tracks whether a turn's tool calls are still going anywhere."""

    request_tokens: set[str] = field(default_factory=set)
    threshold: int = STALL_THRESHOLD

    _seen_content: set[str] = field(default_factory=set)
    _unproductive: int = 0
    _calls: int = 0
    _reason: str = ""

    @classmethod
    def for_request(cls, message: str, threshold: int = STALL_THRESHOLD) -> "StallDetector":
        return cls(request_tokens=_tokens(message), threshold=threshold)

    # ── evidence ────────────────────────────────────────────────────
    def _is_novel(self, result: str) -> bool:
        """True if a result contains lines the model has not already seen."""
        lines = {line.strip() for line in (result or "").splitlines() if line.strip()}
        if not lines:
            return False
        fresh = lines - self._seen_content
        self._seen_content |= lines
        return (len(fresh) / len(lines)) >= NOVELTY_MIN_NEW_RATIO

    def _is_grounded(self, target: str) -> bool:
        """True if the target came from the request or from earlier output."""
        if not target:
            return True  # nothing to judge; do not accuse
        if self.request_tokens and (_tokens(target) & self.request_tokens):
            return True
        basename = target.rsplit("/", 1)[-1].strip('"')
        if not basename:
            return True
        return any(basename in line for line in self._seen_content)

    def record(self, tool: str, target: str, result: str) -> None:
        """Note one completed tool call.

        `tool` is unused today — the signal is the target and the result —
        but it keeps the call site honest about what is being recorded.
        """
        del tool
        self._calls += 1
        # Judge the target against what was known *before* this result.
        grounded = self._is_grounded(target)
        novel = self._is_novel(result)

        if novel and grounded:
            self._unproductive = 0
            self._reason = ""
            return

        if not grounded and self._calls <= ORIENTATION_GRACE_CALLS:
            return

        self._unproductive += 1
        if not novel and not grounded:
            self._reason = (
                "returning information you already have, on files that appear "
                "nowhere in the request or in anything you have read"
            )
        elif not novel:
            self._reason = "returning information you already have"
        else:
            self._reason = (
                "working on files that appear nowhere in the request or in "
                "anything you have read"
            )

    # ── verdict ─────────────────────────────────────────────────────
    @property
    def stalled(self) -> bool:
        return self._unproductive >= self.threshold

    @property
    def reason(self) -> str:
        return self._reason

    def nudge(self, user_message: str) -> str:
        """The message injected when a stall is detected."""
        return (
            f"You have made {self._unproductive} consecutive tool calls "
            f"{self._reason}. Stop and reassess. Either answer what was "
            f'actually asked — "{user_message.strip()[:160]}" — using what '
            "you already know, or make one tool call clearly different from "
            "what you have tried and say why it will help. Do not continue "
            "the current approach."
        )

    def reset(self) -> None:
        """Clear the counter after intervening, so the nudge gets a chance."""
        self._unproductive = 0
        self._reason = ""
