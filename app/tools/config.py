"""Tool for reading current VektorCode configuration."""

from __future__ import annotations

import os

from app.config import get_api_key, get_endpoint, get_model


def get_config() -> str:
    """Return current configuration."""
    api_key = get_api_key()
    model = get_model()
    endpoint = get_endpoint()
    masked = api_key[:6] + "..." + api_key[-4:] if len(api_key) > 10 else "(not set)"
    return (
        f"API Key:   {masked}\n"
        f"Model:     {model}\n"
        f"Endpoint:  {endpoint}"
    )
