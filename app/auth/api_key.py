"""API Key authentication provider.

Reads the API key from environment variables or .env file.
This is the default provider for VektorCode's internal LiteLLM endpoint.
"""

from __future__ import annotations

import os

from app.auth.base import AuthCredentials


class APIKeyAuthProvider:
    """Authenticates via a static API key (LITELLM_API_KEY).

    The key is read from os.environ on construction.
    login() updates the key in memory and os.environ.
    logout() clears the key from memory and os.environ.
    """

    _ENV_KEY = "LITELLM_API_KEY"

    def __init__(self) -> None:
        self._key: str = os.getenv(self._ENV_KEY, "")

    @property
    def name(self) -> str:
        return "API Key"

    def login(self, **kwargs: str) -> AuthCredentials:
        """Set the API key.

        Accepts: login(api_key="sk-...") or login(key="sk-...")
        """
        key = kwargs.get("api_key") or kwargs.get("key") or ""
        if not key:
            return AuthCredentials(provider_name=self.name)
        self._key = key
        os.environ[self._ENV_KEY] = key
        return AuthCredentials(api_key=key, provider_name=self.name)

    def logout(self) -> None:
        """Clear the API key from memory and environment."""
        self._key = ""
        os.environ.pop(self._ENV_KEY, None)

    def is_authenticated(self) -> bool:
        """Return True if an API key is set."""
        return bool(self._key)

    def get_credentials(self) -> AuthCredentials:
        """Return the current API key."""
        return AuthCredentials(api_key=self._key, provider_name=self.name)

    def get_masked_key(self) -> str:
        """Return a masked version of the key for display."""
        if len(self._key) > 10:
            return self._key[:6] + "..." + self._key[-4:]
        if self._key:
            return "***"
        return "(not set)"
