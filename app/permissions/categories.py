"""Permission categories split out for SOLID compliance.

This module defines the PermissionCategory enum separately from the main
permissions module, allowing independent testing and maintenance.
"""

from __future__ import annotations

import enum


class PermissionCategory(enum.Enum):
    """Categories of permissions for fine‑grained control."""
    FILESYSTEM = "filesystem"     # File read/write
    SHELL = "shell"               # Shell commands
    SEARCH = "search"             # Code search
    CONFIG = "config"             # Config inspection
    BROWSER = "browser"           # Browser automation
    COMPUTER = "computer"         # Desktop interaction
    SCREENSHOT = "screenshot"     # Screenshot capture
    NETWORK = "network"           # Network access
    GIT = "git"                   # Git operations
    DESTRUCTIVE = "destructive"   # Destructive operations
