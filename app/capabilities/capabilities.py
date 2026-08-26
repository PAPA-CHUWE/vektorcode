"""Model capability detection for VektorCode.

Detects what capabilities the current LLM supports:
- vision: Can it process images/screenshots?
- tool_calling: Can it call tools via JSON?
- structured_output: Can it output structured data (JSON schema)?

This enables graceful degradation when a model lacks vision
(or other capabilities).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ModelCapabilities:
    """Capabilities of the current LLM."""

    vision: bool = False
    tool_calling: bool = True  # Default to True (VektorCode assumes tool calling)
    structured_output: bool = False
    max_context_length: int = 4096
    model_name: str = "unknown"

    def supports_vision(self) -> bool:
        """Check if the model can process images."""
        return self.vision

    def supports_tool_calling(self) -> bool:
        """Check if the model can call tools."""
        return self.tool_calling

    def supports_structured_output(self) -> bool:
        """Check if the model can output structured data."""
        return self.structured_output


class CapabilityDetector:
    """Detects capabilities of the current LLM."""

    # Known models that support vision
    KNOWN_VISION_MODELS = [
        "gpt-4-vision",
        "gpt-4o",
        "gpt-4o-mini",
        "claude-3",
        "gemini",
        "qwen-vl",
        "llava",
    ]

    # Known models that support structured output
    KNOWN_STRUCTURED_MODELS = [
        "gpt-4o",
        "gpt-4o-mini",
        "claude-3",
        "gemini",
    ]

    def __init__(self, model_name: str) -> None:
        """Initialize with a model name.

        Args:
            model_name: The model name (e.g., "gpt-4", "gemma", "qwen")
        """
        self.model_name = model_name
        self._capabilities: ModelCapabilities | None = None

    def detect(self) -> ModelCapabilities:
        """Detect capabilities based on model name.

        In the future, this could make an API call to the model
        to verify capabilities. For now, it uses heuristics.
        """
        if self._capabilities:
            return self._capabilities

        capabilities = ModelCapabilities(
            model_name=self.model_name,
            tool_calling=True,  # VektorCode uses tool calling by default
            max_context_length=4096,  # Default
        )

        model_lower = self.model_name.lower()

        # Detect vision capability
        for vision_model in self.KNOWN_VISION_MODELS:
            if vision_model.lower() in model_lower:
                capabilities.vision = True
                logger.info(f"Vision capability detected for model: {self.model_name}")
                break

        # Detect structured output
        for structured_model in self.KNOWN_STRUCTURED_MODELS:
            if structured_model.lower() in model_lower:
                capabilities.structured_output = True
                logger.info(f"Structured output capability detected for model: {self.model_name}")
                break

        self._capabilities = capabilities
        return capabilities

    def get_capabilities(self) -> ModelCapabilities:
        """Get the detected capabilities."""
        if self._capabilities is None:
            return self.detect()
        return self._capabilities

    def should_use_vision(self, capabilities: ModelCapabilities) -> bool:
        """Determine if vision should be used for a task.

        Returns True if the model supports vision AND vision
        would materially improve the task outcome.
        """
        if not capabilities.supports_vision():
            return False
        # Heuristic: use vision for UI-related tasks
        return True  # Could be more sophisticated in the future

    def degrade_gracefully(self, capabilities: ModelCapabilities) -> str:
        """Return a message explaining graceful degradation.

        When the model doesn't support vision, we can still use
        structured browser state (DOM, AX tree) instead.
        """
        if not capabilities.supports_vision():
            return (
                "Note: Your current model does not support vision. "
                "Browser tools will use structured state (DOM, accessibility tree) "
                "instead of screenshots."
            )
        return ""


# Global capability detector instance
default_detector: CapabilityDetector | None = None


def get_capability_detector(model_name: str) -> CapabilityDetector:
    """Get or create a capability detector for a model."""
    global default_detector
    if default_detector is None or default_detector.model_name != model_name:
        default_detector = CapabilityDetector(model_name)
    return default_detector
