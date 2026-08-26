"""Model listing command: /models."""

from __future__ import annotations

from app.commands.env_utils import read_env


def cmd_models(args: list[str]) -> str:
    """List available models from the LLM endpoint."""
    from openai import OpenAI

    env = read_env()
    api_key = env.get("LITELLM_API_KEY", "no-key")
    base_url = env.get("LITELLM_BASE_URL", "http://localhost:30080")

    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        models = client.models.list()
        if not models.data:
            return "No models available."
        lines = [f"  {m.id}" for m in models.data]
        return "Available models:\n" + "\n".join(lines)
    except Exception as exc:
        return f"Error fetching models: {exc}"
