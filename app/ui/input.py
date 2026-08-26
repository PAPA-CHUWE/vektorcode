"""Paste-aware interactive input for VektorCode.

Uses prompt_toolkit for the input prompt, slash-command completion,
and paste detection. Displays compact indicators for large pastes
while preserving the full content for the agent.

Architecture:

    prompt_toolkit PromptSession
         │
         ├── VektorCompleter (slash-command completion)
         ├── PasteDetector (paste size analysis)
         └── FileHistory (persistent history)
         │
         ▼
    InputResult(text, paste_info)
         │
         ▼
    CLI (displays compact indicator, passes full text to agent)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style

from app.ui.completer import VektorCompleter

# ── Paste Detection Thresholds ──────────────────────────────────────

# Below these thresholds, input is shown normally.
PASTE_LINE_THRESHOLD = 20      # Lines above this → paste indicator
PASTE_CHAR_THRESHOLD = 2000    # Chars above this → paste indicator
TOKEN_THRESHOLD = 500          # Estimated tokens above this → token display


# ── Paste Info ──────────────────────────────────────────────────────

@dataclass
class PasteInfo:
    """Metadata about a pasted input."""

    line_count: int
    char_count: int
    estimated_tokens: int
    is_paste: bool

    def format_display(self) -> str:
        """Compact display string for large pastes."""
        if not self.is_paste:
            return ""

        parts = [f"PASTED ~{self.line_count} LINES"]
        if self.char_count > PASTE_CHAR_THRESHOLD:
            k_chars = self.char_count / 1000
            parts.append(f"~{k_chars:.1f}K CHARS")
        if self.estimated_tokens > TOKEN_THRESHOLD:
            k_tokens = self.estimated_tokens / 1000
            parts.append(f"~{k_tokens:.1f}K TOKENS")

        return f"[{' · '.join(parts)}]"


def analyze_paste(text: str) -> PasteInfo:
    """Analyze input text to determine if it's a large paste."""
    lines = text.splitlines()
    line_count = len(lines)
    char_count = len(text)
    # Rough estimate: 1 token ≈ 4 chars
    estimated_tokens = max(1, char_count // 4)

    is_paste = (
        line_count > PASTE_LINE_THRESHOLD
        or char_count > PASTE_CHAR_THRESHOLD
    )

    return PasteInfo(
        line_count=line_count,
        char_count=char_count,
        estimated_tokens=estimated_tokens,
        is_paste=is_paste,
    )


# ── Prompt Style ────────────────────────────────────────────────────

VEKTOR_PROMPT_STYLE = Style.from_dict({
    "prompt": "bold #273c88",
})


# ── Input Result ────────────────────────────────────────────────────

@dataclass
class InputResult:
    """Result from the interactive input handler."""

    text: str
    paste_info: PasteInfo
    is_command: bool

    @property
    def display_text(self) -> str:
        """Text to display in the terminal (compact for large pastes)."""
        if self.paste_info.is_paste:
            return self.paste_info.format_display()
        return self.text


# ── VektorInput ─────────────────────────────────────────────────────

class VektorInput:
    """Interactive input handler with paste detection and slash-command completion.

    Uses prompt_toolkit for input, completion, and history.
    Integrates with Rich for all other terminal output.
    """

    def __init__(self, history_path: Path | None = None) -> None:
        self._history_path = history_path or Path.home() / ".vektorcode_history"
        self._session: PromptSession | None = None

    def _get_session(self) -> PromptSession:
        """Lazy-initialize the prompt_toolkit session."""
        if self._session is None:
            self._session = PromptSession(
                history=FileHistory(str(self._history_path)),
                completer=VektorCompleter(),
                complete_while_typing=True,
                multiline=False,
                wrap_lines=True,
                style=VEKTOR_PROMPT_STYLE,
            )
        return self._session

    def get_input(self) -> InputResult | None:
        """Read user input with paste detection.

        Returns:
            InputResult with text and paste metadata, or None on EOF/Ctrl+C.
        """
        session = self._get_session()

        try:
            text = session.prompt("You › ")
        except KeyboardInterrupt:
            return None
        except EOFError:
            return None

        text = text.strip()
        if not text:
            return InputResult(
                text="",
                paste_info=PasteInfo(line_count=0, char_count=0, estimated_tokens=0, is_paste=False),
                is_command=False,
            )

        paste_info = analyze_paste(text)
        is_command = text.startswith("/")

        return InputResult(
            text=text,
            paste_info=paste_info,
            is_command=is_command,
        )
