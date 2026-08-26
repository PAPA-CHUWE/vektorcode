"""Tool for reading current VektorCode configuration."""

from __future__ import annotations

from app.config import get_auth_provider, get_endpoint, get_model


def get_config() -> str:
    """Return current configuration via the auth provider."""
    provider = get_auth_provider()
    creds = provider.get_credentials()
    model = get_model()
    endpoint = get_endpoint()

    if creds.api_key:
        masked = creds.api_key[:6] + "..." + creds.api_key[-4:] if len(creds.api_key) > 10 else "***"
    else:
        masked = "(not set)"

    return (
        f"Provider:  {provider.name}\n"
        f"API Key:   {masked}\n"
        f"Model:     {model}\n"
        f"Endpoint:  {endpoint}"
    )
