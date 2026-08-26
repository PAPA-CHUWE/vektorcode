"""VektorCode agent – orchestrates LLM calls and tool execution.

The agent yields structured AgentEvent objects. The UI layer consumes
these events and decides how to render them. The agent itself contains
zero terminal formatting code.

Orchestration flow:

    USER message
        ↓
    LLM response
        ↓
    ┌── tool call? → execute ONE tool → result as role:"tool" → LLM
    └── no tool call → final response → STOP
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterator

from app.config import get_workspace
from app.context import ContextManager
from app.llm import chat
from app.tools import get_registry, get_event_bus
from app.tools.events import ToolEvent, ToolEventType
from app.tools.tool_result import ToolStatus

MAX_ITERATIONS = 15
_REPEAT_THRESHOLD = 3

logger = logging.getLogger("vektorcode.agent")

SYSTEM_PROMPT_TEMPLATE = """\
You are VektorCode, an AI coding agent.

Your job is to help developers understand, create, modify, test, debug, and improve software projects.

Active workspace: {workspace}

BEHAVIOR RULES — FOLLOW THESE EXACTLY:
- NEVER ask clarifying questions. The user's instruction is your directive.
- If an instruction is ambiguous, pick the most reasonable interpretation and execute it immediately.
- If you need more information, use your tools to discover it (list_files, read_file, search_code) rather than asking the user.
- Be direct and decisive. Give answers, don't ask questions.
- Start working immediately. No preamble, no "Sure!", no "I'd be happy to help". Just do the work.
- When done, give a concise result. One or two sentences max for simple tasks.

You have access to tools for:
- inspecting files
- reading source code
- creating and modifying files
- executing project commands
- searching code across the workspace
- checking your own configuration

Available tools:

1. list_files(path=".") → list files/dirs at path
2. read_file(path="file.txt") → read file contents
3. write_file(path="file.txt", content="...") → write content to file
4. search_code(pattern="regex", path=".", include="*.py") → search file contents (like grep)
5. run_command(command="ls -la", working_directory=".", confirm=false) → run shell command
6. get_config() → show current model, endpoint, API key status

To use a tool, output EXACTLY this format (one call per block):

<tool_call>
{{"name": "list_files", "arguments": {{"path": "."}}}}
</tool_call>

IMPORTANT: The format MUST be valid JSON with "name" and "arguments" keys.
Do NOT use Python set syntax like {{"tool_name", {{args}}}}.
Always use: {{"name": "tool_name", "arguments": {{"key": "value"}}}}

Output at most ONE <tool_call> block per response.
After receiving a tool result, reason about it before calling another tool.
When done, give your final answer WITHOUT any <tool_call> blocks.

TOOL USAGE RULES:
- When the user asks about your model, configuration, endpoint, or API key, ALWAYS call get_config() first.
- When the user asks about project files, ALWAYS call list_files() first.
- When searching for code patterns, use search_code() instead of reading files one by one.
- Understand the project before making changes.
- Make the smallest appropriate changes.
- Run tests or validation after changes.
- Do not modify unrelated files.
- Do not claim something works unless you validated it.
- Ask for confirmation before destructive operations (rm, git reset --hard, etc.).
"""

TOOL_CALL_PATTERN = re.compile(
    r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL
)

TOOL_LABELS: dict[str, str] = {
    "list_files": "Inspecting",
    "read_file": "Reading",
    "write_file": "Editing",
    "search_code": "Searching",
    "run_command": "Running",
    "get_config": "Checking config",
}

TOOL_STYLE: dict[str, str] = {
    "list_files": "file.read",
    "read_file": "file.read",
    "write_file": "file.modified",
    "search_code": "blue",
    "run_command": "cmd",
    "get_config": "blue",
}


@dataclass
class AgentEvent:
    """A structured event emitted by the agent for the UI to render."""

    type: str
    tool: str = ""
    target: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    message: str = ""


def _extract_json_from_tags(text: str) -> list[dict[str, Any]]:
    """Extract JSON objects from <tool_call>...</tool_call> blocks.

    Uses brace-matching to correctly handle nested JSON objects
    in tool arguments (e.g., write_file with nested content).
    """
    calls: list[dict[str, Any]] = []
    tag_start = "<tool_call>"
    tag_end = "</tool_call>"

    search_from = 0
    while True:
        start = text.find(tag_start, search_from)
        if start == -1:
            break
        content_start = start + len(tag_start)

        # Find the matching </  tag by brace-matching the JSON
        end = text.find(tag_end, content_start)
        if end == -1:
            break

        raw = text[content_start:end].strip()
        search_from = end + len(tag_end)

        # Try direct JSON parse first
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict) and "name" in parsed:
                calls.append(parsed)
                continue
        except json.JSONDecodeError:
            pass

        # Try brace-matching to find the JSON object boundary
        json_obj = _extract_balanced_json(raw)
        if json_obj is not None:
            try:
                parsed = json.loads(json_obj)
                if isinstance(parsed, dict) and "name" in parsed:
                    calls.append(parsed)
                    continue
            except json.JSONDecodeError:
                pass

        # Fallback: regex for {"name": ..., "arguments": ...}
        m = re.search(r'\{\s*"name"\s*:\s*"(\w+)"', raw)
        if m:
            name = m.group(1)
            # Try to extract arguments object
            args_match = re.search(r'"arguments"\s*:\s*(\{.*\})', raw, re.DOTALL)
            args: dict[str, Any] = {}
            if args_match:
                try:
                    args = json.loads(args_match.group(1))
                except json.JSONDecodeError:
                    pass
            calls.append({"name": name, "arguments": args})

    return calls


def _extract_balanced_json(text: str) -> str | None:
    """Extract a balanced JSON object from text using brace matching.

    Finds the first '{' and matches braces to find the complete object.
    Returns the JSON string or None if no balanced object found.
    """
    depth = 0
    in_string = False
    escape = False
    start = -1

    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == '\\' and in_string:
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue

        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start >= 0:
                return text[start:i + 1]

    return None


def _extract_bare_json(text: str) -> dict[str, Any] | None:
    """Extract a bare JSON tool call (no <tool_call> tags).

    Looks for a JSON object with "name" and "arguments" keys
    as a standalone line or the entire response.
    """
    # Try the entire text as JSON first
    try:
        parsed = json.loads(text.strip())
        if isinstance(parsed, dict) and "name" in parsed:
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass

    # Try line-by-line
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith('{'):
            continue
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, dict) and "name" in parsed and "arguments" in parsed:
                return parsed
        except (json.JSONDecodeError, ValueError):
            continue

    return None


def _call_signature(name: str, args: dict[str, Any]) -> str:
    """Create a normalized signature for a tool call.

    Used for loop detection — same tool + same args = same signature.
    """
    return json.dumps({"name": name, "arguments": args}, sort_keys=True)


class Agent:
    """Maintains conversation history and drives the LLM tool loop.

    The step() method yields AgentEvent objects. The caller consumes them
    to render the UI. The agent itself never touches the terminal.

    Orchestration:
        - One tool call per LLM cycle (execute → result → LLM)
        - Tool results use role:"tool" (never role:"user")
        - Repeated tool calls are detected and terminated
        - MAX_ITERATIONS is a safety fallback, not the normal path
    """

    def __init__(self) -> None:
        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(workspace=str(get_workspace()))
        self.history: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt}
        ]
        self.total_tokens: int = 0
        self.context_manager = ContextManager()

    def clear_history(self) -> None:
        """Reset conversation to just the system prompt."""
        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(workspace=str(get_workspace()))
        self.history = [{"role": "system", "content": system_prompt}]

    def _execute_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Execute a tool via the registry and emit ToolEvents."""
        import time

        registry = get_registry()
        event_bus = get_event_bus()
        tool_def = registry.get(name)
        category = tool_def.category if tool_def else ""

        # Emit TOOL_STARTED
        event_bus.emit(ToolEvent.started(name, category, self._tool_target(name, arguments)))

        start = time.monotonic()
        result = registry.execute(name, arguments)
        duration = time.monotonic() - start

        # Emit TOOL_COMPLETED or TOOL_FAILED
        if result.status == ToolStatus.ERROR:
            event_bus.emit(ToolEvent.failed(name, category, duration, result.error or ""))
        elif result.status == ToolStatus.BLOCKED:
            event_bus.emit(ToolEvent.failed(name, category, duration, result.error or "blocked"))
        else:
            event_bus.emit(ToolEvent.completed(name, category, duration))

        return str(result)

    def _extract_tool_calls(self, text: str) -> list[dict[str, Any]]:
        """Parse tool call from the model's response.

        Handles:
        1. <tool_call>{"name": ...}</tool_call> (primary format)
        2. {"name": "...", "arguments": {...}} (bare JSON fallback)
        """
        # Format 1: <tool_call> blocks
        calls = _extract_json_from_tags(text)
        if calls:
            return calls

        # Format 2: bare JSON
        bare = _extract_bare_json(text)
        if bare:
            return [bare]

        return []

    def _tool_target(self, name: str, args: dict[str, Any]) -> str:
        """Extract a human-readable target string from tool arguments."""
        if name in ("read_file", "write_file"):
            return args.get("path", "")
        if name == "list_files":
            return args.get("path", ".")
        if name == "search_code":
            pattern = args.get("pattern", "")
            path = args.get("path", ".")
            include = args.get("include", "")
            target = f'"{pattern}"'
            if include:
                target += f" in {include}"
            if path != ".":
                target += f" ({path})"
            return target
        if name == "run_command":
            return args.get("command", "")
        if name == "get_config":
            return ""
        return ""

    def _log_cycle(
        self,
        iteration: int,
        *,
        tool_call: str | None = None,
        result_status: str | None = None,
        final: bool = False,
        loop_detected: bool = False,
    ) -> None:
        """Log agent cycle activity for observability."""
        parts = [f"iteration={iteration + 1}"]
        if final:
            parts.append("final_response")
        elif tool_call:
            parts.append(f"tool_call={tool_call}")
        if result_status:
            parts.append(f"result={result_status}")
        if loop_detected:
            parts.append("LOOP_DETECTED")
        logger.debug(" ".join(parts))

    def step(self, user_message: str) -> Iterator[AgentEvent]:
        """Process one user turn, yielding events for the UI layer.

        Orchestration per iteration:
        1. LLM produces a response
        2. If response contains tool calls → execute FIRST tool only
        3. Feed result back as role:"tool" message
        4. Repeat until LLM produces a final response (no tool calls)
        """
        self.history.append({"role": "user", "content": user_message})

        # Pre-flight context check
        status = self.context_manager.get_status(self.history)
        if status["status"] != "OK":
            yield AgentEvent(
                type="context_status",
                message=status["display"],
                data=status,
            )

        # Loop detection: track recent tool call signatures
        recent_signatures: list[str] = []
        last_tool_name: str = ""

        for iteration in range(MAX_ITERATIONS):
            # Prepare safe messages for the LLM
            safe_messages = self.context_manager.prepare_messages(self.history)

            try:
                response = chat(safe_messages)
            except Exception as exc:
                msg = f"Error: could not reach LLM — {exc}"
                self.history.append({"role": "assistant", "content": msg})
                self._log_cycle(iteration, final=True)
                yield AgentEvent(type="error", message=msg)
                return

            text = response.choices[0].message.content or ""
            tool_calls = self._extract_tool_calls(text)

            # ── No tool calls → final response → STOP ──────────────
            if not tool_calls:
                self.history.append({"role": "assistant", "content": text})
                self._log_cycle(iteration, final=True)
                yield AgentEvent(type="agent_message", message=text)
                return

            # ── Tool call(s) found → execute first one only ─────────
            self.history.append({"role": "assistant", "content": text})

            # Yield thinking text (text before the tool call block)
            thinking = text
            for tc in tool_calls:
                name = tc.get("name", "")
                args_str = json.dumps(tc.get("arguments", {}), ensure_ascii=False)
                block = f'<tool_call>\n{{"name": "{name}", "arguments": {args_str}}}\n</tool_call>'
                thinking = thinking.replace(block, "").replace("<tool_call>", "").replace("</tool_call>", "")
            thinking = thinking.strip()
            if thinking:
                yield AgentEvent(type="agent_message", message=thinking)

            # Execute only the first tool call
            call = tool_calls[0]
            name = call["name"]
            args = call.get("arguments", {})
            target = self._tool_target(name, args)
            last_tool_name = name

            # ── Loop detection ──────────────────────────────────────
            sig = _call_signature(name, args)

            if recent_signatures and sig == recent_signatures[-1]:
                loop_msg = (
                    f"Loop detected: {name} called repeatedly "
                    f"with identical arguments. Stopping to prevent infinite loop."
                )
                self._log_cycle(iteration, tool_call=name, loop_detected=True)
                self.history.append({"role": "assistant", "content": loop_msg})
                yield AgentEvent(type="error", message=loop_msg)
                return

            recent_signatures.append(sig)
            if len(recent_signatures) > _REPEAT_THRESHOLD:
                recent_signatures = recent_signatures[-_REPEAT_THRESHOLD:]

            # ── Execute tool ────────────────────────────────────────
            self._log_cycle(iteration, tool_call=name)

            yield AgentEvent(
                type="tool_start",
                tool=name,
                target=target,
                data=args,
            )

            result = self._execute_tool(name, args)

            is_error = result.startswith("Error:")
            is_blocked = "BLOCKED" in result
            result_status = "error" if is_error else ("blocked" if is_blocked else "success")

            self._log_cycle(iteration, tool_call=name, result_status=result_status)

            yield AgentEvent(
                type="tool_result",
                tool=name,
                target=target,
                message=result,
                data={"error": is_error, "blocked": is_blocked},
            )

            # ── Feed result back as role:"tool" ────────────────────
            tool_call_id = f"call_{uuid.uuid4().hex[:12]}"
            self.history.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": result,
            })

        # ── Safety fallback: MAX_ITERATIONS reached ─────────────────
        diag = (
            f"Agent stopped after {MAX_ITERATIONS} iterations. "
            f"Last tool called: {last_tool_name or 'none'}. "
            f"Repeated calls detected: {'yes' if len(set(recent_signatures)) == 1 and len(recent_signatures) >= 2 else 'no'}."
        )
        self._log_cycle(MAX_ITERATIONS, final=True)
        yield AgentEvent(type="error", message=diag)
