"""VektorCode context window management.

Prevents ContextWindowExceededError by tracking token usage,
enforcing budgets, and compacting conversation history when
approaching model limits.

Architecture:

    User Input
         │
         ▼
    ContextManager
         │
         ├── TokenEstimator
         ├── ContextBudget
         └── Compactor
         │
         ▼
    Safe messages list for LLM
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any


# ── Token Estimation ────────────────────────────────────────────────

# Rough approximation: 1 token ≈ 4 characters for English text.
# This is intentionally conservative to avoid exceeding limits.
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Estimate token count from text. Clearly an approximation."""
    return max(1, len(text) // CHARS_PER_TOKEN)


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    """Estimate total tokens across a list of chat messages."""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            # Multi-part content (e.g., vision messages)
            for part in content:
                if isinstance(part, dict) and "text" in part:
                    total += estimate_tokens(part["text"])
        # Overhead per message (role, formatting)
        total += 4
    return total


# ── Model Context Configuration ─────────────────────────────────────

@dataclass(frozen=True)
class ModelConfig:
    """Context window configuration for a specific model."""

    name: str
    context_window: int = 32768
    max_output_tokens: int = 4096
    safety_margin: int = 1024

    @property
    def input_budget(self) -> int:
        """Maximum tokens available for input."""
        return self.context_window - self.max_output_tokens - self.safety_margin


# Known model configurations. Extend as needed.
_MODEL_CONFIGS: dict[str, ModelConfig] = {
    "qwen": ModelConfig(name="qwen", context_window=32768, max_output_tokens=4096, safety_margin=1024),
    "gemma": ModelConfig(name="gemma", context_window=8192, max_output_tokens=2048, safety_margin=512),
    "gpt-oss-120b": ModelConfig(name="gpt-oss-120b", context_window=131072, max_output_tokens=8192, safety_margin=2048),
    "nemotron": ModelConfig(name="nemotron", context_window=32768, max_output_tokens=4096, safety_margin=1024),
}


def get_model_config(model_name: str | None = None) -> ModelConfig:
    """Get context configuration for a model.

    Resolution order:
    1. Exact match in _MODEL_CONFIGS
    2. LITELLM_CONTEXT_WINDOW env override (explicit escape hatch)
    3. Longest known-key prefix match — deployment names carry suffixes
       ("nemotron-3-super", "qwen2.5-coder-32b"), and an exact-only lookup
       silently assigned them the 32K default, forcing needless compaction.
    4. Safe 32K default
    """
    name = model_name or os.getenv("LITELLM_MODEL", "qwen")

    exact = _MODEL_CONFIGS.get(name)
    if exact is not None:
        return exact

    override = os.getenv("LITELLM_CONTEXT_WINDOW", "").strip()
    if override.isdigit() and int(override) > 0:
        window = int(override)
        return ModelConfig(
            name=name,
            context_window=window,
            max_output_tokens=min(8192, max(1024, window // 8)),
        )

    lowered = name.lower()
    candidates = [k for k in _MODEL_CONFIGS if lowered.startswith(k.lower())]
    if candidates:
        best = max(candidates, key=len)
        return ModelConfig(
            name=name,
            context_window=_MODEL_CONFIGS[best].context_window,
            max_output_tokens=_MODEL_CONFIGS[best].max_output_tokens,
            safety_margin=_MODEL_CONFIGS[best].safety_margin,
        )

    return ModelConfig(name=name)


# ── Context Budget ──────────────────────────────────────────────────

@dataclass
class BudgetReport:
    """Snapshot of context budget status."""

    model: str
    context_window: int
    input_budget: int
    estimated_tokens: int
    utilization: float  # 0.0 – 1.0+
    is_safe: bool
    is_warning: bool  # 70-85%
    is_critical: bool  # 85-95%
    is_overflow: bool  # >95%

    @property
    def remaining(self) -> int:
        return max(0, self.input_budget - self.estimated_tokens)

    @property
    def status_label(self) -> str:
        if self.is_overflow:
            return "OVERFLOW"
        if self.is_critical:
            return "CRITICAL"
        if self.is_warning:
            return "WARNING"
        return "OK"

    def format_display(self) -> str:
        """Compact one-line display: 'Context 18.4k / 27.6k'"""
        est = f"{self.estimated_tokens / 1000:.1f}k"
        bud = f"{self.input_budget / 1000:.1f}k"
        return f"Context {est} / {bud}"


def check_budget(
    messages: list[dict[str, Any]],
    model_name: str | None = None,
) -> BudgetReport:
    """Check if messages fit within the model's context budget."""
    config = get_model_config(model_name)
    tokens = estimate_messages_tokens(messages)
    utilization = tokens / config.input_budget if config.input_budget > 0 else 1.0

    return BudgetReport(
        model=config.name,
        context_window=config.context_window,
        input_budget=config.input_budget,
        estimated_tokens=tokens,
        utilization=utilization,
        is_safe=utilization < 0.70,
        is_warning=0.70 <= utilization < 0.85,
        is_critical=0.85 <= utilization < 0.95,
        is_overflow=utilization >= 0.95,
    )


# ── Compaction ──────────────────────────────────────────────────────

def compact_history(
    messages: list[dict[str, Any]],
    target_tokens: int,
    model_name: str | None = None,
) -> list[dict[str, Any]]:
    """Reduce conversation history to fit within target token budget.

    Strategy:
    1. Always keep: system prompt (index 0)
    2. Always keep: last user message
    3. Compress older tool results and assistant messages
    4. Remove oldest LOW-priority content first

    Returns a new list (does not mutate input).
    """
    if not messages:
        return messages

    config = get_model_config(model_name)
    current_tokens = estimate_messages_tokens(messages)

    if current_tokens <= target_tokens:
        return list(messages)

    result = list(messages)
    # Index 0 is always the system prompt
    system_msg = result[0] if result else None
    remaining = result[1:] if len(result) > 1 else []

    # Phase 1: Truncate large tool results in-place
    remaining = _truncate_tool_outputs(remaining, max_output_chars=2000, preserve_recent=2)

    # Phase 2: If still over budget, remove oldest messages (keep system + last user)
    if estimate_messages_tokens([system_msg] + remaining) > target_tokens and len(remaining) > 2:
        # Find the last user message to always keep
        last_user_idx = len(remaining) - 1
        for i in range(len(remaining) - 1, -1, -1):
            if remaining[i].get("role") == "user":
                last_user_idx = i
                break

        # Remove oldest messages, keeping system, last user, and recent context
        kept_indices = {0}  # system always
        kept_indices.add(last_user_idx)

        # Keep the most recent messages that fit
        budget_remaining = target_tokens - estimate_messages_tokens([system_msg])
        recent: list[dict[str, Any]] = []
        for i in range(len(remaining) - 1, -1, -1):
            msg = remaining[i]
            msg_tokens = estimate_messages_tokens([msg])
            if budget_remaining - msg_tokens >= 0 or i == last_user_idx:
                recent.append(msg)
                budget_remaining -= msg_tokens
            else:
                break

        recent.reverse()
        remaining = recent

    final = []
    if system_msg:
        final.append(system_msg)
    final.extend(remaining)

    return final


# A preserved (recent) tool result is still capped, but generously — enough
# for a full ~500-line source file. Without this, a single huge result could
# still overflow the window.
PRESERVE_HARD_CAP_CHARS = 24000


def _truncate_tool_outputs(
    messages: list[dict[str, Any]],
    max_output_chars: int = 2000,
    preserve_recent: int = 0,
) -> list[dict[str, Any]]:
    """Truncate large tool results to save context.

    Only targets role:"tool" messages — never truncates assistant
    or user messages, as those contain reasoning and instructions.

    The most recent ``preserve_recent`` tool results are kept intact
    (up to PRESERVE_HARD_CAP_CHARS). Truncating the result the model
    just asked for makes it re-request the same data — it never sees
    the output it requested, so it pages through the file forever.
    """
    tool_indices = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    protected = set(tool_indices[-preserve_recent:]) if preserve_recent > 0 else set()

    result = []
    for i, msg in enumerate(messages):
        if msg.get("role") != "tool":
            result.append(msg)
            continue

        content = msg.get("content", "")
        if not isinstance(content, str):
            result.append(msg)
            continue

        limit = PRESERVE_HARD_CAP_CHARS if i in protected else max_output_chars
        if len(content) <= limit:
            result.append(msg)
            continue

        dropped = len(content) - limit
        truncated = (
            content[:limit]
            + f"\n\n[... truncated — {dropped} chars omitted from this tool result. "
            f"Do NOT re-request the same data; use search_code to locate specific "
            f"content instead.]"
        )
        result.append({**msg, "content": truncated})
    return result


# ── Context Manager ─────────────────────────────────────────────────

class ContextManager:
    """Manages context window budget for the agent.

    Tracks token usage, validates requests before sending,
    and compacts history when approaching limits.
    """

    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = model_name
        self._compaction_count = 0

    @property
    def config(self) -> ModelConfig:
        return get_model_config(self.model_name)

    def prepare_messages(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Prepare messages for LLM request.

        1. Check if messages fit in budget
        2. If not, compact history
        3. Return safe message list
        """
        report = check_budget(messages, self.model_name)

        if report.is_safe:
            return messages

        if report.is_critical or report.is_overflow:
            # Compact aggressively
            target = int(self.config.input_budget * 0.6)
            compacted = compact_history(messages, target, self.model_name)
            self._compaction_count += 1
            return compacted

        if report.is_warning:
            # Light compaction — truncate tool outputs only
            compacted = _truncate_tool_outputs(messages, max_output_chars=3000, preserve_recent=2)
            return compacted

        return messages

    def get_status(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        """Return current context status for display."""
        report = check_budget(messages, self.model_name)
        return {
            "model": report.model,
            "context_window": report.context_window,
            "input_budget": report.input_budget,
            "estimated_tokens": report.estimated_tokens,
            "utilization": report.utilization,
            "status": report.status_label,
            "compactions": self._compaction_count,
            "display": report.format_display(),
        }
