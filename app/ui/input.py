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
import re
from dataclasses import dataclass, field
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.styles import Style

from app.permissions.mode import cycle_mode, get_mode
from app.ui.runstate import run_state
from app.ui.completer import VektorCompleter

# ── Paste Detection Thresholds ──────────────────────────────────────

# Below these thresholds, input is shown normally.
PASTE_LINE_THRESHOLD = 20      # Lines above this → paste indicator
PASTE_CHAR_THRESHOLD = 2000    # Chars above this → paste indicator
TOKEN_THRESHOLD = 500          # Estimated tokens above this → token display

# A single-line paste this long wraps across rows and is collapsed too.
PASTE_COLLAPSE_CHARS = 400

# Placeholder shown in the prompt in place of collapsed paste content.
_PLACEHOLDER_RE = re.compile(r"\[#(\d+) Pasted \d+ lines?\]")


def _placeholder(index: int, line_count: int) -> str:
    """Short stand-in rendered in the prompt for a collapsed paste."""
    unit = "line" if line_count == 1 else "lines"
    return f"[#{index} Pasted {line_count} {unit}]"


def should_collapse(data: str) -> bool:
    """True if pasted data should be hidden behind a placeholder.

    Any newline qualifies: the prompt is single-line, so embedded newlines
    render across multiple rows and the whole paste echoes into the terminal.
    """
    return "\n" in data or len(data) > PASTE_COLLAPSE_CHARS


def expand_pastes(text: str, pastes: dict[int, str]) -> str:
    """Restore collapsed paste content before handing text to the agent."""
    def _sub(match: re.Match[str]) -> str:
        return pastes.get(int(match.group(1)), match.group(0))

    return _PLACEHOLDER_RE.sub(_sub, text)


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
    "prompt.workspace": "bold #273c88",
    "prompt.branch": "#cda716",
    "prompt.arrow": "bold #273c88",
    "prompt.continuation": "#273c88",
    "toolbar.mode": "bold #cda716",
    "toolbar.hint": "#666666",
    "toolbar.running": "bold #cda716",
    "toolbar.activity": "#273c88",
    "toolbar.queued": "bold #007d59",
})


def git_branch(path: Path) -> str:
    """Current git branch for path, or "" if not a repo.

    Reads .git/HEAD directly — no subprocess, so this stays cheap enough to
    call on every prompt.
    """
    current = path.resolve()
    for candidate in [current, *current.parents]:
        head = candidate / ".git" / "HEAD"
        if not head.is_file():
            continue
        try:
            content = head.read_text(encoding="utf-8").strip()
        except OSError:
            return ""
        if content.startswith("ref:"):
            return content.rpartition("/")[2]
        return content[:7]  # detached HEAD
    return ""


def _continuation(width: int, line_number: int, is_soft_wrap: bool) -> list[tuple[str, str]]:
    """Marker drawn at the start of every wrapped/continued input line."""
    return [("class:prompt.continuation", "│ ".rjust(width))]


def prompt_fragments(workspace: Path) -> list[tuple[str, str]]:
    """Build the prompt as prompt_toolkit style/text fragments.

    Renders as "VektorCode (main) › ", putting cheap, genuinely useful
    context inline instead of on a separate status line.
    """
    fragments = [("class:prompt.workspace", workspace.name)]
    branch = git_branch(workspace)
    if branch:
        fragments.append(("class:prompt.branch", f" ({branch})"))
    fragments.append(("class:prompt.arrow", " › "))
    return fragments


class _Interrupt:
    """Sentinel: the user pressed Ctrl+C rather than sending EOF.

    Ctrl+C means "stop what you're doing"; Ctrl+D means "I'm done". Returning
    None for both made an idle Ctrl+C quit the REPL.
    """

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return "INTERRUPT"


INTERRUPT = _Interrupt()


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

    def __init__(
        self,
        history_path: Path | None = None,
        workspace: Path | None = None,
    ) -> None:
        self._history_path = history_path or Path.home() / ".vektorcode_history"
        self._workspace = workspace or Path.cwd()
        self._session: PromptSession | None = None
        # Collapsed paste bodies for the current prompt, keyed by placeholder id.
        self._pastes: dict[int, str] = {}
        self._paste_seq = 0

    def _bottom_toolbar(self) -> list[tuple[str, str]]:
        """Live footer under the prompt showing the permission mode.

        Rendered every keystroke, so Shift+Tab feedback is immediate and the
        mode is visible *before* a destructive command is ever proposed.
        """
        mode = get_mode()
        parts: list[tuple[str, str]] = [("class:toolbar.mode", f" {mode.label} ")]

        if run_state.running:
            parts.append(("class:toolbar.running", " ⚙ running "))
            if run_state.activity:
                parts.append(("class:toolbar.activity", f"{run_state.activity} "))
            if run_state.max_steps:
                parts.append(("class:toolbar.hint", f" step {run_state.steps}/{run_state.max_steps} "))
            parts.append(("class:toolbar.hint", " enter queues · ctrl+c interrupts "))
        else:
            parts.append(("class:toolbar.hint", " shift+tab to cycle "))

        if run_state.queued:
            parts.append(("class:toolbar.queued", f" ⏳ {run_state.queued} queued "))
        return parts

    def _key_bindings(self) -> KeyBindings:
        """Bind bracketed paste so multi-line content collapses to a marker.

        prompt_toolkit's default binding inserts the raw data into the buffer.
        In a single-line prompt that echoes every pasted line into the
        terminal before the input is even submitted.
        """
        kb = KeyBindings()

        @kb.add(Keys.BracketedPaste)
        def _(event) -> None:
            data = event.data.replace("\r\n", "\n").replace("\r", "\n")
            if not should_collapse(data):
                event.current_buffer.insert_text(data)
                return
            self._paste_seq += 1
            index = self._paste_seq
            self._pastes[index] = data
            event.current_buffer.insert_text(
                _placeholder(index, len(data.splitlines()))
            )

        @kb.add(Keys.BackTab)  # Shift+Tab
        def _(event) -> None:
            cycle_mode()
            event.app.invalidate()  # redraw the toolbar immediately

        return kb

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
                key_bindings=self._key_bindings(),
                bottom_toolbar=self._bottom_toolbar,
            )
        return self._session

    def _build(self, raw: str) -> InputResult:
        """Turn submitted prompt text into an InputResult."""
        # The prompt held placeholders; the agent must receive the real text.
        text = expand_pastes(raw, self._pastes).strip()
        if not text:
            return InputResult(
                text="",
                paste_info=PasteInfo(line_count=0, char_count=0, estimated_tokens=0, is_paste=False),
                is_command=False,
            )
        return InputResult(
            text=text,
            paste_info=analyze_paste(text),
            # A slash command is a single line. A pasted block that happens
            # to begin with "/" is content, not a command.
            is_command=text.startswith("/") and "\n" not in text,
        )

    def get_input(self) -> InputResult | None:
        """Read user input with paste detection.

        Returns:
            InputResult with text and paste metadata, or None on EOF/Ctrl+C.
        """
        session = self._get_session()
        self._pastes.clear()
        try:
            raw = session.prompt(
                prompt_fragments(self._workspace),
                prompt_continuation=_continuation,
            )
        except (KeyboardInterrupt, EOFError):
            return None
        return self._build(raw)

    async def get_input_async(self) -> "InputResult | _Interrupt | None":
        """Async twin of get_input, for the concurrent REPL loop.

        Running the prompt on the event loop is what lets the user keep
        typing while a turn is still executing.
        """
        session = self._get_session()
        self._pastes.clear()
        try:
            raw = await session.prompt_async(
                prompt_fragments(self._workspace),
                prompt_continuation=_continuation,
            )
        except KeyboardInterrupt:
            return INTERRUPT
        except EOFError:
            return None
        return self._build(raw)
