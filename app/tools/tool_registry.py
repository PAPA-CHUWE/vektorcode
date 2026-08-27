"""Tool registry for VektorCode.

Single source of truth for all tools. Every tool is registered here
with metadata, permissions, category, and an executor function.

The registry supports:
- Registration with metadata
- O(1) lookup by name
- Category-based listing
- Enabled/disabled state
- Permission enforcement at execution time

Consumers:
- Agent → discovers and executes tools
- /tools → lists available tools
- LLM → generates tool schemas from registry
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from app.tools.tool_result import ToolResult, ToolStatus

logger = logging.getLogger(__name__)


@dataclass
class ToolDefinition:
    """Complete definition of a VektorCode tool."""

    name: str
    description: str
    category: str
    handler: Callable[..., str]
    parameters: dict[str, Any] = field(default_factory=dict)
    required_permissions: list[str] = field(default_factory=list)
    enabled: bool = True

    def to_llm_schema(self) -> dict[str, Any]:
        """Generate OpenAI-compatible function schema for LLM calls."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters or {
                    "type": "object",
                    "properties": {},
                },
            },
        }


class ToolRegistry:
    """Registry for VektorCode tools.

    Usage:
        registry = ToolRegistry()
        registry.register(
            name="read_file",
            description="Read file contents",
            category="filesystem",
            handler=read_file_fn,
            parameters={...},
        )
        tool = registry.get("read_file")
        result = registry.execute("read_file", {"path": "foo.py"})
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(
        self,
        name: str,
        description: str,
        category: str,
        handler: Callable[..., str],
        parameters: dict[str, Any] | None = None,
        required_permissions: list[str] | None = None,
        enabled: bool = True,
    ) -> None:
        """Register a tool. Overwrites if already registered."""
        if name in self._tools:
            logger.warning(f"Tool '{name}' already registered, overwriting")
        self._tools[name] = ToolDefinition(
            name=name,
            description=description,
            category=category,
            handler=handler,
            parameters=parameters or {},
            required_permissions=required_permissions or [],
            enabled=enabled,
        )
        logger.debug(f"Registered tool: {name} [{category}]")

    def unregister(self, name: str) -> None:
        """Remove a tool from the registry."""
        self._tools.pop(name, None)

    def get(self, name: str) -> ToolDefinition | None:
        """Get a tool definition by name. O(1) lookup."""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    def list_all(self) -> list[ToolDefinition]:
        """List all registered tools."""
        return list(self._tools.values())

    def list_available(self) -> list[ToolDefinition]:
        """List all enabled tools."""
        return [t for t in self._tools.values() if t.enabled]

    def list_by_category(self, category: str) -> list[ToolDefinition]:
        """List tools in a specific category."""
        return [t for t in self._tools.values() if t.category == category]

    def get_categories(self) -> list[str]:
        """Get sorted list of distinct categories."""
        return sorted({t.category for t in self._tools.values()})

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Execute a tool by name.

        Returns ToolResult with status, data, and error info.
        """
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult.make_error(f"Unknown tool: {name}")
        if not tool.enabled:
            return ToolResult.make_error(f"Tool '{name}' is disabled")
        try:
            result = tool.handler(**arguments)
            if isinstance(result, ToolResult):
                return result
            return ToolResult.success(data=result)
        except TypeError as exc:
            return ToolResult.make_error(f"Bad arguments for {name}: {exc}")
        except Exception as exc:
            return ToolResult.make_error(f"Tool {name} failed: {exc}")

    def to_llm_schemas(self) -> list[dict[str, Any]]:
        """Generate LLM tool schemas for all enabled tools."""
        return [t.to_llm_schema() for t in self.list_available()]


# ── Global registry instance ─────────────────────────────────────────

_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    """Get the global tool registry, creating and populating it if needed."""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
        _register_builtin_tools(_registry)
    return _registry


def _register_builtin_tools(registry: ToolRegistry) -> None:
    """Register all built-in VektorCode tools."""
    from app.tools.filesystem import list_files, read_file, write_file
    from app.tools.search import search_code
    from app.tools.terminal import run_command
    from app.tools.config import get_config
    from app.tools.git import handler as git_handler

    registry.register(
        name="list_files",
        description="List files and directories at path",
        category="filesystem",
        handler=list_files,
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path relative to workspace",
                    "default": ".",
                },
            },
        },
        required_permissions=["filesystem"],
    )

    registry.register(
        name="read_file",
        description="Read file contents",
        category="filesystem",
        handler=read_file,
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to workspace",
                },
            },
            "required": ["path"],
        },
        required_permissions=["filesystem"],
    )

    registry.register(
        name="write_file",
        description="Write or create files",
        category="filesystem",
        handler=write_file,
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to workspace",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write",
                },
            },
            "required": ["path", "content"],
        },
        required_permissions=["filesystem"],
    )

    registry.register(
        name="search_code",
        description="Search code with regex (like grep)",
        category="search",
        handler=search_code,
        parameters={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regex pattern to search for",
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search in",
                    "default": ".",
                },
                "include": {
                    "type": "string",
                    "description": "File glob pattern",
                    "default": "",
                },
            },
            "required": ["pattern"],
        },
        required_permissions=["search"],
    )

    registry.register(
        name="run_command",
        description="Execute shell commands",
        category="shell",
        handler=run_command,
        parameters={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute",
                },
                "working_directory": {
                    "type": "string",
                    "description": "Working directory",
                    "default": ".",
                },
            },
            "required": ["command"],
        },
        required_permissions=["shell"],
    )

    registry.register(
        name="git_tool",
        description="Run git operations: status, diff, log, add, commit, branch, push, pull, merge",
        category="git",
        handler=git_handler,
        parameters={
            "type": "object",
            "properties": {
                "subcommand": {
                    "type": "string",
                    "description": "One of: status, diff, log, add, commit, branch, push, pull, merge",
                },
                "path": {
                    "type": "string",
                    "description": "Path scoped to workspace, for diff/add",
                    "default": ".",
                },
                "staged": {
                    "type": "boolean",
                    "description": "For diff: show staged changes",
                    "default": False,
                },
                "max_count": {
                    "type": "integer",
                    "description": "For log: limit number of commits shown",
                },
                "message": {
                    "type": "string",
                    "description": "Commit message (required for commit)",
                },
                "branch_name": {
                    "type": "string",
                    "description": "Branch name (for branch/merge)",
                },
                "switch_only": {
                    "type": "boolean",
                    "description": "For branch: switch to existing branch instead of creating a new one",
                    "default": False,
                },
            },
            "required": ["subcommand"],
        },
        required_permissions=["git"],
    )

    registry.register(
        name="get_config",
        description="Show current configuration",
        category="config",
        handler=get_config,
        parameters={
            "type": "object",
            "properties": {},
        },
        required_permissions=["config"],
    )


def format_tools_for_prompt(registry: "ToolRegistry") -> str:
    """Render every enabled tool as a numbered prompt line.

    The system prompt is the model's only source of truth about what tools
    exist — this project uses textual <tool_call> blocks, not the OpenAI
    `tools=` parameter. Hand-maintaining that list meant registering a tool
    updated the registry (and the status bar) while the model went on
    believing the old set. Generating it here keeps the two in step.

    Uses the `description` and `parameters` already supplied at registration,
    so there is no second place to keep current.
    """
    lines = ["Available tools:", ""]
    for index, tool in enumerate(registry.list_available(), start=1):
        schema = tool.parameters or {}
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))

        parts: list[str] = []
        for name, spec in properties.items():
            is_string = spec.get("type") == "string"
            if name in required:
                example = '"..."' if is_string else "..."
            else:
                default = spec.get("default")
                if default is None:
                    example = '""' if is_string else "..."
                elif isinstance(default, str):
                    example = f'"{default}"'
                elif isinstance(default, bool):
                    example = "true" if default else "false"
                else:
                    example = str(default)
            parts.append(f"{name}={example}")

        lines.append(f"{index}. {tool.name}({', '.join(parts)}) → {tool.description}")
    return "\n".join(lines)
