"""Tool execution events for VektorCode.

Lightweight structured events emitted during tool execution.
These are consumed by the Activity Engine and CLI renderer.

Events do NOT enter conversation history — they are UI/telemetry state.
Events do NOT expose secrets, API keys, or credentials.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class ToolEventType(str, Enum):
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    TOOL_FAILED = "tool_failed"


@dataclass
class ToolEvent:
    """Structured event emitted during tool execution."""

    type: ToolEventType
    tool: str
    category: str = ""
    target: str = ""
    duration: float = 0.0
    success: bool = True
    error: str = ""
    metadata: dict[str, object] = field(default_factory=dict)

    @staticmethod
    def started(tool: str, category: str = "", target: str = "") -> ToolEvent:
        return ToolEvent(
            type=ToolEventType.TOOL_STARTED,
            tool=tool,
            category=category,
            target=target,
        )

    @staticmethod
    def completed(tool: str, category: str = "", duration: float = 0.0, success: bool = True) -> ToolEvent:
        return ToolEvent(
            type=ToolEventType.TOOL_COMPLETED,
            tool=tool,
            category=category,
            duration=duration,
            success=success,
        )

    @staticmethod
    def failed(tool: str, category: str = "", duration: float = 0.0, error: str = "") -> ToolEvent:
        return ToolEvent(
            type=ToolEventType.TOOL_FAILED,
            tool=tool,
            category=category,
            duration=duration,
            success=False,
            error=error,
        )


# ── Event subscriber system ──────────────────────────────────────────

EventSubscriber = callable


class EventBus:
    """Simple internal event bus for tool events.

    No external dependencies. Supports multiple subscribers.
    Subscribers are called synchronously in registration order.
    """

    def __init__(self) -> None:
        self._subscribers: list[EventSubscriber] = []

    def subscribe(self, fn: EventSubscriber) -> None:
        self._subscribers.append(fn)

    def unsubscribe(self, fn: EventSubscriber) -> None:
        self._subscribers = [s for s in self._subscribers if s is not fn]

    def emit(self, event: ToolEvent) -> None:
        for subscriber in self._subscribers:
            try:
                subscriber(event)
            except Exception:
                pass  # Never let a subscriber break tool execution


# Global event bus
_event_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
    return _event_bus
