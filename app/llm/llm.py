"""VektorCode LLM client — OpenAI-compatible API calls.

Authentication is handled by the auth provider, never read directly
from .env or os.environ inside this module.
"""

from __future__ import annotations

from typing import Any

from openai import OpenAI

from app.config import get_auth_provider, get_endpoint, get_model, load_env

load_env()


def _get_client() -> OpenAI:
    creds = get_auth_provider().get_credentials()
    api_key = creds.api_key or creds.token or "no-key"
    return OpenAI(api_key=api_key, base_url=get_endpoint())


# Generation is halted at the end of the first tool call. Without this the
# model keeps writing past its own <tool_call> and fabricates <tool_output>
# blocks — role-playing both sides of the conversation. It then acts on
# invented results, and re-requests the same data once real results arrive.
TOOL_CALL_STOP = "</tool_call>"

MAX_OUTPUT_TOKENS = 4096


def chat(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    model: str | None = None,
) -> Any:
    client = _get_client()
    kwargs: dict[str, Any] = {
        "model": model or get_model(),
        "messages": messages,
        "stop": [TOOL_CALL_STOP],
        "max_tokens": MAX_OUTPUT_TOKENS,
    }
    if tools:
        kwargs["tools"] = tools
    return client.chat.completions.create(**kwargs)
