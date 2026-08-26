"""VektorCode global configuration.

Resolves workspace, .env location, and version from a single source.
All tools and the agent import from here instead of hardcoding paths.
"""

from __future__ import annotations

import os
from pathlib import Path

__version__ = "0.1.0"

# The directory this package lives in (installed or editable).
PACKAGE_DIR = Path(__file__).resolve().parent.parent

# The user's current working directory — always the workspace.
WORKSPACE = Path.cwd().resolve()

# Config search order: ~/.config/vektorcode > package dir > CWD
_CONFIG_DIRS: list[Path] = [
    Path.home() / ".config" / "vektorcode",
    PACKAGE_DIR,
    WORKSPACE,
]


def get_env_path() -> Path:
    """Return the first .env file found in the config search order."""
    for d in _CONFIG_DIRS:
        p = d / ".env"
        if p.is_file():
            return p
    # Default: create one in ~/.config/vektorcode/
    return Path.home() / ".config" / "vektorcode" / ".env"


def load_env() -> None:
    """Load .env into os.environ without overwriting existing values."""
    env_path = get_env_path()
    if not env_path.is_file():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if key and key not in os.environ:
                os.environ[key] = value


def get_model() -> str:
    return os.getenv("LITELLM_MODEL", "gemma")


def get_endpoint() -> str:
    return os.getenv("LITELLM_BASE_URL", "http://localhost:30080")


def get_api_key() -> str:
    return os.getenv("LITELLM_API_KEY", "")
