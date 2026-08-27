"""Per-model token pricing.

Local models are free per token — you pay for the GPU, not the request —
so the table defaults them to $0. Metered hosted endpoints are configured
through environment variables rather than hardcoded rates, which go stale.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# One million tokens; rates are quoted per 1M by every provider.
_PER_MILLION = 1_000_000


@dataclass(frozen=True)
class ModelPricing:
    """Token rates for a model, in USD per 1M tokens."""

    name: str
    input_per_1m: float = 0.0
    output_per_1m: float = 0.0


# Self-hosted models. Free per token by definition.
_MODEL_PRICING: dict[str, ModelPricing] = {
    "qwen": ModelPricing(name="qwen"),
    "gemma": ModelPricing(name="gemma"),
    "gpt-oss-120b": ModelPricing(name="gpt-oss-120b"),
    "nemotron": ModelPricing(name="nemotron"),
}


def _env_float(name: str) -> float | None:
    """Read a non-negative float from the environment, or None."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


def get_model_pricing(model_name: str | None = None) -> ModelPricing:
    """Resolve pricing for a model.

    Order:
    1. LITELLM_INPUT_COST_PER_1M / LITELLM_OUTPUT_COST_PER_1M — wins for any
       model, so a metered endpoint can be priced without code changes.
    2. Exact match in the table.
    3. Longest known-key prefix match, for deployment names that carry
       suffixes ("qwen2.5-coder-32b", "nemotron-3-super").
    4. Free.
    """
    name = model_name or os.getenv("LITELLM_MODEL", "") or "unknown"

    env_input = _env_float("LITELLM_INPUT_COST_PER_1M")
    env_output = _env_float("LITELLM_OUTPUT_COST_PER_1M")
    if env_input is not None or env_output is not None:
        return ModelPricing(
            name=name,
            input_per_1m=env_input or 0.0,
            output_per_1m=env_output or 0.0,
        )

    exact = _MODEL_PRICING.get(name)
    if exact is not None:
        return exact

    lowered = name.lower()
    candidates = [k for k in _MODEL_PRICING if lowered.startswith(k.lower())]
    if candidates:
        best = _MODEL_PRICING[max(candidates, key=len)]
        return ModelPricing(
            name=name,
            input_per_1m=best.input_per_1m,
            output_per_1m=best.output_per_1m,
        )

    return ModelPricing(name=name)


def estimate_cost(
    model_name: str | None,
    input_tokens: int,
    output_tokens: int,
) -> float:
    """Estimated USD cost of one call. Returns 0.0 for free models."""
    pricing = get_model_pricing(model_name)
    if not pricing.input_per_1m and not pricing.output_per_1m:
        return 0.0
    return (
        (max(0, input_tokens) / _PER_MILLION) * pricing.input_per_1m
        + (max(0, output_tokens) / _PER_MILLION) * pricing.output_per_1m
    )
