"""VektorCode LLM client — OpenAI-compatible API calls."""

from __future__ import annotations

import os
from typing import Any

from openai import OpenAI

from app.config import get_api_key, get_endpoint, get_model, load_env

load_env()


def _get_client() -> OpenAI:
    return OpenAI(api_key=get_api_key() or "no-key", base_url=get_endpoint())


def chat(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    model: str | None = None,
) -> Any:
    client = _get_client()
    kwargs: dict[str, Any] = {
        "model": model or get_model(),
        "messages": messages,
    }
    if tools:
        kwargs["tools"] = tools
    return client.chat.completions.create(**kwargs)
