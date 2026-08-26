"""Base authentication provider protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class AuthCredentials:
    """Credential payload returned by an auth provider.

    The shape varies by provider:
      - APIKey: api_key is set, token is None
      - OAuth:  token is set, api_key may be None
      - SSO:    token is set, api_key may be None
    """

    api_key: str = ""
    token: str = ""
    provider_name: str = ""

    @property
    def is_valid(self) -> bool:
        return bool(self.api_key or self.token)


@runtime_checkable
class AuthProvider(Protocol):
    """Protocol that all authentication providers must implement.

    Providers are responsible for:
      - Storing/retrieving credentials
      - Reporting authentication status
      - Providing credentials to the LLM client

    The LLM client never reads .env or os.environ directly for auth.
    It calls auth_provider.get_credentials() instead.
    """

    @property
    def name(self) -> str:
        """Human-readable provider name (e.g. 'API Key', 'OAuth')."""
        ...

    def login(self, **kwargs: str) -> AuthCredentials:
        """Authenticate and return credentials.

        For APIKeyAuthProvider: login(api_key="sk-...")
        For OAuthAuthProvider:  login(client_id="...", client_secret="...")
        """
        ...

    def logout(self) -> None:
        """Clear stored credentials."""
        ...

    def is_authenticated(self) -> bool:
        """Return True if valid credentials are available."""
        ...

    def get_credentials(self) -> AuthCredentials:
        """Return current credentials for use by the LLM client."""
        ...
