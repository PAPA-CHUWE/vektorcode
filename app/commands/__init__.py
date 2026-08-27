"""VektorCode CLI commands package.

Single source of truth for all slash commands: metadata + handlers.
The completer and /help both derive from this registry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class CommandMeta:
    """Metadata for a single slash command."""

    name: str
    description: str
    usage: str = ""


def _noop(args: list[str]) -> str | None:
    """Placeholder handler — commands handled inline in dispatch."""
    return None


def _build_registry() -> tuple[list[CommandMeta], dict[str, Callable[..., Any]]]:
    """Build the command registry from all command modules.

    Returns (metadata_list, handlers_dict).
    """
    from app.commands.config import (
        cmd_auth,
        cmd_endpoint,
        cmd_help,
        cmd_model,
        cmd_reload,
    )
    from app.commands.models import cmd_models
    from app.commands.session import cmd_clear, cmd_status

    metadata = [
        CommandMeta("/help",      "Show available commands"),
        CommandMeta("/status",    "Show agent and workspace status"),
        CommandMeta("/model",     "View or change the active model"),
        CommandMeta("/models",    "List available models from the endpoint"),
        CommandMeta("/endpoint",  "Show or set the LLM base URL"),
        CommandMeta("/auth",      "Show, set, or clear the API key"),
        CommandMeta("/workspace", "Show current workspace path"),
        CommandMeta("/tools",     "Show available tools"),
        CommandMeta("/clear",     "Clear conversation history"),
        CommandMeta("/tokens",    "Toggle token counts in the status bar"),
        CommandMeta("/queue",     "List queued messages (clear | pop)"),
        CommandMeta("/mode",      "Show or set permission mode (shift+tab cycles)"),
        CommandMeta("/trace",     "Show the full traceback for the last error"),
        CommandMeta("/reload",    "Reload configuration from .env"),
        CommandMeta("/exit",      "Exit VektorCode"),
    ]

    handlers: dict[str, Callable[..., Any]] = {
        "/auth": cmd_auth,
        "/model": cmd_model,
        "/models": cmd_models,
        "/endpoint": cmd_endpoint,
        "/reload": cmd_reload,
        "/help": cmd_help,
        "/status": cmd_status,
        "/clear": cmd_clear,
    }

    return metadata, handlers


# Build once at import time
COMMAND_registry: list[CommandMeta]
COMMANDS: dict[str, Callable[..., Any]]

COMMAND_registry, COMMANDS = _build_registry()

COMMANDS_BY_NAME: dict[str, CommandMeta] = {
    c.name: c for c in COMMAND_registry
}


def format_help_text() -> str:
    """Build the /help display text from the command registry."""
    lines = ["Available commands\n"]
    for cmd in COMMAND_registry:
        lines.append(f"  {cmd.name:<12}{cmd.description}")
    lines.append(f"\n  {'exit':<12}Exit VektorCode")
    return "\n".join(lines)
