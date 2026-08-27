"""VektorCode global configuration."""

from app.config.config import (
    DEFAULT_MODEL,
    __version__,
    get_workspace,
    get_auth_provider,
    set_auth_provider,
    get_env_path,
    load_env,
    get_model,
    get_endpoint,
    get_api_key,
    PACKAGE_DIR,
    WORKSPACE,
)

__all__ = [
    "DEFAULT_MODEL",
    "__version__",
    "get_workspace",
    "get_auth_provider",
    "set_auth_provider",
    "get_env_path",
    "load_env",
    "get_model",
    "get_endpoint",
    "get_api_key",
    "PACKAGE_DIR",
    "WORKSPACE",
]
