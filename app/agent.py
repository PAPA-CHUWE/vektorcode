"""VektorCode agent – orchestrates LLM calls and tool execution.

The agent yields structured AgentEvent objects. The UI layer consumes
these events and decides how to render them. The agent itself contains
zero terminal formatting code.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterator

from app.config import WORKSPACE
from app.llm import chat
from app.tools import filesystem, search, terminal
from app.tools.config import get_config

MAX_ITERATIONS = 15

SYSTEM_PROMPT_TEMPLATE = """\
You are VektorCode, an AI coding agent.

Your job is to help developers understand, create, modify, test, debug, and improve software projects.

Active workspace: {workspace}

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

You may chain multiple tool calls by outputting multiple <tool_call> blocks.
After receiving tool results, continue reasoning and decide next steps.

IMPORTANT RULES:
- When the user asks about your model, configuration, endpoint, or API key, ALWAYS call get_config() first. Never guess or make up an answer.
- When the user asks about project files, ALWAYS call list_files() first.
- When searching for code patterns, use search_code() instead of reading files one by one.
- Understand the project before making changes.
- Make the smallest appropriate changes.
- Run tests or validation after changes.
- Do not modify unrelated files.
- Do not claim something works unless you validated it.
- Ask for confirmation before destructive operations (rm, git reset --hard, etc.).
- When done, give your final answer WITHOUT any <tool_call> blocks.
"""

TOOL_CALL_PATTERN = re.compile(
    r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL
)

TOOL_MAP: dict[str, Any] = {
    "list_files": filesystem.list_files,
    "read_file": filesystem.read_file,
    "write_file": filesystem.write_file,
    "search_code": search.search_code,
    "run_command": terminal.run_command,
    "get_config": get_config,
}

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


class Agent:
    """Maintains conversation history and drives the LLM tool loop.

    The step() method yields AgentEvent objects. The caller consumes them
    to render the UI. The agent itself never touches the terminal.
    """

    def __init__(self) -> None:
        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(workspace=str(WORKSPACE))
        self.history: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt}
        ]
        self.total_tokens: int = 0

    def clear_history(self) -> None:
        """Reset conversation to just the system prompt."""
        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(workspace=str(WORKSPACE))
        self.history = [{"role": "system", "content": system_prompt}]

    def _execute_tool(self, name: str, arguments: dict[str, Any]) -> str:
        fn = TOOL_MAP.get(name)
        if fn is None:
            return f"Error: unknown tool '{name}'"
        try:
            return fn(**arguments)
        except TypeError as exc:
            return f"Error: bad arguments for {name}: {exc}"
        except Exception as exc:
            return f"Error: {exc}"

    def _extract_tool_calls(self, text: str) -> list[dict[str, Any]]:
        """Parse tool call blocks from the model's response."""
        calls = []
        for match in TOOL_CALL_PATTERN.finditer(text):
            raw = match.group(1).strip()
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict) and "name" in parsed:
                    calls.append(parsed)
                elif isinstance(parsed, dict) and "function" in parsed:
                    calls.append(parsed)
                elif isinstance(parsed, list) and len(parsed) >= 2:
                    name = parsed[0] if isinstance(parsed[0], str) else str(parsed[0])
                    args = parsed[1] if isinstance(parsed[1], dict) else {}
                    calls.append({"name": name, "arguments": args})
            except json.JSONDecodeError:
                m = re.search(r'\{["\']?(\w+)["\']?\s*,\s*(\{.*?\})\}', raw)
                if m:
                    name = m.group(1)
                    try:
                        args = json.loads(m.group(2))
                    except json.JSONDecodeError:
                        args = {}
                    calls.append({"name": name, "arguments": args})
                continue
        return calls

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

    def step(self, user_message: str) -> Iterator[AgentEvent]:
        """Process one user turn, yielding events for the UI layer."""
        self.history.append({"role": "user", "content": user_message})

        for iteration in range(MAX_ITERATIONS):
            try:
                response = chat(self.history)
            except Exception as exc:
                msg = f"Error: could not reach LLM — {exc}"
                self.history.append({"role": "assistant", "content": msg})
                yield AgentEvent(type="error", message=msg)
                return

            text = response.choices[0].message.content or ""
            tool_calls = self._extract_tool_calls(text)

            if not tool_calls:
                self.history.append({"role": "assistant", "content": text})
                yield AgentEvent(type="agent_message", message=text)
                return

            self.history.append({"role": "assistant", "content": text})

            # Yield thinking message if there's text before tool calls
            thinking = text
            for tc in tool_calls:
                name = tc.get("name", "")
                args_str = json.dumps(tc.get("arguments", {}), ensure_ascii=False)
                block = f'<tool_call>\n{{"name": "{name}", "arguments": {args_str}}}\n</tool_call>'
                thinking = thinking.replace(block, "").replace("<tool_call>", "").replace("</tool_call>", "")
            thinking = thinking.strip()
            if thinking:
                yield AgentEvent(type="agent_message", message=thinking)

            results = []
            for call in tool_calls:
                name = call["name"]
                args = call.get("arguments", {})
                target = self._tool_target(name, args)

                yield AgentEvent(
                    type="tool_start",
                    tool=name,
                    target=target,
                    data=args,
                )

                result = self._execute_tool(name, args)

                is_error = result.startswith("Error:")
                is_blocked = "BLOCKED" in result

                yield AgentEvent(
                    type="tool_result",
                    tool=name,
                    target=target,
                    message=result,
                    data={"error": is_error, "blocked": is_blocked},
                )

                results.append(f"[{name}] {result}")

            tool_output = "\n\n".join(results)
            self.history.append(
                {"role": "user", "content": f"Tool results:\n{tool_output}"}
            )

        yield AgentEvent(
            type="error",
            message="Agent exceeded maximum iterations. Please rephrase your request.",
        )
