"""Permission modes and human approval for privileged tool actions.

The agent must never be able to approve its own privileged actions. Before
this module, ``run_command`` took a model-settable ``confirm`` flag and the
refusal message told the model exactly how to bypass it — so ``rm -rf`` ran
without a human ever being asked.

Approval now flows the other way: tools ask this module, and this module asks
a human through a callback the UI installs. The model has no say.

    tool  →  request_approval(kind, detail)
                 │
                 ├── mode says auto-allow  → True
                 ├── approver installed    → ask the human
                 └── no approver           → deny (non-interactive is not consent)
"""

from __future__ import annotations

import enum
from typing import Callable


class PermissionMode(enum.Enum):
    """How much the agent may do without asking."""

    DEFAULT = "default"        # confirm writes, shell, destructive
    AUTO_EDIT = "auto-edit"    # writes are free; shell/destructive still ask
    FULL_AUTO = "full-auto"    # never ask

    @property
    def label(self) -> str:
        return self.value

    @property
    def style(self) -> str:
        """Theme style used when the mode appears in the status bar."""
        return {
            PermissionMode.DEFAULT: "success",
            PermissionMode.AUTO_EDIT: "warning",
            PermissionMode.FULL_AUTO: "error",
        }[self]


# Kinds of action a tool can request approval for.
WRITE = "write"
SHELL = "shell"
DESTRUCTIVE = "destructive"

# Which kinds each mode waves through without asking.
_AUTO_ALLOWED: dict[PermissionMode, set[str]] = {
    PermissionMode.DEFAULT: set(),
    PermissionMode.AUTO_EDIT: {WRITE},
    PermissionMode.FULL_AUTO: {WRITE, SHELL, DESTRUCTIVE},
}

_MODE_ORDER = [
    PermissionMode.DEFAULT,
    PermissionMode.AUTO_EDIT,
    PermissionMode.FULL_AUTO,
]

_mode: PermissionMode = PermissionMode.DEFAULT

# Installed by the interactive UI. Signature: (kind, detail) -> bool.
_approver: Callable[[str, str], bool] | None = None


# ── mode state ──────────────────────────────────────────────────────

def get_mode() -> PermissionMode:
    """The active permission mode."""
    return _mode


def set_mode(mode: PermissionMode) -> PermissionMode:
    """Set the permission mode explicitly."""
    global _mode
    _mode = mode
    return _mode


def cycle_mode() -> PermissionMode:
    """Advance to the next mode, wrapping around. Bound to Shift+Tab."""
    global _mode
    _mode = _MODE_ORDER[(_MODE_ORDER.index(_mode) + 1) % len(_MODE_ORDER)]
    return _mode


def mode_from_name(name: str) -> PermissionMode | None:
    """Look up a mode by its label, or None if unknown."""
    for mode in _MODE_ORDER:
        if mode.value == name.strip().lower():
            return mode
    return None


def all_modes() -> list[PermissionMode]:
    return list(_MODE_ORDER)


# ── approval ────────────────────────────────────────────────────────

def set_approver(callback: Callable[[str, str], bool] | None) -> None:
    """Install the human-approval callback (the interactive UI does this)."""
    global _approver
    _approver = callback


def has_approver() -> bool:
    """True if a human can currently be asked."""
    return _approver is not None


def request_approval(kind: str, detail: str) -> bool:
    """Ask whether a privileged action may proceed.

    Called by tools, never by the agent. Returns True only if the mode
    auto-allows this kind, or a human approves it.
    """
    if kind in _AUTO_ALLOWED[_mode]:
        return True
    if _approver is None:
        # One-shot / piped runs have nobody to ask. Silence is not consent.
        return False
    return bool(_approver(kind, detail))
