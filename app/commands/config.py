"""Configuration commands: /auth, /model, /endpoint, /help."""

from __future__ import annotations

from app.commands.env_utils import mask_key, read_env, reload_env, write_env
from app.config import get_auth_provider


def cmd_auth(args: list[str]) -> str:
    """Show, set, or clear the API key via the auth provider."""
    provider = get_auth_provider()

    if not args:
        if provider.is_authenticated():
            creds = provider.get_credentials()
            masked = mask_key(creds.api_key)
            return f"Provider: {provider.name}\n  API Key: {masked}"
        return f"Provider: {provider.name}\n  Status: not authenticated"

    if args[0] == "clear":
        provider.logout()
        # Also clear from .env
        env = read_env()
        env.pop("LITELLM_API_KEY", None)
        write_env(env)
        return "API Key cleared."

    # Set new key
    provider.login(api_key=args[0])
    # Also persist to .env
    env = read_env()
    env["LITELLM_API_KEY"] = args[0]
    write_env(env)
    return f"API Key set to: {mask_key(args[0])}"


def cmd_model(args: list[str]) -> str:
    """Show or switch the current model."""
    env = read_env()
    current = env.get("LITELLM_MODEL", "(not set)")

    if not args:
        return f"Current model: {current}"

    env["LITELLM_MODEL"] = args[0]
    write_env(env)
    reload_env(env)
    return f"Model switched to: {args[0]}"


def cmd_endpoint(args: list[str]) -> str:
    """Show or set the LLM base URL.

    `/endpoint local` switches to VEKTOR_LOCAL_ENDPOINT, or to a discovered
    local server, so moving to a locally-served model does not mean
    retyping a URL every session.
    """
    from app.commands.models import discover_local, local_endpoint

    env = read_env()
    current = env.get("LITELLM_BASE_URL", "(not set)")

    if not args:
        configured = local_endpoint(env)
        suffix = f"\nLocal shortcut: {configured}" if configured else ""
        return f"Endpoint: {current}{suffix}"

    target = args[0]
    if target.lower() == "local":
        target = local_endpoint(env)
        if not target:
            discovered = discover_local(env)
            if not discovered:
                return (
                    "No local endpoint configured or reachable. Set "
                    "VEKTOR_LOCAL_ENDPOINT in your .env, or start a local server."
                )
            target = discovered[0][1]

    env["LITELLM_BASE_URL"] = target
    write_env(env)
    reload_env(env)
    return f"Endpoint set to: {target}"


def cmd_reload(args: list[str]) -> str:
    """Reload configuration from .env and report current settings."""
    env = read_env()
    reload_env(env)
    api_key = env.get("LITELLM_API_KEY", "")
    model = env.get("LITELLM_MODEL", "(not set)")
    endpoint = env.get("LITELLM_BASE_URL", "(not set)")
    return (
        "Configuration reloaded:\n"
        f"  API Key:   {mask_key(api_key) if api_key else '(not set)'}\n"
        f"  Model:     {model}\n"
        f"  Endpoint:  {endpoint}"
    )


def cmd_help(args: list[str]) -> str:
    """List all available commands."""
    return """Built-in commands:
  /auth [key|clear]   Show, set, or clear the API key
  /model [name]       Show or switch the active model
  /models             List available models from the endpoint
  /endpoint [url]     Show or set the LLM base URL
  /reload             Reload config from .env and show status
  /status             Show session status (uptime)
  /clear              Clear conversation history
  /help               Show this help message
  exit / quit         Exit VektorCode"""
