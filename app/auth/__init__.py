"""Authentication provider abstraction.

Authentication logic is NOT embedded in the LLM client or config module.
Providers implement this protocol and are injected where needed.

Current providers:
  - APIKeyAuthProvider

Future providers (not implemented):
  - OAuthAuthProvider
  - SSOAuthProvider
"""

from __future__ import annotations

from app.auth.api_key import APIKeyAuthProvider
from app.auth.base import AuthProvider, AuthCredentials

__all__ = [
    "AuthProvider",
    "AuthCredentials",
    "APIKeyAuthProvider",
]
