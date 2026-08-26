"""Dynamic activity language engine for VektorCode.

Generates contextual activity phrases from:
- Current operation (tool type)
- Target/context (file paths, patterns, commands)
- Execution stage (state)
- Domain vocabulary (physics, math, systems, chemistry, etc.)

No fixed vocabulary lists - phrases are composed from domain-specific
templates and contextual fragments. The system produces novel labels
that are always semantically relevant to the current operation.

Architecture:

    AgentEvent
         │
         ▼
    ActivityEngine.resolve(event)
         │
         ├── state classification
         ├── target/context extraction
         ├── phrase composition
         │       ├── verb selection (operation-appropriate)
         │       ├── target context (file, pattern, command)
         │       └── object (domain-noun)
         ├── flavor injection (30% chance for creative variation)
         └── Activity(state, label, detail)
         │
         ▼
    UI renders: "◐ VektorCode · tracing the dependency chain"

Key constraints:
- Semantic relevance > creativity
- 2-7 word phrases
- Event-driven (tool/state changes only)
- No LLM calls
- Always readable, never confusing
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ActivityState(str, Enum):
    """Semantic states the agent can be in."""

    REASONING = "reasoning"
    SCANNING = "scanning"
    READING = "reading"
    SEARCHING = "searching"
    EDITING = "editing"
    EXECUTING = "executing"
    TESTING = "testing"
    COMPOSING = "composing"
    COMPLETED = "completed"
    ERROR = "error"


# Tool → state mapping
_TOOL_STATE_MAP: dict[str, ActivityState] = {
    "list_files": ActivityState.SCANNING,
    "read_file": ActivityState.READING,
    "write_file": ActivityState.EDITING,
    "search_code": ActivityState.SEARCHING,
    "run_command": ActivityState.EXECUTING,
    "get_config": ActivityState.READING,
}


@dataclass
class Activity:
    """A resolved activity label for display."""

    state: ActivityState
    label: str
    detail: str = ""

    def format_display(self) -> str:
        """Full display string: '◐ VektorCode · triangulating'"""
        icon = "✓" if self.state == ActivityState.COMPLETED else "◐"
        text = f"{icon} VektorCode · {self.label}"
        if self.detail:
            text += f"\n  └─ {self.detail}"
        return text


class _ActivityBuilder:
    """Composes activity phrases from contextual fragments.

    Each operation is mapped to a state. For each state, we define:
    - verbs: action words appropriate to the operation
    - objects: domain-nouns that pair with those verbs
    - flavor: creative physics/math/systems metaphors for occasional variation

    The phrase is built as: [verb] [target_context] [object]
    or from flavor: [verb] [object] (e.g., "collapsing the search space")

    Target context is extracted from tool arguments (file paths, patterns, commands).
    """

    # Verbs: action words per state
    _VERBS: dict[ActivityState, list[str]] = {
        ActivityState.REASONING: [
            "triangulating", "correlating", "deducing", "synthesizing",
            "converging", "decomposing", "extrapolating", "hypothesizing",
            "mapping", "tracing", "analyzing", "probing",
        ],
        ActivityState.SCANNING: [
            "scanning", "indexing", "mapping", "surveying",
            "enumerating", "cataloging", "discovering", "probing",
        ],
        ActivityState.READING: [
            "inspecting", "examining", "parsing", "extracting",
            "reviewing", "analyzing", "decoding", "absorbing",
        ],
        ActivityState.SEARCHING: [
            "tracing", "searching", "retrieving", "distilling",
            "cross-referencing", "narrowing", "probing", "hunting",
            "tracking", "pinpointing",
        ],
        ActivityState.EDITING: [
            "patching", "refactoring", "instrumenting", "reshaping",
            "rewiring", "transforming", "crafting", "forging",
            "engineering", "structuring", "building",
        ],
        ActivityState.EXECUTING: [
            "executing", "running", "invoking", "launching",
            "dispatching", "probing", "testing", "exercising",
        ],
        ActivityState.TESTING: [
            "probing", "validating", "benchmarking", "asserting",
            "verifying", "stress-testing", "pressing", "challenging",
        ],
        ActivityState.COMPOSING: [
            "synthesizing", "crystallizing", "assembling", "formulating",
            "drafting", "materializing", "consolidating",
        ],
        ActivityState.COMPLETED: [
            "converged", "resolved", "verified", "completed",
        ],
        ActivityState.ERROR: [
            "interrupted", "blocked", "stalled",
        ],
    }

    # Objects: domain-nouns per state
    _OBJECTS: dict[ActivityState, list[str]] = {
        ActivityState.REASONING: [
            "the problem", "the solution", "the logic", "the context",
            "the dependencies", "the architecture", "the flow",
            "the constraints", "the design", "the strategy",
        ],
        ActivityState.SCANNING: [
            "the filesystem", "the directory", "the workspace", "the tree",
            "the structure", "the layout", "the hierarchy",
        ],
        ActivityState.READING: [
            "the source", "the file", "the implementation", "the codebase",
            "the module", "the function", "the class", "the definition",
            "the specification", "the signature",
        ],
        ActivityState.SEARCHING: [
            "the pattern", "the dependency", "the reference", "the symbol",
            "the call site", "the implementation", "the usage",
            "the definition", "the boundary", "the chain",
        ],
        ActivityState.EDITING: [
            "the implementation", "the module", "the component", "the file",
            "the interface", "the structure", "the layer", "the system",
            "the feature", "the endpoint", "the route",
        ],
        ActivityState.EXECUTING: [
            "the command", "the script", "the process", "the job",
            "the build", "the test", "the deployment", "the pipeline",
            "the task", "the routine", "the operation",
        ],
        ActivityState.TESTING: [
            "the test suite", "the validation", "the assertion", "the boundary",
            "the state", "the edge case", "the integration", "the contract",
            "the behavior", "the system",
        ],
        ActivityState.COMPOSING: [
            "the response", "the solution", "the patch", "the summary",
            "the output", "the report", "the deliverable",
        ],
    }

    # Flavor: creative domain metaphors (physics, math, chemistry, systems)
    # These are full phrases for occasional injection
    _FLAVOR: dict[ActivityState, list[tuple[str, str]]] = {
        ActivityState.REASONING: [
            ("collapsing", "the search space"),
            ("perturbing", "the state space"),
            ("projecting", "the solution path"),
            ("vectorizing", "the context"),
            ("triangulating", "the problem space"),
            ("orbiting", "the problem space"),
            ("calibrating", "the approach"),
            ("resonating", "the components"),
            ("reducing", "the complexity"),
            ("converging", "on the solution"),
        ],
        ActivityState.SCANNING: [
            ("indexing", "the filesystem"),
            ("surveying", "the directory"),
            ("mapping", "the structure"),
            ("cataloging", "the contents"),
            ("enumerating", "the entries"),
            ("discovering", "the layout"),
            ("traversing", "the tree"),
        ],
        ActivityState.READING: [
            ("decoding", "the implementation"),
            ("analyzing", "the structure"),
            ("absorbing", "the context"),
            ("dissecting", "the codebase"),
            ("unpacking", "the module"),
            ("parsing", "the source"),
            ("examining", "the definition"),
            ("inspecting", "the signature"),
        ],
        ActivityState.SEARCHING: [
            ("tracing", "the dependency chain"),
            ("excavating", "the reference"),
            ("probing", "the boundary"),
            ("mining", "the pattern"),
            ("tracing", "the call graph"),
            ("mapping", "the topology"),
            ("tracking", "the symbol"),
            ("pinpointing", "the definition"),
            ("cross-referencing", "the usage"),
            ("distilling", "the relevant context"),
        ],
        ActivityState.EDITING: [
            ("crystallizing", "the implementation"),
            ("forging", "the patch"),
            ("weaving", "the changes"),
            ("reconciling", "the modifications"),
            ("structuring", "the system"),
            ("orchestrating", "the components"),
            ("engineering", "the feature"),
            ("rewiring", "the dependency"),
            ("transforming", "the codebase"),
            ("instrumenting", "the module"),
            ("patching", "the implementation"),
            ("refactoring", "the structure"),
        ],
        ActivityState.EXECUTING: [
            ("launching", "the process"),
            ("triggering", "the chain reaction"),
            ("exercising", "the pipeline"),
            ("probing", "the execution boundary"),
            ("stress-testing", "the system"),
            ("running", "the experiment"),
            ("dispatching", "the command"),
            ("invoking", "the process"),
        ],
        ActivityState.TESTING: [
            ("probing", "the failure boundary"),
            ("perturbing", "the state space"),
            ("stress-testing", "the contract"),
            ("validating", "the invariants"),
            ("examining", "the edge cases"),
            ("asserting", "the preconditions"),
            ("challenging", "the implementation"),
            ("benchmarking", "the performance"),
            ("pressing", "the boundaries"),
            ("verifying", "the behavior"),
        ],
        ActivityState.COMPOSING: [
            ("synthesizing", "the response"),
            ("crystallizing", "the solution"),
            ("materializing", "the output"),
            ("consolidating", "the findings"),
            ("formulating", "the answer"),
            ("drafting", "the report"),
        ],
    }

    def build(self, state: ActivityState, target: str = "") -> Activity:
        """Build an activity label from state and target context.

        Strategy:
        1. 40% chance: use a flavor phrase (creative domain metaphor)
        2. 60% chance: compose from verb + target_context + object

        Target context is extracted from tool arguments to make phrases
        specific to the current operation (e.g., a file path or pattern).
        """
        # Use flavor phrases for creative variation
        flavor_options = self._FLAVOR.get(state, [])
        if flavor_options and random.random() < 0.4:
            verb, obj = random.choice(flavor_options)
            return Activity(state=state, label=f"{verb} {obj}")

        # Compose from components
        verb = self._pick_random(self._VERBS.get(state, ["working"]))
        object_options = self._OBJECTS.get(state, ["the problem"])

        # Extract target context from tool arguments
        target_context = self._extract_target_context(state, target)

        if target_context:
            # State-specific target formatting
            obj_template = self._state_object_template(state)
            return Activity(
                state=state,
                label=f"{verb} {target_context} {obj_template}",
                detail=target,
            )
        else:
            obj = self._pick_random(object_options)
            return Activity(state=state, label=f"{verb} {obj}")

    def _pick_random(self, options: list[str]) -> str:
        """Pick a random option, avoiding the last one if possible."""
        if len(options) <= 1:
            return options[0]
        return random.choice(options)

    def _extract_target_context(self, state: ActivityState, target: str) -> str:
        """Extract contextual information from the target string.

        Returns a formatted context string or empty string if nothing to show.
        """
        if not target:
            return ""

        # File-related operations
        if state in (ActivityState.READING, ActivityState.EDITING):
            return self._parse_file_path(target)

        # Search operations
        if state == ActivityState.SEARCHING:
            return self._parse_search_target(target)

        # Command execution
        if state == ActivityState.EXECUTING:
            return self._parse_command(target)

        return ""

    def _state_object_template(self, state: ActivityState) -> str:
        """Get a default object for the current state."""
        objects = self._OBJECTS.get(state, ["the problem"])
        # Return the first object or a default
        return objects[0] if objects else "the problem"

    def _parse_file_path(self, target: str) -> str:
        """Extract meaningful file path context.

        Shows the most relevant path components (last 2-3 levels).
        """
        try:
            from pathlib import PurePath

            path = PurePath(target)
            parts = list(path.parts)
            if len(parts) > 3:
                return f"{parts[-3]}/{parts[-2]}/{parts[-1]}"
            return target
        except Exception:
            return target

    def _parse_search_target(self, target: str) -> str:
        """Extract search query context.

        Target format: '"pattern" in *.py (dir)'
        We extract the pattern string.
        """
        pattern_match = re.search(r'"([^"]+)"', target)
        if pattern_match:
            pattern = pattern_match.group(1)
            # Truncate long patterns
            if len(pattern) > 20:
                pattern = pattern[:17] + "..."
            return f'pattern "{pattern}"'
        return target

    def _parse_command(self, target: str) -> str:
        """Extract command context.

        Truncates long commands to keep phrases readable.
        """
        if len(target) > 40:
            return target[:37] + "..."
        return target


class ActivityEngine:
    """Resolves agent events into contextual activity labels.

    This is the public API. The engine is stateless between events
    (except for label variation tracking). Callers invoke the appropriate
    resolve method based on the event type.

    Usage:
        engine = ActivityEngine()

        # Tool start event
        activity = engine.resolve_tool_start("search_code", '"auth" in *.py')
        print(activity.format_display())
        # → "◐ VektorCode · tracing pattern \"auth\" the dependency chain"

        # Thinking event
        activity = engine.resolve_thinking()
        print(activity.format_display())
        # → "◐ VektorCode · triangulating the problem"

        # Completed event
        activity = engine.resolve_completed()
        print(activity.format_display())
        # → "✓ VektorCode · converged"
    """

    def __init__(self) -> None:
        self._builder = _ActivityBuilder()

    def resolve_tool_start(self, tool: str, target: str = "") -> Activity:
        """Resolve activity for a tool_start event."""
        state = _TOOL_STATE_MAP.get(tool, ActivityState.REASONING)
        return self._builder.build(state, target)

    def resolve_thinking(self) -> Activity:
        """Resolve activity for agent reasoning (no tool call)."""
        return self._builder.build(ActivityState.REASONING)

    def resolve_completed(self) -> Activity:
        """Resolve activity for task completion."""
        return self._builder.build(ActivityState.COMPLETED)

    def resolve_error(self) -> Activity:
        """Resolve activity for error state."""
        return self._builder.build(ActivityState.ERROR)
