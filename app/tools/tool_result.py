"""Structured tool result format for all VektorCode tools.

Every tool returns a ToolResult instead of raw strings. The agent
decodes these and the UI layer renders them appropriately.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Optional


class ToolStatus(enum.Enum):
    SUCCESS = "success"
    ERROR = "error"
    BLOCKED = "blocked"
    PARTIAL = "partial"  # e.g., screenshot captured but observation failed


@dataclass
class ToolResult:
    """Structured result returned by every VektorCode tool.

    Attributes:
        status: Whether the tool succeeded, failed, or was blocked.
        data: Any data the tool produced (file contents, command output, etc.).
        observation: Human-readable observation (e.g., "Clicking Login button").
        error: Error message if status is ERROR or BLOCKED.
        metadata: Extra structured data (duration, screenshot path, etc.).
    """

    status: ToolStatus = ToolStatus.SUCCESS
    data: Any = None
    observation: str = ""
    error_msg: Optional[str] = field(default=None, metadata={"alias": "error"})
    metadata: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def success(data: Any = None, observation: str = "", metadata: dict[str, Any] | None = None) -> ToolResult:
        """Create a successful result."""
        return ToolResult(
            status=ToolStatus.SUCCESS,
            data=data,
            observation=observation,
            metadata=metadata or {},
        )

    @staticmethod
    def make_error(message: str, data: Any = None) -> ToolResult:
        """Create an error result."""
        return ToolResult(
            status=ToolStatus.ERROR,
            data=data,
            error_msg=message,
            observation=message,
        )

    @staticmethod
    def blocked(message: str, data: Any = None) -> ToolResult:
        """Create a blocked result (permission denied)."""
        return ToolResult(
            status=ToolStatus.BLOCKED,
            data=data,
            error_msg=message,
            observation=message,
        )

    @property
    def error(self) -> Optional[str]:
        return self.error_msg

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for agent history."""
        return {
            "status": self.status.value,
            "data": self.data,
            "observation": self.observation,
            "error": self.error_msg,
            "metadata": self.metadata,
        }

    def __str__(self) -> str:
        """String representation for terminal output."""
        if self.error_msg:
            # A refusal is not an error; it already says why it was blocked.
            if self.status == ToolStatus.BLOCKED:
                return self.error_msg
            return f"Error: {self.error_msg}"
        if self.data is not None:
            if isinstance(self.data, str):
                return self.data
            return str(self.data)
        if self.observation:
            return self.observation
        return ""
