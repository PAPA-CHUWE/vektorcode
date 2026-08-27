"""VektorCode global configuration.

Resolves workspace, .env location, and version from a single source.
All tools and the agent import from here instead of hardcoding paths.
"""

from __future__ import annotations

import os
from pathlib import Path

from app.auth import APIKeyAuthProvider, AuthProvider

__version__ = "0.1.0"

# The directory this package lives in (installed or editable).
PACKAGE_DIR = Path(__file__).resolve().parent.parent

# Workspace is resolved lazily at CLI startup, not at import time.
# This ensures CWD is captured when the user runs vektorcode, not when
# the module is first imported (critical for pipx installations).
WORKSPACE: Path | None = None


def get_workspace() -> Path:
    """Return the current workspace, falling back to CWD if not yet set."""
    return WORKSPACE if WORKSPACE is not None else Path.cwd().resolve()

# Config search order: ~/.config/vektorcode > package dir > CWD
# Built lazily after WORKSPACE is resolved.
_CONFIG_DIRS: list[Path] | None = None


def _get_config_dirs() -> list[Path]:
    """Return config search dirs, building them on first call.

    Search order:
    1. ~/.config/vektorcode
    2. Package directory (app/)
    3. Workspace root (CWD)
    4. Immediate subdirectories of workspace (e.g., vektor-code-agent/)
    """
    global _CONFIG_DIRS
    if _CONFIG_DIRS is None:
        ws = WORKSPACE or Path.cwd().resolve()
        _CONFIG_DIRS = [
            Path.home() / ".config" / "vektorcode",
            PACKAGE_DIR,
            ws,
        ]
        # Also search immediate subdirectories (for monorepo layouts)
        try:
            for child in sorted(ws.iterdir()):
                if child.is_dir() and not child.name.startswith(".") and child.name != "__pycache__":
                    _CONFIG_DIRS.append(child)
        except PermissionError:
            pass
    return _CONFIG_DIRS

# Singleton auth provider — initialized once at import time.
_auth_provider: AuthProvider | None = None


def get_auth_provider() -> AuthProvider:
    """Return the global auth provider, creating it if needed."""
    global _auth_provider
    if _auth_provider is None:
        _auth_provider = APIKeyAuthProvider()
    return _auth_provider


def set_auth_provider(provider: AuthProvider) -> None:
    """Replace the global auth provider (used by future OAuth/SSO)."""
    global _auth_provider
    _auth_provider = provider


def get_env_path() -> Path:
    """Return the first .env file found in the config search order."""
    for d in _get_config_dirs():
        p = d / ".env"
        if p.is_file():
            return p
    # Default: create one in ~/.config/vektorcode/
    return Path.home() / ".config" / "vektorcode" / ".env"


def load_env(force: bool = False) -> None:
    """Load .env into os.environ.

    By default, skips keys already set in os.environ.
    Pass force=True to overwrite (used by --reload).
    """
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
            if key and (force or key not in os.environ):
                os.environ[key] = value


# Single source of truth for the fallback model. Previously config.py said
# "gemma" while context.py said "qwen", so with LITELLM_MODEL unset the app
# would call one model and size its context budget for another.
DEFAULT_MODEL = "nemotron-3-super"


def get_model() -> str:
    return os.getenv("LITELLM_MODEL", DEFAULT_MODEL)


def get_endpoint() -> str:
    return os.getenv("LITELLM_BASE_URL", "http://localhost:30080")


def get_api_key() -> str:
    """Return the API key from the auth provider."""
    return get_auth_provider().get_credentials().api_key
