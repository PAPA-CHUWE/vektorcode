"""Permission model for VektorCode tools.

Defines permission levels, policy configuration, and permission checking.

Permission levels (increasing privilege):
- NONE: No tool access (not practical as default)
- READ_ONLY: File read, list, search (existing baseline)
- BROWSER: Launch browser, navigate, screenshot, DOM access
- INTERACTIVE: Mouse/keyboard interaction with desktop UI
- FULL: All capabilities including destructive operations

Policy is enforced at tool execution time. Tools check the policy
before performing actions. The agent never bypasses permissions.
"""

from __future__ import annotations

import enum
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from app.config import get_workspace

logger = logging.getLogger(__name__)


class PermissionLevel(enum.Enum):
    """Permission levels for VektorCode tools."""
    NONE = "none"           # No tools
    READ_ONLY = "read_only" # File read, list, search, config
    SHELL_READ = "shell_read"   # Shell commands (read-only, no destructive)
    SHELL_FULL = "shell_full"   # Full shell with confirmation for destructive
    BROWSER = "browser"       # Browser automation
    INTERACTIVE = "interactive"   # Desktop computer use
    FULL = "full"         # All capabilities


class PermissionCategory(enum.Enum):
    """Categories of permissions for fine-grained control."""
    FILESYSTEM = "filesystem"     # File read/write
    SHELL = "shell"           # Shell commands
    SEARCH = "search"     # Code search
    CONFIG = "config"     # Config inspection
    BROWSER = "browser"   # Browser automation
    COMPUTER = "computer"   # Desktop interaction
    SCREENSHOT = "screenshot"   # Screenshot capture
    NETWORK = "network"     # Network access
    DESTRUCTIVE = "destructive"   # Destructive operations


@dataclass
class PermissionPolicy:
    """Policy configuration for VektorCode permissions.

    This defines what the agent is allowed to do. It is loaded from
    a policy file or constructed from environment variables.
    """

    # Overall permission level
    level: PermissionLevel = PermissionLevel.READ_ONLY

    # Category-level overrides
    categories: dict[str, bool] = field(default_factory=lambda: {
        "filesystem": True,
        "shell": False,
        "search": True,
        "config": True,
        "browser": False,
        "computer": False,
        "screenshot": False,
        "network": False,
        "destructive": False,
    })

    # Browser policy
    browser_allowed_domains: list[str] = field(default_factory=lambda: [
        "localhost",
        "127.0.0.1",
    ])

    # Destructive action policy
    destructive_requires_confirmation: bool = True

    # Screenshot policy
    screenshot_ephemeral: bool = True       # Delete after use
    screenshot_logging: bool = False        # Never log screenshots

    # Loop limits
    max_tool_calls_per_step: int = 15
    max_iterations_per_user: int = 30
    action_timeout_seconds: int = 300

    # Approval policy
    approval_required_actions: list[str] = field(default_factory=lambda: [
        "delete",
        "submit",
        "publish",
        "send",
        "install",
        "modify_account",
        "change_security",
    ])

    def is_allowed(self, category: PermissionCategory) -> bool:
        """Check if a category is allowed under this policy."""
        cat_name = category.value
        if self.categories.get(cat_name, False):
            return True
        # Check overall level
        if self.level == PermissionLevel.FULL:
            return True
        if self.level == PermissionLevel.READ_ONLY and cat_name in (
            "filesystem",
            "search",
            "config",
        ):
            return True
        return False

    def is_domain_allowed(self, domain: str) -> bool:
        """Check if a domain is allowed for browser access."""
        for allowed in self.browser_allowed_domains:
            if allowed in domain or domain in allowed:
                return True
        return False

    def requires_approval(self, action: str) -> bool:
        """Check if an action requires user approval."""
        action_lower = action.lower()
        for required_action in self.approval_required_actions:
            if required_action.lower() in action_lower:
                return True
        return False


@dataclass
class PermissionManager:
    """Manages permission policies and checks."""

    policy: PermissionPolicy = field(default_factory=PermissionPolicy)
    user_consents: dict[str, bool] = field(default_factory=dict)  # action -> approved
    consent_timestamps: dict[str, float] = field(default_factory=dict)  # action -> timestamp

    def check_permission(self, category: PermissionCategory) -> bool:
        """Check if a category is permitted."""
        return self.policy.is_allowed(category)

    def check_domain(self, domain: str) -> bool:
        """Check if a domain is allowed."""
        return self.policy.is_domain_allowed(domain)

    def require_approval(self, action: str, user_input: Optional[str] = None) -> bool:
        """Check if an action requires approval, optionally ask user."""
        if not self.policy.requires_approval(action):
            return True  # No approval needed

        # Check if user already consented
        if self.user_consents.get(action, False):
            return True

        if user_input and user_input.lower() in ("y", "yes", "true"):
            self.user_consents[action] = True
            return True

        return False  # Approval required but not given

    def grant_permission(self, category: PermissionCategory) -> None:
        """Grant permission for a category (used by CLI commands)."""
        self.policy.categories[category.value] = True
        logger.info(f"Granted permission: {category.value}")

    def revoke_permission(self, category: PermissionCategory) -> None:
        """Revoke permission for a category."""
        self.policy.categories[category.value] = False
        logger.info(f"Revoked permission: {category.value}")

    def save_policy(self, path: Optional[Path] = None) -> Path:
        """Save current policy to a file."""
        policy_path = path or get_workspace() / ".vektorcode-policy.json"
        policy_data = {
            "level": self.policy.level.value,
            "categories": self.policy.categories,
            "browser_allowed_domains": self.policy.browser_allowed_domains,
            "destructive_requires_confirmation": self.policy.destructive_requires_confirmation,
            "screenshot_ephemeral": self.policy.screenshot_ephemeral,
            "screenshot_logging": self.policy.screenshot_logging,
            "max_tool_calls_per_step": self.policy.max_tool_calls_per_step,
            "max_iterations_per_user": self.policy.max_iterations_per_user,
            "action_timeout_seconds": self.policy.action_timeout_seconds,
            "approval_required_actions": self.policy.approval_required_actions,
        }
        policy_path.write_text(json.dumps(policy_data, indent=2))
        return policy_path

    def load_policy(self, path: Optional[Path] = None) -> "PermissionManager":
        """Load policy from a file."""
        policy_path = path or get_workspace() / ".vektorcode-policy.json"
        if not policy_path.exists():
            return self
        try:
            data = json.loads(policy_path.read_text())
            self.policy = PermissionPolicy(
                level=PermissionLevel(data.get("level", "read_only")),
                categories=data.get("categories", self.policy.categories),
                browser_allowed_domains=data.get(
                    "browser_allowed_domains",
                    self.policy.browser_allowed_domains,
                ),
                destructive_requires_confirmation=data.get(
                    "destructive_requires_confirmation",
                    self.policy.destructive_requires_confirmation,
                ),
                screenshot_ephemeral=data.get(
                    "screenshot_ephemeral",
                    self.policy.screenshot_ephemeral,
                ),
                screenshot_logging=data.get(
                    "screenshot_logging",
                    self.policy.screenshot_logging,
                ),
                max_tool_calls_per_step=data.get(
                    "max_tool_calls_per_step",
                    self.policy.max_tool_calls_per_step,
                ),
                max_iterations_per_user=data.get(
                    "max_iterations_per_user",
                    self.policy.max_iterations_per_user,
                ),
                action_timeout_seconds=data.get(
                    "action_timeout_seconds",
                    self.policy.action_timeout_seconds,
                ),
                approval_required_actions=data.get(
                    "approval_required_actions",
                    self.policy.approval_required_actions,
                ),
            )
        except (json.JSONDecodeError, PermissionError, FileNotFoundError):
            logger.warning("Failed to load policy, using defaults")
        return self


# Global permission manager instance
_permission_manager: Optional[PermissionManager] = None


def get_permission_manager() -> PermissionManager:
    """Get the global permission manager, creating it if needed."""
    global _permission_manager
    if _permission_manager is None:
        _permission_manager = PermissionManager()
        _permission_manager.load_policy()  # Load from workspace if exists
    return _permission_manager


def reset_permission_manager() -> None:
    """Reset the global permission manager (for testing)."""
    global _permission_manager
    _permission_manager = None
