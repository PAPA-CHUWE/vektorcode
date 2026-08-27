"""Model listing command: /models.

Lists the configured endpoint's models, and — when a local server is
reachable — the locally-served ones alongside them, so switching to a local
model does not require knowing its exact identifier.
"""

from __future__ import annotations

import os

from app.commands.env_utils import read_env

# Where local OpenAI-compatible servers usually listen. Probed only when a
# local endpoint has not been configured explicitly.
_LOCAL_CANDIDATES = (
    ("ollama", "http://127.0.0.1:11434/v1"),
    ("vllm", "http://127.0.0.1:8000/v1"),
    ("lm-studio", "http://127.0.0.1:1234/v1"),
)

# Probes must be short: /models should not hang because nothing is serving.
_PROBE_TIMEOUT = 1.5


def _fetch(base_url: str, api_key: str, timeout: float) -> list[str]:
    """Model ids from an OpenAI-compatible endpoint, or [] if unreachable."""
    from openai import OpenAI

    try:
        client = OpenAI(api_key=api_key or "no-key", base_url=base_url, timeout=timeout)
        return [m.id for m in client.models.list().data]
    except Exception:
        return []


def local_endpoint(env: dict[str, str] | None = None) -> str | None:
    """The configured local endpoint, if one is set.

    VEKTOR_LOCAL_ENDPOINT in the environment or .env, so switching to a
    local model is one /endpoint away rather than a retyped URL.
    """
    env = env if env is not None else read_env()
    value = (os.getenv("VEKTOR_LOCAL_ENDPOINT") or env.get("VEKTOR_LOCAL_ENDPOINT") or "").strip()
    return value or None


def discover_local(env: dict[str, str] | None = None) -> list[tuple[str, str, list[str]]]:
    """Local servers that answered, as (label, base_url, model_ids)."""
    env = env if env is not None else read_env()
    configured = local_endpoint(env)
    candidates = (
        [("configured", configured)] if configured else list(_LOCAL_CANDIDATES)
    )

    found: list[tuple[str, str, list[str]]] = []
    for label, base_url in candidates:
        models = _fetch(base_url, "local", _PROBE_TIMEOUT)
        if models:
            found.append((label, base_url, models))
    return found


def cmd_models(args: list[str]) -> str:
    """List models from the configured endpoint, plus any served locally."""
    env = read_env()
    api_key = env.get("LITELLM_API_KEY", "no-key")
    base_url = env.get("LITELLM_BASE_URL", "http://localhost:30080")

    sections: list[str] = []

    remote = _fetch(base_url, api_key, 10.0)
    if remote:
        sections.append("Available models:\n" + "\n".join(f"  {m}" for m in remote))
    else:
        sections.append(f"No models from {base_url} (unreachable or empty).")

    for label, url, models in discover_local(env):
        # Don't list the same server twice under two names.
        if url.rstrip("/") == base_url.rstrip("/"):
            continue
        sections.append(
            f"Local ({label} · {url}):\n"
            + "\n".join(f"  {m}" for m in models)
            + f"\n  → /endpoint {url}  then  /model <name>"
        )

    return "\n\n".join(sections)
