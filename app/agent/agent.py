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
from dataclasses import dataclass, field
from typing import Any, Iterator

from app.config import get_endpoint, get_model, get_workspace
from app.context import ContextManager
from app.cost import BudgetTracker, TaskLogger, estimate_cost
from app.agent.classify import Classification, DEFAULT_TASK_TYPE, TaskType, classify
from app.agent.planning import Plan, propose as propose_plan, should_plan
from app.agent.confidence import CONFIDENCE_INSTRUCTION, extract as extract_confidence
from app.agent.schemas import (
    render as render_schema,
    retry_instruction,
    schema_instruction,
    validate as validate_schema,
)
from app.agent.stall import StallDetector
from app.agent.steering import as_injection
from app.memory import CheckpointStore, SessionLog, set_store
from app.skills.loader import load_skills, match_skills, render_skills
from app.llm import chat
from app.tools import get_registry, get_event_bus
from app.tools.tool_registry import format_tools_for_prompt
from app.tools.events import ToolEvent, ToolEventType
from app.tools.tool_result import ToolStatus

# Classification costs one extra LLM call per turn. On by default; tests
# that script an exact chat sequence turn it off.
CLASSIFY_TASKS = True

MAX_ITERATIONS = 15
_REPEAT_THRESHOLD = 3
_HISTORY_CAP = 40  # max messages in history (system + conversation window)

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

ACT — DO NOT EXPLAIN HOW THE USER COULD DO IT:
- You are operating on a REAL workspace with REAL tools. If a request can be
  carried out with your tools, carry it out. Do not describe the steps the
  user could take themselves.
- Never answer a "do X" request with a tutorial, a numbered how-to, or an
  example workflow using placeholder names. The user asked you to change
  their project, not to teach them.
- Never end with "let me know if you'd like help applying this", "give it a
  try", or "if you want, I can...". You are the one applying it. Apply it.
- If you are asked whether you are done: you are done only if you actually
  changed something or answered the specific question. Explaining is not
  doing, and a summary of what could be done is not a result.
- The one exception: if the user explicitly asks how something works, or for
  an explanation, then explain.

INVESTIGATION STYLE:
- Investigate efficiently, then finish the task. Take as many tool calls as the
  task genuinely needs — there is no fixed budget, and you must not stop early
  or ration calls.
- Prefer search_code over reading files one by one to explore.
- Do NOT call a tool twice with identical arguments; you already have that result.
- Every tool result you receive is real. Never invent or predict tool output.

You have access to tools for:
- inspecting files
- reading source code
- creating and modifying files
- executing project commands
- searching code across the workspace
- checking your own configuration

{tools_section}

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
- Destructive operations (rm, git reset --hard) are gated by the permission
  layer, which asks the user directly. You cannot approve them yourself; if
  one is declined, report that and move on.
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
    "git_tool": "Git",
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


def _escape_raw_control_chars(text: str) -> str:
    """Escape literal newlines/tabs that appear inside JSON string values.

    JSON forbids raw control characters inside strings, but a model writing
    a multi-line file almost always emits them anyway:

        {"name": "write_file", "arguments": {"content": "line one
        line two"}}

    Without this the object fails to parse, the caller falls back to empty
    arguments, and write_file is dispatched with no path or content.
    """
    out: list[str] = []
    in_string = False
    escape = False
    for ch in text:
        if escape:
            out.append(ch)
            escape = False
            continue
        if ch == "\\" and in_string:
            out.append(ch)
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            out.append(ch)
            continue
        if in_string and ch in "\n\r\t":
            out.append({"\n": "\\n", "\r": "\\r", "\t": "\\t"}[ch])
            continue
        out.append(ch)
    return "".join(out)


def _loads_relaxed(raw: str) -> dict[str, Any] | None:
    """json.loads, retrying once with raw control characters escaped."""
    for candidate in (raw, _escape_raw_control_chars(raw)):
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


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

        # Find the matching closing tag. It may be absent: the LLM stop
        # sequence is "</tool_call>", so the provider strips it from the
        # response. Treat the remainder of the text as the call body.
        end = text.find(tag_end, content_start)
        if end == -1:
            raw = text[content_start:].strip()
            search_from = len(text)
        else:
            raw = text[content_start:end].strip()
            search_from = end + len(tag_end)

        # Try direct JSON parse first (relaxed: tolerates raw newlines)
        parsed = _loads_relaxed(raw)
        if parsed is not None and "name" in parsed:
            calls.append(parsed)
            continue

        # Try brace-matching to find the JSON object boundary
        json_obj = _extract_balanced_json(raw)
        if json_obj is not None:
            parsed = _loads_relaxed(json_obj)
            if parsed is not None and "name" in parsed:
                calls.append(parsed)
                continue

        # Fallback: regex for {"name": ..., "arguments": ...}
        m = re.search(r'\{\s*"name"\s*:\s*"(\w+)"', raw)
        if m:
            name = m.group(1)
            # Try to extract arguments object
            args_match = re.search(r'"arguments"\s*:\s*(\{.*\})', raw, re.DOTALL)
            args: dict[str, Any] | None = None
            if args_match:
                args = _loads_relaxed(args_match.group(1))

            if args is None:
                # Arguments were present but unrecoverable — usually a
                # truncated write_file. Dispatching with {} produced a
                # baffling "missing 2 required positional arguments"; flag
                # it instead so the model is told to resend the call.
                calls.append({"name": name, "arguments": {}, "_malformed": True})
            else:
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


# Hermes/Qwen-style calls: <function=name><parameter=key>value
# nemotron-3-nano emits these instead of JSON, so without this its calls are
# invisible and every turn looks like an empty response.
_FUNCTION_RE = re.compile(r"<function\s*=\s*([A-Za-z_][A-Za-z0-9_]*)\s*>", re.IGNORECASE)
_PARAMETER_RE = re.compile(
    r"<parameter\s*=\s*([A-Za-z_][A-Za-z0-9_]*)\s*>\s*(.*?)\s*(?=<parameter\s*=|</parameter>|</function>|<function\s*=|</tool_call>|\Z)",
    re.IGNORECASE | re.DOTALL,
)


def _coerce_scalar(raw: str) -> Any:
    """Turn an XML parameter's text into the type the schema expects."""
    text = raw.strip().strip('"').strip("'")
    lowered = text.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    return text


def _extract_xml_calls(text: str) -> list[dict[str, Any]]:
    """Parse <function=name><parameter=key>value tool calls."""
    calls: list[dict[str, Any]] = []
    matches = list(_FUNCTION_RE.finditer(text))
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[start:end]
        arguments = {
            key: _coerce_scalar(value)
            for key, value in _PARAMETER_RE.findall(body)
        }
        calls.append({"name": match.group(1), "arguments": arguments})
    return calls


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


# Openers that mark planning-aloud rather than an answer. Reasoning models
# sometimes leak their analysis channel into content: they narrate the call
# they are about to make, then emit no <tool_call> at all. A real answer
# addresses the user ("4", "Hello! How can I help?") and never opens this way,
# so the voice — not the length alone — is what separates the two.
_DELIBERATION_OPENERS = (
    "we need", "we should", "we must", "we will", "we'll", "we have",
    "we can", "we've", "let's", "let me", "now we", "first we", "next we",
    "i need to", "i should", "i'll",
)

# Deliberation is terse; a genuine answer that opens this way is longer.
_ABANDONED_MAX_CHARS = 200


# What actually distinguishes leaked deliberation from prose is the model
# talking to itself, not hedging words. Generic connectives ("so", "since",
# "however", "therefore") are everywhere in ordinary answers — keying on them
# truncated real content like "Since the file is large, I only read the first
# 500 lines." and "So the total is 42 files.", losing a caveat and an answer.
_REASONING_MARKERS = _DELIBERATION_OPENERS + (
    "we can just", "we could", "we might", "maybe we", "perhaps we",
    "i should probably", "the user wants", "the user said",
    "the user asked", "user wants", "user said",
)


def _looks_like_deliberation(paragraph: str) -> bool:
    """True if a paragraph is the model reasoning at itself.

    Requires a first-person planning phrase somewhere in the paragraph. A
    hedging connective on its own is not evidence.
    """
    lowered = paragraph.lower()
    return any(
        lowered.startswith(marker) or f" {marker}" in lowered
        for marker in _REASONING_MARKERS
    )


_THINK_PAIR_RE = re.compile(r"<think\s*>.*?</think\s*>", re.DOTALL | re.IGNORECASE)
_THINK_UPTO_CLOSE_RE = re.compile(r"^.*?</think\s*>", re.DOTALL | re.IGNORECASE)


def _strip_think_blocks(text: str) -> str:
    """Remove a leaked <think> block from the content channel.

    Reasoning models (nemotron here) sometimes write their thinking into
    content instead of reasoning_content, terminated by a literal </think>.
    That tag is an exact delimiter — everything up to and including it is
    deliberation — which beats guessing from sentence shape.
    """
    cleaned = _THINK_PAIR_RE.sub("", text)
    # A stray closing tag with no opener: the whole prefix was the think block.
    if "</think" in cleaned.lower():
        cleaned = _THINK_UPTO_CLOSE_RE.sub("", cleaned, count=1)
    return cleaned.strip()


def _strip_leaked_reasoning(text: str) -> str:
    """Strip a leaked deliberation preamble from a final answer.

    When text looks like [deliberation paragraph(s)] followed by [a final
    part that isn't deliberation], keep only the final part. Returns the
    text UNCHANGED whenever the split is ambiguous — occasionally showing
    leaked reasoning beats truncating a genuine answer.
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paragraphs) < 2:
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        if len(lines) < 2:
            return text
        paragraphs = lines

    last = paragraphs[-1]
    preamble = paragraphs[:-1]

    # Only strip when EVERY preceding paragraph reads as deliberation and the
    # final one does not. A real multi-paragraph answer won't satisfy this.
    if (
        preamble
        and all(_looks_like_deliberation(p) for p in preamble)
        and not _looks_like_deliberation(last)
    ):
        return last
    return text


def _is_abandoned_turn(text: str) -> bool:
    """True if a reply is planning-aloud with no tool call — not an answer."""
    stripped = text.strip()
    if not stripped:
        return True
    if len(stripped) >= _ABANDONED_MAX_CHARS:
        return False
    return stripped.lower().startswith(_DELIBERATION_OPENERS)


_SKILLS_CACHE: list | None = None


def _skills() -> list:
    """All parsed skills, loaded once per process."""
    global _SKILLS_CACHE
    if _SKILLS_CACHE is None:
        _SKILLS_CACHE = load_skills()
    return _SKILLS_CACHE


# Guidance appended per task type. The loop is unchanged; only the framing
# differs, so a misclassification costs tone rather than capability.
_TASK_GUIDANCE: dict[TaskType, str] = {
    TaskType.CODING: "",
    TaskType.SEARCH: (
        "\nThis is a lookup. Prefer search_code over reading files one by one, "
        "and report where things are rather than explaining them at length.\n"
    ),
    TaskType.SUMMARIZATION: (
        "\nThis is a summarisation request. Read what you need, then condense. "
        "Do not pad, and do not restate the source at length.\n"
    ),
    TaskType.CREATIVE_WRITING: (
        "\nThis is a writing request. Write the thing that was asked for, in "
        "prose. Do not wrap it in JSON, and do not narrate your process.\n"
    ),
    TaskType.QA: (
        "\nThis is a question. Answer it directly. Use tools only if you "
        "genuinely need to look something up.\n"
    ),
    TaskType.CONVERSATIONAL: (
        "\nThis is conversational. Reply briefly and move on. No tool calls "
        "are needed for a greeting or an acknowledgement.\n"
    ),
}


def _build_system_prompt() -> str:
    """Build the system prompt with a tool list generated from the registry."""
    return SYSTEM_PROMPT_TEMPLATE.format(
        workspace=str(get_workspace()),
        tools_section=format_tools_for_prompt(get_registry()),
    )


def _safe_int(value: Any) -> int:
    """Coerce a token-usage field to int, defaulting to 0.

    Deliberately not ``int(value)``: MagicMock defines __int__, so a mocked
    or absent usage object would silently coerce to 1 and fabricate token
    counts. Only real numeric types are trusted.
    """
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


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
        system_prompt = _build_system_prompt()
        self.history: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt}
        ]
        self.total_tokens: int = 0
        self.input_tokens: int = 0
        self.output_tokens: int = 0
        self.context_manager = ContextManager()
        self.session_cost_usd: float = 0.0
        self.session_log = SessionLog.from_env(get_workspace())
        self.checkpoints = CheckpointStore(workspace=get_workspace())
        set_store(self.checkpoints)
        # Set by the interactive UI. Signature: (Plan) -> (approved, edit).
        # Left None in one-shot mode, where there is nobody to ask.
        self.plan_reviewer = None
        # Polled after every tool call. Returns a correction to apply now, or
        # None. Left None in one-shot mode: nobody is typing.
        self.steering_source = None
        self.last_classification: Classification | None = None
        # The clarification just asked, so the same question is not put
        # twice in a row after the user has already answered it.
        self._last_clarification: str = ""
        self.classify_tasks = CLASSIFY_TASKS
        self.budget = BudgetTracker()
        self.budget.load_today(get_workspace() / "logs")
        # Status of the most recent tool execution, set by _execute_tool.
        self._last_tool_status: ToolStatus | None = None

    def clear_history(self) -> None:
        """Reset conversation to just the system prompt."""
        system_prompt = _build_system_prompt()
        self.history = [{"role": "system", "content": system_prompt}]

    def _cap_history(self) -> None:
        """Trim history to prevent context overflow.

        Keeps: system prompt (index 0) + last _HISTORY_CAP messages.
        Called after every tool result to prevent unbounded growth.
        """
        if len(self.history) <= _HISTORY_CAP + 1:
            return
        system = self.history[0]
        recent = self.history[-(_HISTORY_CAP):]
        self.history = [system] + recent

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

        self._last_tool_status = result.status
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

        # Format 2: <function=name><parameter=key>value
        xml_calls = _extract_xml_calls(text)
        if xml_calls:
            return xml_calls

        # Format 3: bare JSON
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
        if name == "git_tool":
            sub = args.get("subcommand", "")
            extra = args.get("branch_name") or args.get("path", "")
            return f"{sub} {extra}".strip() if extra not in ("", ".") else sub
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
        # The budget gate lives inside the loop, but classification happens
        # before it — so check here too, or an exhausted budget still pays
        # for one call.
        if self.budget.would_exceed(0.0):
            msg = (
                f"Daily budget exceeded: ${self.budget.spent_today_usd:.4f} of "
                f"${self.budget.daily_budget_usd:.2f} already spent today. "
                "Refusing further model calls until the budget resets."
            )
            self.history.append({"role": "user", "content": user_message})
            self.history.append({"role": "assistant", "content": msg})
            self.input_tokens = self.output_tokens = self.total_tokens = 0
            yield AgentEvent(type="error", message=msg)
            return

        self.checkpoints.begin_turn()

        # ── Stage 1: classify for routing (never forks the loop) ────
        classification = (
            classify(user_message, chat) if self.classify_tasks
            else Classification(DEFAULT_TASK_TYPE, 1.0, fell_back=True)
        )
        self.last_classification = classification
        task_type = classification.task_type
        if not classification.fell_back:
            yield AgentEvent(
                type="task_classified",
                message=task_type.value,
                data={
                    "task_type": task_type.value,
                    "confidence": classification.confidence,
                },
            )

        # ── Plan first for work that will span several tool calls ──
        approved_plan: Plan | None = None
        if self.plan_reviewer is not None and should_plan(classification, user_message):
            plan = propose_plan(user_message, chat)
            if plan:
                approved, replacement = self.plan_reviewer(plan)
                if not approved:
                    note = replacement or "Plan rejected. Restate what you want."
                    self.history.append({"role": "assistant", "content": note})
                    self.session_log.record(task_type.value, user_message, note)
                    yield AgentEvent(type="plan_rejected", message=note)
                    return
                approved_plan = replacement or plan

        # ── Stage 4: condensed recap of recent turns, not a transcript ──
        recap = self.session_log.recap()
        if recap:
            self.history.append({"role": "system", "content": recap, "recap": True})

        # ── Contextual framing + output shape for this task type ────
        framing = _TASK_GUIDANCE.get(task_type, "")
        if classification.is_structured:
            framing += schema_instruction(task_type)
        if classification.is_confidence_gated:
            framing += CONFIDENCE_INSTRUCTION
        if framing.strip():
            self.history.append({"role": "system", "content": framing.strip(), "framing": True})
        if approved_plan is not None:
            self.history.append({
                "role": "system",
                "content": approved_plan.as_instruction(),
                "plan": True,
            })

        self.history.append({"role": "user", "content": user_message})

        # ── Inject any skills this request triggers ─────────────────
        # Only matched skills go in: all 16 bodies total ~13KB, which would
        # be half the context budget on a 27.6k model. Already-injected
        # skills are skipped so a long conversation cannot accumulate them.
        already = {
            m.get("skill") for m in self.history if m.get("role") == "system"
        }
        matched = [s for s in match_skills(user_message, _skills()) if s.name not in already]
        for skill in matched:
            self.history.append({
                "role": "system",
                "content": render_skills([skill]),
                "skill": skill.name,
            })
        if matched:
            yield AgentEvent(
                type="skills_applied",
                message=", ".join(s.name for s in matched),
                data={"skills": [s.name for s in matched]},
            )

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
        consecutive_loops = 0
        empty_responses = 0
        schema_retried = False
        last_tool_name: str = ""

        # Per-turn accounting. A turn makes several LLM calls, so usage is
        # summed here and published to the public attributes exactly once,
        # at whichever exit this turn takes.
        model_name = get_model()
        task_logger = TaskLogger(get_workspace() / "logs")
        stall = StallDetector.for_request(user_message)
        turn_input_tokens = 0
        turn_output_tokens = 0
        turn_total_tokens = 0
        turn_cost_usd = 0.0

        def _finalize(result_text: str, had_error: bool) -> None:
            self.input_tokens = turn_input_tokens
            self.output_tokens = turn_output_tokens
            self.total_tokens = turn_total_tokens
            task_logger.finalize(
                task=user_message,
                model=model_name,
                input_tokens=turn_input_tokens,
                output_tokens=turn_output_tokens,
                cost_usd=turn_cost_usd,
                result=result_text,
                had_error=had_error,
            )

        for iteration in range(MAX_ITERATIONS):
            # ── Pre-flight budget gate ──────────────────────────────
            if self.budget.would_exceed(0.0):
                msg = (
                    f"Daily budget exceeded: ${self.budget.spent_today_usd:.4f} of "
                    f"${self.budget.daily_budget_usd:.2f} already spent today. "
                    "Refusing further model calls until the budget resets."
                )
                self.history.append({"role": "assistant", "content": msg})
                _finalize(msg, had_error=True)
                yield AgentEvent(type="error", message=msg)
                return

            # Prepare safe messages for the LLM
            safe_messages = self.context_manager.prepare_messages(self.history)

            try:
                response = chat(safe_messages)
            except Exception as exc:
                # A fresh install has no .env, so this is the first thing a
                # new user hits. "Connection error" alone tells them nothing.
                msg = (
                    f"Error: could not reach the LLM at {get_endpoint()} — {exc}\n"
                    "Set LITELLM_BASE_URL, LITELLM_API_KEY and LITELLM_MODEL in "
                    "~/.config/vektorcode/.env (or your shell), then run /reload."
                )
                self.history.append({"role": "assistant", "content": msg})
                self._log_cycle(iteration, final=True)
                task_logger.errors.append(msg)
                _finalize(msg, had_error=True)
                yield AgentEvent(type="error", message=msg)
                return

            message = response.choices[0].message
            text = _strip_think_blocks(message.content or "")
            reasoning = getattr(message, "reasoning_content", None) or ""

            tool_calls = self._extract_tool_calls(text) if text.strip() else []

            # Reasoning models (gpt-oss, nemotron) frequently emit an empty
            # content channel. If the tool call landed in reasoning_content,
            # recover it — but never present raw chain-of-thought to the user
            # as a final answer, so reasoning is used for dispatch only.
            if not tool_calls and not text.strip() and reasoning.strip():
                recovered = self._extract_tool_calls(reasoning)
                if recovered:
                    tool_calls = recovered
                    text = reasoning

            # Accumulate token usage across the whole turn. Overwriting here
            # reported only the final call and undercounted multi-step turns.
            usage = getattr(response, "usage", None)
            call_input_tokens = _safe_int(getattr(usage, "prompt_tokens", 0)) if usage else 0
            call_output_tokens = _safe_int(getattr(usage, "completion_tokens", 0)) if usage else 0
            call_total_tokens = _safe_int(getattr(usage, "total_tokens", 0)) if usage else 0
            if not call_total_tokens:
                call_total_tokens = call_input_tokens + call_output_tokens

            turn_input_tokens += call_input_tokens
            turn_output_tokens += call_output_tokens
            turn_total_tokens += call_total_tokens

            call_cost_usd = estimate_cost(model_name, call_input_tokens, call_output_tokens)
            turn_cost_usd += call_cost_usd
            self.session_cost_usd += call_cost_usd
            self.budget.record(call_cost_usd)

            # This deployment intermittently drops the tool call: the model
            # deliberates in the reasoning channel and emits either nothing or
            # bare thinking-aloud into content, with no <tool_call> block. That
            # is not a final answer, so nudge rather than reporting it as one.
            # Deliberation can surface in either channel, so check both. The
            # length bound is what separates "thinking aloud about a call" from
            # a real answer that happens to mention a tool by name; a genuine
            # answer to a real question is not this short.
            intended_tool = not tool_calls and _is_abandoned_turn(text)

            # ── Empty / abandoned turn → nudge once, never report as answer ──
            if not tool_calls and (not text.strip() or intended_tool):
                empty_responses += 1
                if empty_responses >= 3:
                    msg = (
                        "Error: the model failed to emit a tool call 3 times in a row "
                        "(it reasoned about calling one but produced no <tool_call> "
                        "block). Try rephrasing, or switch models with /model."
                    )
                    self.history.append({"role": "assistant", "content": msg})
                    task_logger.errors.append(msg)
                    _finalize(msg, had_error=True)
                    yield AgentEvent(type="error", message=msg)
                    return
                self.history.append({
                    "role": "user",
                    "content": "Your last reply contained no <tool_call> block and no "
                               "final answer. Reply now with either a literal "
                               "<tool_call> block or your final answer.",
                })
                continue

            # ── No tool calls → final response → STOP ──────────────
            if not tool_calls:
                # Strip any leaked protocol markup from final response
                clean = re.sub(r"<tool_call>.*?</tool_call>", "", text, flags=re.DOTALL).strip()
                # Store the stripped version: otherwise later turns re-read the
                # model's own leaked deliberation as a prior real answer.
                clean = _strip_leaked_reasoning(clean)

                # ── Stage 3: ask rather than guess when unsure ──────
                if classification.is_confidence_gated:
                    signal = extract_confidence(clean)
                    if signal is not None:
                        clean = signal.body or clean
                        repeat = (
                            signal.question.strip().lower()
                            == self._last_clarification.strip().lower()
                        )
                        if signal.should_clarify() and not repeat:
                            question = signal.question
                            self._last_clarification = question
                            self.history.append({"role": "assistant", "content": question})
                            self.session_log.record(task_type.value, user_message, question)
                            _finalize(question, had_error=False)
                            yield AgentEvent(
                                type="clarification_needed",
                                message=question,
                                data={"confidence": signal.confidence},
                            )
                            return

                # ── Stage 2: validate shapes that are checkable ─────
                if classification.is_structured:
                    parsed, error = validate_schema(task_type, clean)
                    if parsed is not None:
                        clean = render_schema(parsed)
                    elif error and not schema_retried and "{" in clean:
                        # One informed retry, with the specific failure.
                        schema_retried = True
                        self.history.append({"role": "assistant", "content": clean})
                        self.history.append({
                            "role": "user",
                            "content": retry_instruction(error),
                        })
                        self._log_cycle(iteration, result_status="schema_invalid")
                        self._cap_history()
                        continue
                    # Either a second failure, or the model answered in plain
                    # prose. Prose is a legitimate answer — rejecting it would
                    # be the structured-output layer making things worse.

                self._last_clarification = ""
                self.history.append({"role": "assistant", "content": clean})
                self._log_cycle(iteration, final=True)
                self.session_log.record(task_type.value, user_message, clean)
                _finalize(clean, had_error=False)
                yield AgentEvent(type="agent_message", message=clean)
                return

            # ── Tool call(s) found → execute first one only ─────────
            # Strip <tool_call> protocol markup from assistant message
            clean_text = text
            for tc in tool_calls:
                name = tc.get("name", "")
                args_str = json.dumps(tc.get("arguments", {}), ensure_ascii=False)
                block = f'<tool_call>\n{{"name": "{name}", "arguments": {args_str}}}\n</tool_call>'
                clean_text = clean_text.replace(block, "")
            clean_text = re.sub(r"<tool_call>.*?</tool_call>", "", clean_text, flags=re.DOTALL).strip()

            self.history.append({"role": "assistant", "content": clean_text or "(tool call)"})

            # Execute only the first tool call
            call = tool_calls[0]
            name = call["name"]
            args = call.get("arguments", {})
            target = self._tool_target(name, args)
            last_tool_name = name

            # ── Unparseable arguments → ask for a resend ────────────
            if call.get("_malformed"):
                malformed_msg = (
                    f"Your {name} call could not be parsed — the arguments were "
                    "incomplete or invalid JSON. Resend the whole <tool_call> "
                    "block with valid JSON, escaping newlines in string values "
                    "as \\n."
                )
                self._log_cycle(iteration, tool_call=name, result_status="error")
                self.history.append({
                    "role": "tool",
                    "tool_call_id": f"malformed_{iteration}",
                    "content": malformed_msg,
                })
                task_logger.errors.append(f"{name}: unparseable arguments")
                self._cap_history()
                continue

            # ── Loop detection ──────────────────────────────────────
            sig = _call_signature(name, args)

            if recent_signatures and sig == recent_signatures[-1]:
                consecutive_loops += 1
                loop_msg = (
                    f"Loop detected: {name} called repeatedly "
                    f"with identical arguments."
                )
                self._log_cycle(iteration, tool_call=name, loop_detected=True)

                # Force stop after 3 consecutive loops — model is stuck
                if consecutive_loops >= 3:
                    stop_msg = f"Agent stopped: {loop_msg} Giving up after {consecutive_loops} attempts."
                    self.history.append({"role": "assistant", "content": stop_msg})
                    task_logger.errors.append(stop_msg)
                    _finalize(stop_msg, had_error=True)
                    yield AgentEvent(type="error", message=stop_msg)
                    return

                # Send feedback so model can try a different approach
                self.history.append({
                    "role": "tool",
                    "tool_call_id": f"loop_{iteration}",
                    "content": f"BLOCKED: {loop_msg} Do NOT call {name} again with these arguments. Respond with a final answer or try a different tool.",
                })
                self._cap_history()
                continue

            consecutive_loops = 0  # reset on successful different call
            empty_responses = 0    # model produced a usable turn
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

            # Classify from the tool's own status, never by searching the
            # output text: reading a file that merely contains the word
            # "BLOCKED" (agent.py does, in its loop-detection message) would
            # otherwise render the whole file as a blocked-command error.
            status = self._last_tool_status
            self._last_tool_status = None
            if status is None:
                is_error = result.startswith("Error:")
                is_blocked = False
            else:
                is_error = status == ToolStatus.ERROR
                is_blocked = status == ToolStatus.BLOCKED
            result_status = "error" if is_error else ("blocked" if is_blocked else "success")

            self._log_cycle(iteration, tool_call=name, result_status=result_status)
            task_logger.record_tool_call(name, args, result_status)
            stall.record(name, target, result)

            yield AgentEvent(
                type="tool_result",
                tool=name,
                target=target,
                message=result,
                data={"error": is_error, "blocked": is_blocked},
            )

            # ── Feed result back as role:"tool" ────────────────────
            # The tool result carries a header naming the call it answers.
            # The assistant turn cannot hold this: the model imitates whatever
            # it sees in its own voice and emits the record instead of a real
            # <tool_call>. Stated in the tool message, it is unambiguously an
            # observation — and without it the model cannot tell which file or
            # line range a result corresponds to, so it re-requests the data.
            tool_call_id = f"call_{iteration}_{name}"
            args_repr = json.dumps(args, ensure_ascii=False)
            self.history.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": f"Result of {name}({args_repr}):\n{result}",
            })

            # ── Mid-turn steering: a correction cannot wait for the ──
            # ── turn to end, or by then the damage is done.          ──
            if self.steering_source is not None:
                correction = self.steering_source()
                if correction:
                    self.history.append({
                        "role": "user",
                        "content": as_injection(correction),
                        "steering": True,
                    })
                    task_logger.record_tool_call("steering", {"message": correction}, "success")
                    yield AgentEvent(type="steering_applied", message=correction)
                    self._cap_history()
                    continue

            # ── Unproductive loop → nudge, do not wait for the cap ──
            if stall.stalled:
                self._log_cycle(iteration, tool_call=name, loop_detected=True)
                self.history.append({
                    "role": "user",
                    "content": stall.nudge(user_message),
                })
                task_logger.errors.append(f"stall: {stall.reason}")
                yield AgentEvent(
                    type="stall_detected",
                    tool=name,
                    target=target,
                    message=stall.reason,
                )
                stall.reset()
                self._cap_history()
                continue

            # ── Cap history to prevent context overflow ─────────────
            self._cap_history()

        # ── Safety fallback: MAX_ITERATIONS reached ─────────────────
        # Yield a summary of what was accomplished, not an error
        tools_used = list({s.split('"name": "')[1].split('"')[0]
                          for s in recent_signatures if '"name": "' in s})
        fallback = (
            f"Stopped after {MAX_ITERATIONS} iterations without finishing. "
            f"Tools used: {', '.join(tools_used) or 'none'}."
        )

        # Actually ask the model what happened. The previous code yielded the
        # instruction text itself to the user as if it were an answer.
        self.history.append({
            "role": "user",
            "content": (
                f"You've used all {MAX_ITERATIONS} tool-call iterations without "
                "reaching a final answer. Stop calling tools. In 2-3 sentences, "
                "tell the user what you found/attempted, what's still unresolved, "
                "and what you'd need to finish (do NOT emit a <tool_call> block)."
            ),
        })
        try:
            safe_messages = self.context_manager.prepare_messages(self.history)
            response = chat(safe_messages)
            message = response.choices[0].message
            summary = (message.content or "").strip()
            if not summary:
                summary = (getattr(message, "reasoning_content", None) or "").strip()
            summary = re.sub(r"<tool_call>.*?</tool_call>", "", summary, flags=re.DOTALL).strip()
            if not summary:
                summary = fallback
        except Exception:
            summary = fallback

        self.history.append({"role": "assistant", "content": summary})
        self._log_cycle(MAX_ITERATIONS, final=True)
        # had_error=True: hitting the cap is not a clean finish.
        _finalize(summary, had_error=True)
        yield AgentEvent(type="max_iterations_reached", message=summary)
        return
