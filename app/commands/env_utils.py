"""Shared helpers for reading and writing the .env file."""

from __future__ import annotations

import os

from app.config import get_env_path


def read_env() -> dict[str, str]:
    env: dict[str, str] = {}
    env_path = get_env_path()
    if not env_path.exists():
        return env
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        env[key.strip()] = val.strip()
    return env


def write_env(env: dict[str, str]) -> None:
    env_path = get_env_path()
    env_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}={v}" for k, v in env.items()]
    env_path.write_text("\n".join(lines) + "\n")


def reload_env(env: dict[str, str]) -> None:
    for k, v in env.items():
        os.environ[k] = v


def mask_key(key: str) -> str:
    if len(key) > 10:
        return key[:6] + "..." + key[-4:]
    return "***"
