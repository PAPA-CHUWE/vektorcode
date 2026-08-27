"""Terminal cursor-shape signaling.

Uses DECSCUSR escape codes to distinguish "waiting for your input" from
"agent is working", independent of the terminal window's own OS-level focus
rendering.

Shapes are configurable via VEKTOR_CURSOR_READY / VEKTOR_CURSOR_BUSY (see
SHAPES). Writes are gated on stdout being a TTY. Ungated, the raw bytes land in
redirected output — `vektorcode "..." > out.txt` would begin the file with
`\\x1b[5 q`. Terminals that don't understand DECSCUSR ignore it silently, so
no capability detection beyond isatty() is needed.
"""

from __future__ import annotations

import os
import sys

# DECSCUSR shapes. There is no width parameter — width comes from the shape,
# so a wider cursor means block (full cell) or underline, not bar.
SHAPES: dict[str, str] = {
    "blinking-block": "\x1b[1 q",      # widest
    "block": "\x1b[2 q",
    "blinking-underline": "\x1b[3 q",
    "underline": "\x1b[4 q",
    "blinking-bar": "\x1b[5 q",        # thinnest
    "bar": "\x1b[6 q",
}

# Ready is a blinking full-cell block; busy is the same block without the
# blink. Shape stays constant so the difference reads as motion, not size.
_READY_DEFAULT = "blinking-block"
_BUSY_DEFAULT = "block"

_DEFAULT = "\x1b[0 q"        # reset to terminal default


def _shape(env_var: str, fallback: str) -> str:
    """Resolve a cursor shape from the environment, falling back safely."""
    name = os.getenv(env_var, "").strip().lower()
    return SHAPES.get(name) or SHAPES[fallback]


def _write(code: str) -> None:
    """Emit an escape code, but only to a real terminal.

    Never raises: a closed or non-interactive stdout must not take down the
    CLI over a cosmetic cursor change.
    """
    try:
        if not sys.stdout.isatty():
            return
        sys.stdout.write(code)
        sys.stdout.flush()
    except (OSError, ValueError, AttributeError):
        pass


def set_cursor_ready() -> None:
    """Waiting for input. Override the shape with VEKTOR_CURSOR_READY."""
    _write(_shape("VEKTOR_CURSOR_READY", _READY_DEFAULT))


def set_cursor_busy() -> None:
    """Steady block cursor: the agent is processing a turn.

    Input can still be typed and queued while this is showing — it signals
    "not answering you yet", not "not listening".

    Override the shape with VEKTOR_CURSOR_BUSY.
    """
    _write(_shape("VEKTOR_CURSOR_BUSY", _BUSY_DEFAULT))


def reset_cursor() -> None:
    """Restore the terminal's default cursor shape.

    Called on every exit path so VektorCode never leaves a custom cursor
    shape active in the user's shell afterwards.
    """
    _write(_DEFAULT)
