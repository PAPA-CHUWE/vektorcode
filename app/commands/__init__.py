"""VektorCode CLI commands package."""

from __future__ import annotations

from typing import Any

from app.commands.config import cmd_auth, cmd_endpoint, cmd_help, cmd_model, cmd_reload
from app.commands.models import cmd_models
from app.commands.session import cmd_clear, cmd_status

COMMANDS: dict[str, Any] = {
    "/auth": cmd_auth,
    "/model": cmd_model,
    "/models": cmd_models,
    "/endpoint": cmd_endpoint,
    "/reload": cmd_reload,
    "/help": cmd_help,
    "/status": cmd_status,
    "/clear": cmd_clear,
}
