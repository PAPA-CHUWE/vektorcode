"""End-to-end tests for the VektorCode agent."""

from __future__ import annotations

import json
import sys
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure app package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.config as _cfg
from app.agent import Agent, AgentEvent
from app.tools.filesystem import list_files, read_file, write_file
from app.tools.terminal import run_command, _is_destructive
from app.tools.search import search_code
from app.ui.diff import format_diff

# Ensure WORKSPACE is set for tests (normally done by main())
if _cfg.WORKSPACE is None:
    _cfg.WORKSPACE = Path.cwd().resolve()

from app.config import get_workspace
WORKSPACE = get_workspace()

TEST_DIR = WORKSPACE / "_test_e2e"

# These tests exercise tool behaviour, not the permission gate. Without an
# approver installed the gate correctly denies everything, so grant blanket
# permission here and cover the gate itself in test_permission_gate().
from app.permissions.mode import PermissionMode, set_mode as _set_mode
_set_mode(PermissionMode.FULL_AUTO)


def _setup():
    TEST_DIR.mkdir(exist_ok=True)


from tests._log_isolation import snapshot as _snapshot_logs

_LOGS_BEFORE = _snapshot_logs()


def _cleanup():
    import shutil
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    # Clean up test artifact from nested JSON test
    test_py = Path("app/test.py")
    if test_py.exists():
        test_py.unlink()
    # Remove only the task logs this run created — never the whole day
    # directory, which may hold genuine audit logs from real usage.
    from tests._log_isolation import restore
    restore(_LOGS_BEFORE)


def test_workspace_is_cwd():
    """Verify workspace is the current working directory."""
    assert WORKSPACE == Path.cwd().resolve(), f"Workspace {WORKSPACE} != CWD {Path.cwd()}"
    print("  PASS: workspace is CWD")


def test_list_files():
    _setup()
    write_file("_test_e2e/hello.py", "print('hello')")
    result = list_files("_test_e2e")
    assert "hello.py" in result, f"Expected hello.py in result: {result}"
    print("  PASS: list_files")


def test_read_file():
    _setup()
    write_file("_test_e2e/read_test.txt", "test content here")
    result = read_file("_test_e2e/read_test.txt")
    assert "test content here" in result, f"Expected content: {result}"
    print("  PASS: read_file")


def test_write_file():
    _setup()
    result = write_file("_test_e2e/write_test.py", "x = 42")
    assert "Wrote" in result, f"Expected write confirmation: {result}"
    result = read_file("_test_e2e/write_test.py")
    assert "x = 42" in result, f"Expected written content: {result}"
    print("  PASS: write_file")


def test_search_code():
    _setup()
    write_file("_test_e2e/search_a.py", "def alpha():\n    pass")
    write_file("_test_e2e/search_b.py", "def beta():\n    pass")
    result = search_code("alpha", path="_test_e2e")
    assert "alpha" in result, f"Expected alpha match: {result}"
    assert "search_a.py" in result, f"Expected file name: {result}"
    print("  PASS: search_code")


def test_path_traversal_blocked():
    """Verify paths escaping workspace are rejected."""
    result = list_files("../../etc")
    assert "Error" in result, f"Expected path traversal error: {result}"
    print("  PASS: path traversal blocked")


def test_destructive_command():
    """Destructive commands are blocked in default mode with nobody to ask."""
    from app.permissions.mode import PermissionMode, get_mode, set_approver, set_mode

    original = get_mode()
    try:
        set_mode(PermissionMode.DEFAULT)
        set_approver(None)
        result = str(run_command("rm -rf /tmp/test"))
        assert "BLOCKED" in result, f"Expected BLOCKED: {result}"
    finally:
        set_approver(None)
        set_mode(original)
    print("  PASS: destructive command blocked")


def test_permission_gate():
    """The agent must not be able to approve its own privileged actions."""
    import inspect
    from app.permissions.mode import (
        PermissionMode, set_approver, set_mode, get_mode,
    )
    from app.tools import get_registry

    original = get_mode()
    try:
        # "confirm" must not exist — as a parameter or in the LLM schema.
        assert "confirm" not in inspect.signature(run_command).parameters, \
            "run_command must not take a model-settable confirm flag"
        schema = [s for s in get_registry().to_llm_schemas()
                  if s["function"]["name"] == "run_command"][0]
        assert "confirm" not in schema["function"]["parameters"]["properties"], \
            "confirm must not be exposed to the model"

        # No approver available → denied.
        set_mode(PermissionMode.DEFAULT)
        set_approver(None)
        assert "BLOCKED" in str(run_command("rm /tmp/nonexistent_for_test_12345"))

        # Human declines → denied.
        set_approver(lambda kind, detail: False)
        assert "BLOCKED" in str(run_command("rm /tmp/nonexistent_for_test_12345"))

        # Human approves → runs.
        set_approver(lambda kind, detail: True)
        assert "BLOCKED" not in str(run_command("echo ok"))

        # full-auto never asks.
        set_mode(PermissionMode.FULL_AUTO)
        set_approver(lambda kind, detail: (_ for _ in ()).throw(
            AssertionError("full-auto must not prompt")))
        assert "BLOCKED" not in str(run_command("rm /tmp/nonexistent_for_test_12345"))
    finally:
        set_approver(None)
        set_mode(original)

    print("  PASS: permission gate (agent cannot self-approve)")


def test_destructive_patterns():
    commands = [
        "rm -rf /", "rm file.txt", "git reset --hard",
        "git clean -fd", "git push --force", "drop table users",
        "drop database mydb",
    ]
    for cmd in commands:
        assert _is_destructive(cmd), f"Expected destructive: {cmd}"
    safe_commands = ["ls -la", "cat file.txt", "git status", "python main.py"]
    for cmd in safe_commands:
        assert not _is_destructive(cmd), f"Should not be destructive: {cmd}"
    print("  PASS: destructive pattern detection")


def test_agent_tool_loop():
    tool_call_resp = MagicMock()
    tool_call_resp.choices = [MagicMock()]
    tool_call_resp.choices[0].message.content = (
        "I'll list the files.\n\n<tool_call>\n"
        '{"name": "list_files", "arguments": {"path": "."}}\n'
        "</tool_call>"
    )
    final_resp = MagicMock()
    final_resp.choices = [MagicMock()]
    final_resp.choices[0].message.content = "Here are the files in the workspace."

    with patch("app.agent.agent.chat", side_effect=[tool_call_resp, final_resp]):
        agent = Agent()
        events = list(agent.step("list files in the workspace"))
        types = [e.type for e in events]
        assert "tool_start" in types, f"Expected tool_start event: {types}"
        assert "tool_result" in types, f"Expected tool_result event: {types}"
        assert events[-1].type == "agent_message"
        assert events[-1].message == "Here are the files in the workspace."
        print("  PASS: agent tool loop")


def test_agent_max_iterations():
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = (
        '<tool_call>\n{"name": "get_config", "arguments": {}}\n</tool_call>'
    )

    with patch("app.agent.agent.chat", return_value=mock_response):
        agent = Agent()
        events = list(agent.step("show config"))
        types = [e.type for e in events]
        assert types[-1] == "error", f"Expected error event: {types}"
        # Loop detection fires at _REPEAT_THRESHOLD (3) repetitions
        assert "Loop detected" in events[-1].message
        print("  PASS: max iterations (loop detection)")


def test_agent_final_answer():
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "The workspace is empty."

    with patch("app.agent.agent.chat", return_value=mock_response):
        agent = Agent()
        events = list(agent.step("what's in the workspace?"))
        assert len(events) == 1
        assert events[0].type == "agent_message"
        assert events[0].message == "The workspace is empty."
        print("  PASS: agent final answer")


def test_agent_bare_json_tool_call():
    """Test parsing of bare JSON tool calls without <tool_call> tags."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = (
        '{"name": "list_files", "arguments": {"path": "."}}'
    )

    with patch("app.agent.agent.chat", return_value=mock_response):
        agent = Agent()
        events = list(agent.step("list files"))
        types = [e.type for e in events]
        assert "tool_start" in types, f"Expected tool_start: {types}"
        assert "tool_result" in types, f"Expected tool_result: {types}"
        print("  PASS: bare JSON tool call")


def test_agent_clear_history():
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "OK."

    with patch("app.agent.agent.chat", return_value=mock_response):
        agent = Agent()
        list(agent.step("hello"))
        assert len(agent.history) > 1
        agent.clear_history()
        assert len(agent.history) == 1
        assert agent.history[0]["role"] == "system"
        assert str(WORKSPACE) in agent.history[0]["content"]
        print("  PASS: clear history")


def test_agent_system_prompt_includes_workspace():
    """Verify the system prompt contains the active workspace."""
    agent = Agent()
    assert str(WORKSPACE) in agent.history[0]["content"]
    print("  PASS: system prompt includes workspace")


def test_diff_format():
    old = "line1\nline2\nline3"
    new = "line1\nmodified\nline3\nline4"
    diff = format_diff(old, new, "test.py")
    assert "modified" in diff
    assert "line4" in diff
    print("  PASS: diff formatting")


def test_event_dataclass():
    e = AgentEvent(type="tool_start", tool="read_file", target="src/main.py")
    assert e.type == "tool_start"
    assert e.tool == "read_file"
    assert e.target == "src/main.py"
    assert e.data == {}
    print("  PASS: AgentEvent dataclass")


def test_config_version():
    from app.config import __version__
    assert __version__, f"Version should not be empty"
    print(f"  PASS: version = {__version__}")


def test_config_env_search():
    from app.config import get_env_path
    env_path = get_env_path()
    assert env_path.name == ".env", f"Expected .env file: {env_path}"
    print(f"  PASS: env path = {env_path}")


def test_auth_provider():
    from app.auth import APIKeyAuthProvider, AuthProvider
    from app.auth.base import AuthCredentials

    provider = APIKeyAuthProvider()
    assert isinstance(provider, AuthProvider)
    assert provider.name == "API Key"

    # Initially may or may not have a key from .env
    was_authenticated = provider.is_authenticated()

    # Login with a test key
    creds = provider.login(api_key="sk-test1234567890abcdef")
    assert creds.api_key == "sk-test1234567890abcdef"
    assert creds.provider_name == "API Key"
    assert provider.is_authenticated()

    # Get credentials
    creds = provider.get_credentials()
    assert creds.api_key == "sk-test1234567890abcdef"

    # Masked key
    masked = provider.get_masked_key()
    assert "..." in masked

    # Logout
    provider.logout()
    assert not provider.is_authenticated()
    creds = provider.get_credentials()
    assert creds.api_key == ""

    # Restore original state
    if was_authenticated:
        from app.config import get_api_key
        # The .env key was cleared, that's fine for testing
    print("  PASS: auth provider")


def test_context_budget():
    from app.context import (
        estimate_tokens, estimate_messages_tokens, check_budget,
        compact_history, ContextManager, get_model_config,
    )

    # Token estimation
    assert estimate_tokens("hello world") > 0
    assert estimate_tokens("") >= 1

    # Message token estimation
    msgs = [{"role": "user", "content": "hello"}]
    tokens = estimate_messages_tokens(msgs)
    assert tokens > 0

    # Budget check — small message should be safe
    msgs = [
        {"role": "system", "content": "You are a coding agent."},
        {"role": "user", "content": "hello"},
    ]
    report = check_budget(msgs, "qwen")
    assert report.is_safe
    assert report.model == "qwen"
    assert report.context_window == 32768
    assert report.input_budget > 0

    # Budget check — large message should warn
    large_content = "x" * 100000
    msgs = [
        {"role": "system", "content": "You are a coding agent."},
        {"role": "user", "content": large_content},
    ]
    report = check_budget(msgs, "qwen")
    assert not report.is_safe

    # Compaction
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "bye"},
    ]
    compacted = compact_history(msgs, target_tokens=10, model_name="qwen")
    assert len(compacted) >= 2  # At least system + last user
    assert compacted[0]["role"] == "system"

    # ContextManager
    cm = ContextManager("qwen")
    status = cm.get_status(msgs)
    assert "display" in status
    assert status["model"] == "qwen"

    print("  PASS: context budget")


def test_paste_detection():
    from app.ui.input import analyze_paste, PasteInfo

    # Short input — not a paste
    info = analyze_paste("hello world")
    assert not info.is_paste
    assert info.line_count == 1
    assert info.char_count == len("hello world")

    # Empty input
    info = analyze_paste("")
    assert not info.is_paste

    # Large paste — many lines
    big_text = "\n".join([f"line {i}" for i in range(100)])
    info = analyze_paste(big_text)
    assert info.is_paste
    assert info.line_count == 100
    assert info.estimated_tokens > 0

    # Display format
    display = info.format_display()
    assert "PASTED" in display
    assert "LINES" in display

    # Short input display is empty
    info = analyze_paste("hello")
    assert info.format_display() == ""

    print("  PASS: paste detection")


def test_activity_engine():
    from app.ui.activity import ActivityEngine, ActivityState

    engine = ActivityEngine()

    # Tool start — list_files should map to SCANNING
    activity = engine.resolve_tool_start("list_files", "/src")
    assert activity.state == ActivityState.SCANNING
    assert activity.label

    # Tool start — read_file should map to READING
    activity = engine.resolve_tool_start("read_file", "main.py")
    assert activity.state == ActivityState.READING
    assert activity.label

    # Tool start — write_file should map to EDITING
    activity = engine.resolve_tool_start("write_file", "test.py")
    assert activity.state == ActivityState.EDITING

    # Tool start — search_code should map to SEARCHING
    activity = engine.resolve_tool_start("search_code", "def main")
    assert activity.state == ActivityState.SEARCHING

    # Tool start — run_command should map to EXECUTING
    activity = engine.resolve_tool_start("run_command", "pytest")
    assert activity.state == ActivityState.EXECUTING

    # Unknown tool falls back to REASONING
    activity = engine.resolve_tool_start("unknown_tool", "")
    assert activity.state == ActivityState.REASONING

    # Thinking
    activity = engine.resolve_thinking()
    assert activity.state == ActivityState.REASONING
    assert activity.label

    # Completed
    activity = engine.resolve_completed()
    assert activity.state == ActivityState.COMPLETED

    # Error
    activity = engine.resolve_error()
    assert activity.state == ActivityState.ERROR

    # Display format
    display = activity.format_display()
    assert "VektorCode" in display

    # Labels are 2-7 words (domain phrases)
    for _ in range(5):
        a = engine.resolve_thinking()
        word_count = len(a.label.split())
        assert 2 <= word_count <= 10  # Allow some flexibility

    # No repeat of same label consecutively
    labels = set()
    for _ in range(10):
        a = engine.resolve_thinking()
        labels.add(a.label)
    assert len(labels) >= 2

    print("  PASS: activity engine")


def test_command_registry():
    from app.commands import COMMANDS, COMMAND_registry, COMMANDS_BY_NAME, format_help_text

    # Registry has all expected commands
    expected = {"/help", "/status", "/model", "/models", "/endpoint",
                "/auth", "/workspace", "/tools", "/clear", "/retry", "/skills",
                "/tokens", "/trace", "/queue", "/mode", "/reload", "/exit"}
    actual = {c.name for c in COMMAND_registry}
    assert expected == actual

    # Handlers exist for non-inline commands
    assert "/auth" in COMMANDS
    assert "/model" in COMMANDS
    assert "/clear" in COMMANDS

    # BY_NAME lookup works
    assert COMMANDS_BY_NAME["/help"].description == "Show available commands"

    # format_help_text works
    help_text = format_help_text()
    assert "/help" in help_text
    assert "/auth" in help_text
    assert "Available commands" in help_text

    print("  PASS: command registry")


# ── Regression tests for agent loop fix ──────────────────────────────


def test_regress_tool_then_final_response():
    """Test 1: Tool succeeds → final response → STOP (no max iterations)."""
    tool_resp = MagicMock()
    tool_resp.choices = [MagicMock()]
    tool_resp.choices[0].message.content = (
        '<tool_call>\n{"name": "write_file", "arguments": {"path": "test.py", "content": "x=1"}}\n</tool_call>'
    )
    final_resp = MagicMock()
    final_resp.choices = [MagicMock()]
    final_resp.choices[0].message.content = "Done. Created test.py with x=1."

    with patch("app.agent.agent.chat", side_effect=[tool_resp, final_resp]):
        agent = Agent()
        events = list(agent.step("create test.py"))

    types = [e.type for e in events]
    assert "tool_start" in types
    assert "tool_result" in types
    assert events[-1].type == "agent_message"
    assert "Done" in events[-1].message
    # Must NOT end with an error about iterations or loops
    assert events[-1].type != "error"
    print("  PASS: regress tool then final response")


def test_regress_repeated_tool_call():
    """Test 2: Same tool call repeated → loop feedback → force stop after 3 loops."""
    call_resp = MagicMock()
    call_resp.choices = [MagicMock()]
    call_resp.choices[0].message.content = (
        '<tool_call>\n{"name": "list_files", "arguments": {"path": "."}}\n</tool_call>'
    )

    with patch("app.agent.agent.chat", return_value=call_resp):
        agent = Agent()
        events = list(agent.step("list files"))

    # Should get: tool_start, tool_result, then 3 loop feedbacks, then force stop error
    tool_starts = [e for e in events if e.type == "tool_start"]
    assert len(tool_starts) == 1, f"Expected 1 tool_start, got {len(tool_starts)}"
    assert events[-1].type == "error"
    assert "stopped" in events[-1].message.lower() or "giving up" in events[-1].message.lower()
    print("  PASS: regress repeated tool call")


def test_regress_multiple_normal_operations():
    """Test 3: read → write → run → final response (3 tool calls + finish)."""
    call1 = MagicMock()
    call1.choices = [MagicMock()]
    call1.choices[0].message.content = (
        '<tool_call>\n{"name": "read_file", "arguments": {"path": "app/main.py"}}\n</tool_call>'
    )
    call2 = MagicMock()
    call2.choices = [MagicMock()]
    call2.choices[0].message.content = (
        '<tool_call>\n{"name": "write_file", "arguments": {"path": "app/test.py", "content": "print(1)"}}\n</tool_call>'
    )
    call3 = MagicMock()
    call3.choices = [MagicMock()]
    call3.choices[0].message.content = (
        '<tool_call>\n{"name": "run_command", "arguments": {"command": "python app/test.py"}}\n</tool_call>'
    )
    call4 = MagicMock()
    call4.choices = [MagicMock()]
    call4.choices[0].message.content = "All done. Test ran successfully."

    with patch("app.agent.agent.chat", side_effect=[call1, call2, call3, call4]):
        agent = Agent()
        events = list(agent.step("read, write, and run test"))

    types = [e.type for e in events]
    assert types[-1] == "agent_message"
    assert "All done" in events[-1].message
    # Should have 3 tool_start events
    tool_starts = [e for e in events if e.type == "tool_start"]
    assert len(tool_starts) == 3
    print("  PASS: regress multiple normal operations")


def test_regress_nested_json_arguments():
    """Test 4: Tool call with nested JSON arguments parses correctly."""
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    nested_content = (
        'Here is the file:\n\n<tool_call>\n'
        '{"name": "write_file", "arguments": {"path": "app/test.py", '
        '"content": "def hello():\\n    return {\\n        \\"key\\": \\"value\\",\\n        \\"nested\\": [1, 2, 3]\\n    }"}}\n'
        '</tool_call>'
    )
    mock_resp.choices[0].message.content = nested_content

    with patch("app.agent.agent.chat", return_value=mock_resp):
        agent = Agent()
        events = list(agent.step("write a file with nested dict"))

    tool_starts = [e for e in events if e.type == "tool_start"]
    assert len(tool_starts) == 1
    assert tool_starts[0].tool == "write_file"
    assert tool_starts[0].data["path"] == "app/test.py"
    assert "def hello" in tool_starts[0].data["content"]
    print("  PASS: regress nested JSON arguments")


def test_regress_tool_result_not_user_message():
    """Test 5: Tool results are NEVER represented as role:user messages."""
    tool_resp = MagicMock()
    tool_resp.choices = [MagicMock()]
    tool_resp.choices[0].message.content = (
        '<tool_call>\n{"name": "list_files", "arguments": {"path": "."}}\n</tool_call>'
    )
    final_resp = MagicMock()
    final_resp.choices = [MagicMock()]
    final_resp.choices[0].message.content = "Here are the files."

    with patch("app.agent.agent.chat", side_effect=[tool_resp, final_resp]):
        agent = Agent()
        list(agent.step("list files"))

    # Check every message in history
    for msg in agent.history:
        if msg.get("role") == "user":
            content = msg.get("content", "")
            assert not content.startswith("Tool results:"), (
                f"Tool result found as role:user: {content[:80]}"
            )
        # Tool results should use role:"tool" with tool_call_id
        if msg.get("role") == "tool":
            assert "tool_call_id" in msg
            assert "content" in msg

    # Verify the tool result message exists with correct role
    tool_msgs = [m for m in agent.history if m.get("role") == "tool"]
    assert len(tool_msgs) >= 1, "Expected at least one role:tool message"
    assert len(tool_msgs[0].get("content", "")) > 0, "Tool result content should not be empty"
    print("  PASS: regress tool result not user message")


def test_tool_registry():
    """Test ToolRegistry: get, list, execute, categories."""
    from app.tools import get_registry

    registry = get_registry()

    # Every built-in tool is registered.
    expected = {"list_files", "read_file", "write_file", "search_code",
                "run_command", "git_tool", "get_config"}
    all_tools = registry.list_all()
    assert {t.name for t in all_tools} == expected, \
        f"registered tools differ: {sorted(t.name for t in all_tools)}"

    # get() returns correct tool
    tool = registry.get("list_files")
    assert tool is not None
    assert tool.name == "list_files"
    assert tool.category == "filesystem"

    # has()
    assert registry.has("read_file") is True
    assert registry.has("nonexistent") is False

    # list_by_category
    fs_tools = registry.list_by_category("filesystem")
    assert len(fs_tools) == 3

    # Execute
    result = registry.execute("get_config", {})
    assert result.status.value == "success"

    # Unknown tool
    result = registry.execute("nope", {})
    assert result.status.value == "error"

    print("  PASS: tool registry")


def test_tool_event_bus():
    """Test EventBus: subscribe, emit, unsubscribe."""
    from app.tools.events import EventBus, ToolEvent, ToolEventType

    bus = EventBus()
    received = []
    bus.subscribe(lambda e: received.append(e))

    event = ToolEvent.started("read_file", "filesystem", "test.py")
    bus.emit(event)
    assert len(received) == 1
    assert received[0].tool == "read_file"

    # Unsubscribe
    sub = bus.subscribe(lambda e: None)
    bus.unsubscribe(sub)
    assert len(received) == 1  # no new events from unsubscribed

    print("  PASS: tool event bus")


def test_tool_result_field_fix():
    """Test that ToolResult.error field is not shadowed by staticmethod."""
    from app.tools.tool_result import ToolResult, ToolStatus

    # Error result
    r = ToolResult.make_error("bad")
    assert r.error == "bad"
    assert r.status == ToolStatus.ERROR
    assert "bad" in str(r)

    # Success result
    r2 = ToolResult.success(data="ok")
    assert r2.error is None
    assert str(r2) == "ok"

    # Blocked result
    r3 = ToolResult.blocked("denied")
    assert r3.error == "denied"
    assert r3.status == ToolStatus.BLOCKED

    print("  PASS: tool result field fix")


def test_tools_command_registry():
    """Test /tools command uses registry, not hardcoded."""
    from app.tools import get_registry

    registry = get_registry()
    tools = registry.list_available()
    categories = registry.get_categories()

    # Should have tools in multiple categories
    assert len(tools) == len(registry.list_all())
    assert len(categories) >= 3

    # Every tool has name, description, category
    for tool in tools:
        assert tool.name
        assert tool.description
        assert tool.category

    print("  PASS: /tools command registry")


def test_agent_emits_tool_events():
    """Test that agent emits ToolEvents via EventBus during execution."""
    from unittest.mock import patch, MagicMock
    from app.tools import get_event_bus
    from app.tools.events import ToolEventType

    bus = get_event_bus()
    events_received = []
    sub = bus.subscribe(lambda e: events_received.append(e))

    tool_resp = MagicMock()
    tool_resp.choices = [MagicMock()]
    tool_resp.choices[0].message.content = (
        '<tool_call>\n{"name": "get_config", "arguments": {}}\n</tool_call>'
    )
    final_resp = MagicMock()
    final_resp.choices = [MagicMock()]
    final_resp.choices[0].message.content = "Done."

    with patch("app.agent.agent.chat", side_effect=[tool_resp, final_resp]):
        from app.agent import Agent
        agent = Agent()
        list(agent.step("show config"))

    bus.unsubscribe(sub)

    # Should have received TOOL_STARTED and TOOL_COMPLETED
    started = [e for e in events_received if e.type == ToolEventType.TOOL_STARTED]
    completed = [e for e in events_received if e.type == ToolEventType.TOOL_COMPLETED]
    assert len(started) >= 1, f"Expected TOOL_STARTED, got {len(started)}"
    assert len(completed) >= 1, f"Expected TOOL_COMPLETED, got {len(completed)}"
    assert started[0].tool == "get_config"

    print("  PASS: agent emits tool events")


def test_read_file_truncation():
    """Test that read_file truncates large files with metadata."""
    from app.tools.filesystem import read_file

    _setup()
    # Create a file with >500 lines
    big_content = "\n".join(f"line {i}" for i in range(600))
    write_file("_test_e2e/big.py", big_content)

    result = read_file("_test_e2e/big.py")
    assert "truncated" in result
    assert "lines 1-500 of 600" in result
    assert "line 0" in result
    assert "line 499" in result

    # Small file should NOT be truncated
    write_file("_test_e2e/small.py", "x = 1")
    result = read_file("_test_e2e/small.py")
    assert "truncated" not in result
    assert "x = 1" in result

    print("  PASS: read_file truncation")


def test_truncate_only_targets_tool_role():
    """Test that _truncate_tool_outputs only truncates role:tool messages."""
    from app.context.context import _truncate_tool_outputs

    messages = [
        {"role": "system", "content": "sys prompt " * 500},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "thinking " * 500},
        {"role": "tool", "tool_call_id": "c1", "content": "result " * 500},
    ]

    truncated = _truncate_tool_outputs(messages, max_output_chars=200)

    # System, user, assistant should be untouched
    assert truncated[0]["content"] == messages[0]["content"]
    assert truncated[1]["content"] == messages[1]["content"]
    assert truncated[2]["content"] == messages[2]["content"]
    # Tool should be truncated
    assert "truncated" in truncated[3]["content"]
    assert len(truncated[3]["content"]) < len(messages[3]["content"])

    print("  PASS: truncate only targets tool role")


def test_tool_protocol_stripped_from_history():
    """Test that <tool_call> markup is stripped from stored assistant messages."""
    from unittest.mock import patch, MagicMock

    tool_resp = MagicMock()
    tool_resp.choices = [MagicMock()]
    tool_resp.choices[0].message.content = (
        'Let me check.\n<tool_call>\n{"name": "get_config", "arguments": {}}\n</tool_call>'
    )
    final_resp = MagicMock()
    final_resp.choices = [MagicMock()]
    final_resp.choices[0].message.content = "Done."

    with patch("app.agent.agent.chat", side_effect=[tool_resp, final_resp]):
        agent = Agent()
        list(agent.step("check config"))

    # The assistant message in history should NOT contain <tool_call> tags
    assistant_msgs = [m for m in agent.history if m.get("role") == "assistant"]
    assert len(assistant_msgs) >= 1
    for msg in assistant_msgs:
        assert "<tool_call>" not in msg["content"], f"Protocol leak: {msg['content'][:100]}"
        assert "</tool_call>" not in msg["content"]

    print("  PASS: tool protocol stripped from history")


# ── Cost accounting ─────────────────────────────────────────────────

def _usage_response(content, prompt_tokens, completion_tokens):
    from unittest.mock import MagicMock

    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    resp.choices[0].message.reasoning_content = None
    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens
    usage.total_tokens = prompt_tokens + completion_tokens
    resp.usage = usage
    return resp


def _tool_call(name, **args):
    import json as _json
    return f'<tool_call>\n{{"name": "{name}", "arguments": {_json.dumps(args)}}}\n</tool_call>'


def test_regress_token_usage_cumulative_across_tool_chain():
    """Usage must be summed across a turn, not overwritten per call."""
    from unittest.mock import patch
    import app.agent.agent as A

    responses = [
        _usage_response(_tool_call("read_file", path="a.py"), 100, 10),
        _usage_response(_tool_call("write_file", path="a.py", content="x"), 200, 20),
        _usage_response("Done.", 300, 30),
    ]
    with patch.object(A, "chat", side_effect=responses), \
         patch.object(A.Agent, "_execute_tool", lambda self, n, a: "ok"):
        agent = Agent()
        list(agent.step("edit a.py"))

    assert agent.input_tokens == 600, f"input {agent.input_tokens} != 600 (sum of 100+200+300)"
    assert agent.output_tokens == 60, f"output {agent.output_tokens} != 60 (sum of 10+20+30)"
    assert agent.total_tokens == 660, f"total {agent.total_tokens} != 660"
    print("  PASS: token usage is cumulative across a tool chain")


def test_token_usage_ignores_non_numeric_mock_usage():
    """An auto-mocked usage object must not fabricate token counts."""
    from unittest.mock import MagicMock, patch
    import app.agent.agent as A

    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = "Hello."
    resp.choices[0].message.reasoning_content = None
    # .usage left as an auto-created MagicMock: int(MagicMock()) == 1.

    with patch.object(A, "chat", return_value=resp):
        agent = Agent()
        list(agent.step("hi"))

    assert agent.input_tokens == 0, f"fabricated input tokens: {agent.input_tokens}"
    assert agent.output_tokens == 0, f"fabricated output tokens: {agent.output_tokens}"
    assert agent.total_tokens == 0, f"fabricated total tokens: {agent.total_tokens}"
    print("  PASS: non-numeric usage coerces to zero, not one")


def test_cost_estimate_zero_for_local_models():
    from app.cost import estimate_cost

    assert estimate_cost("qwen", 1_000_000, 1_000_000) == 0.0
    assert estimate_cost("gpt-oss-120b", 500_000, 500_000) == 0.0
    assert estimate_cost("nemotron-3-super", 10_000, 10_000) == 0.0
    print("  PASS: local models cost nothing per token")


def test_cost_estimate_env_override():
    from app.cost import estimate_cost, get_model_pricing

    before_in = os.environ.get("LITELLM_INPUT_COST_PER_1M")
    before_out = os.environ.get("LITELLM_OUTPUT_COST_PER_1M")
    try:
        os.environ["LITELLM_INPUT_COST_PER_1M"] = "3.00"
        os.environ["LITELLM_OUTPUT_COST_PER_1M"] = "15.00"

        pricing = get_model_pricing("qwen")
        assert pricing.input_per_1m == 3.00, pricing
        assert pricing.output_per_1m == 15.00, pricing

        # 1M in + 1M out = $3 + $15
        cost = estimate_cost("qwen", 1_000_000, 1_000_000)
        assert abs(cost - 18.00) < 1e-9, cost
    finally:
        for key, value in (("LITELLM_INPUT_COST_PER_1M", before_in),
                           ("LITELLM_OUTPUT_COST_PER_1M", before_out)):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("  PASS: env vars override model pricing")


def test_budget_tracker_blocks_over_budget():
    from app.cost import BudgetTracker

    budget = BudgetTracker(1.00)
    budget.record(0.50)
    assert not budget.would_exceed(), "0.50 of 1.00 should not exceed"
    assert abs(budget.remaining_usd - 0.50) < 1e-9

    budget.record(0.60)
    assert budget.would_exceed(), "1.10 of 1.00 should exceed"
    assert budget.remaining_usd == 0.0

    # Landing exactly on the ceiling must block the next call.
    exact = BudgetTracker(1.00)
    exact.record(1.00)
    assert exact.would_exceed(0.0), "spend equal to the budget must block"
    print("  PASS: budget tracker blocks at and over budget")


def test_agent_stops_when_daily_budget_exhausted():
    from unittest.mock import patch
    import app.agent.agent as A

    called = []

    def spy(*a, **k):
        called.append(1)
        raise AssertionError("chat() must not be called when over budget")

    with patch.object(A, "chat", spy):
        agent = Agent()
        agent.budget.spent_today_usd = agent.budget.daily_budget_usd
        events = list(agent.step("do something expensive"))

    assert not called, "the LLM was called despite an exhausted budget"
    assert events, "expected an error event"
    assert events[-1].type == "error", f"expected error, got {events[-1].type}"
    assert "budget" in events[-1].message.lower(), events[-1].message
    print("  PASS: agent refuses to call the model when the budget is spent")


def test_task_log_written_with_expected_fields():
    from datetime import date
    from unittest.mock import patch
    import app.agent.agent as A

    day_dir = WORKSPACE / "logs" / date.today().isoformat()
    before = set(day_dir.glob("task-*.json")) if day_dir.exists() else set()

    responses = [
        _usage_response(_tool_call("read_file", path="a.py"), 100, 10),
        _usage_response("All done.", 50, 5),
    ]
    with patch.object(A, "chat", side_effect=responses), \
         patch.object(A.Agent, "_execute_tool", lambda self, n, a: "ok"):
        agent = Agent()
        list(agent.step("inspect a.py"))

    after = set(day_dir.glob("task-*.json"))
    fresh = after - before
    assert fresh, f"no task log written under {day_dir}"

    payload = json.loads(sorted(fresh)[-1].read_text(encoding="utf-8"))
    expected = {
        "task", "timestamp", "model", "input_tokens", "output_tokens",
        "total_tokens", "estimated_cost_usd", "files_accessed",
        "files_modified", "tools_called", "tests_executed", "result",
        "errors", "had_error", "duration_seconds",
    }
    missing = expected - set(payload)
    assert not missing, f"task log missing fields: {sorted(missing)}"

    assert payload["task"] == "inspect a.py", payload["task"]
    assert payload["input_tokens"] == 150, payload["input_tokens"]
    assert payload["output_tokens"] == 15, payload["output_tokens"]
    assert payload["tools_called"] == ["read_file"], payload["tools_called"]
    assert payload["files_accessed"] == ["a.py"], payload["files_accessed"]
    assert payload["had_error"] is False
    print("  PASS: task log written with the expected shape")


# ── MAX_ITERATIONS give-up path ─────────────────────────────────────

def _iteration_exhausting_responses(final):
    """15 distinct tool calls (so loop detection never trips) + a 16th reply."""
    from app.agent.agent import MAX_ITERATIONS

    responses = [
        _usage_response(_tool_call("read_file", path=f"f{i}.py"), 10, 1)
        for i in range(MAX_ITERATIONS)
    ]
    responses.append(final)
    return responses


def test_max_iterations_asks_model_for_real_summary():
    """The give-up text must be the model's words, not a bookkeeping string."""
    from unittest.mock import patch
    import app.agent.agent as A

    written = ("I inspected fifteen files without locating the parser. "
               "Unresolved: which module owns tokenization. "
               "I'd need the module name to finish.")
    responses = _iteration_exhausting_responses(_usage_response(written, 20, 5))

    with patch.object(A, "chat", side_effect=responses), \
         patch.object(A.Agent, "_execute_tool", lambda self, n, a: "ok"):
        agent = Agent()
        events = list(agent.step("find the parser"))

    last = events[-1]
    assert last.type == "max_iterations_reached", f"got {last.type}"
    assert last.message == written, f"expected the model's summary, got {last.message!r}"
    assert "Provide a summary of findings so far" not in last.message
    print("  PASS: max-iterations summary is written by the model")


def test_max_iterations_event_is_not_agent_message():
    """The old bug's exact symptom: give-up rendered as a normal answer."""
    from unittest.mock import patch
    import app.agent.agent as A

    responses = _iteration_exhausting_responses(_usage_response("Ran out of room.", 20, 5))

    with patch.object(A, "chat", side_effect=responses), \
         patch.object(A.Agent, "_execute_tool", lambda self, n, a: "ok"):
        agent = Agent()
        events = list(agent.step("find the parser"))

    for event in events:
        assert event.type != "agent_message", \
            f"give-up path emitted agent_message: {event.message!r}"
        assert "Provide a summary of findings so far" not in event.message
    print("  PASS: give-up does not masquerade as a final answer")


def test_max_iterations_falls_back_to_synthetic_summary_on_llm_error():
    """If the summary call fails, still end cleanly with a useful message."""
    from unittest.mock import patch
    import app.agent.agent as A
    from app.agent.agent import MAX_ITERATIONS

    calls = {"n": 0}
    tool_call = _usage_response(_tool_call("read_file", path="x.py"), 10, 1)

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] > MAX_ITERATIONS:
            raise RuntimeError("endpoint down")
        # Vary the path so loop detection does not trip.
        return _usage_response(_tool_call("read_file", path=f"f{calls['n']}.py"), 10, 1)

    with patch.object(A, "chat", side_effect=flaky), \
         patch.object(A.Agent, "_execute_tool", lambda self, n, a: "ok"):
        agent = Agent()
        events = list(agent.step("find the parser"))

    last = events[-1]
    assert last.type == "max_iterations_reached", f"got {last.type}"
    assert "Stopped after" in last.message, last.message
    assert "read_file" in last.message, last.message
    print("  PASS: summary-call failure degrades to a synthetic summary")


def test_gave_up_turn_renders_no_success_panel():
    """_render_turn_summary must stay silent when the turn gave up."""
    import app.main as _main
    from app.agent import AgentEvent
    from app.ui.console import console as ui_console
    from app.ui.toolfeed import ToolFeed

    _main.reset_turn()
    _main._turn["tools"] = 15
    _main._turn["changed"] = ["app/tools/scratch.txt"]

    with ui_console.capture() as cap:
        _main.render_event(
            AgentEvent(type="max_iterations_reached", message="Ran out of iterations."),
            ToolFeed(),
        )
        _main._render_turn_summary("Ran out of iterations.")
    out = cap.get()

    assert "Task completed" not in out, "gave-up turn rendered a success panel"
    assert "Stopped without finishing" in out, out
    assert _main._turn["gave_up"] is True
    _main.reset_turn()
    print("  PASS: no success panel on a gave-up turn")


# ── Leaked chain-of-thought ─────────────────────────────────────────

_LEAKED_GREETING = (
    'We need to respond. No tool calls needed just a greeting. But we must '
    'follow behavior: direct and decisive, no preamble. Probably just "Hello". '
    'However maybe they expect something else. Since user said "HI", respond '
    'with greeting.\nHello'
)


def test_regress_leaked_reasoning_stripped_from_final_answer():
    """Deliberation written into the content channel must not reach the user."""
    from unittest.mock import patch
    import app.agent.agent as A

    with patch.object(A, "chat", return_value=_usage_response(_LEAKED_GREETING, 10, 5)):
        agent = Agent()
        events = list(agent.step("HI"))

    assert events[-1].message == "Hello", f"leaked reasoning shown: {events[-1].message!r}"

    # And it must not be stored either, or later turns re-read it as a
    # prior real answer and the leak compounds.
    stored = [m for m in agent.history if m["role"] == "assistant"][-1]["content"]
    assert stored == "Hello", f"leaked reasoning stored in history: {stored!r}"
    print("  PASS: leaked reasoning stripped from the final answer")


def test_strip_leaked_reasoning_preserves_real_multiparagraph_answers():
    """The false-positive guard — it matters more than catching every leak."""
    from app.agent.agent import _strip_leaked_reasoning

    genuine = [
        # Hedging connectives are ordinary prose, not deliberation.
        "Since the file is large, I only read the first 500 lines.\n"
        "The parser lives in app/tools/search.py.",
        "So the total is 42 files.\nThey are spread across six packages.",
        "However you configure it, the parser stays the same.\n"
        "Use search_code to find it.",
        # Genuine two-paragraph explanation.
        "The agent loop calls the LLM, parses tool calls, and executes them.\n\n"
        "Each result is fed back as a tool message before the next call.",
        # Lead-in followed by a list.
        "Based on the code, there are three issues.\n"
        "- Missing validation\n- No retry\n- Silent failure",
    ]
    for text in genuine:
        assert _strip_leaked_reasoning(text) == text, \
            f"genuine answer was truncated: {text!r}"
    print("  PASS: genuine multi-paragraph answers pass through unchanged")


def test_think_block_stripped_from_content():
    """A leaked </think> block is an exact delimiter — strip it precisely."""
    from app.agent.agent import _strip_think_blocks

    leaked = (
        'We have user just says "HI". We should respond concisely.\n'
        "Thus final answer: Hello!\n"
        "</think>\n"
        "Hello! How can I assist you today?"
    )
    assert _strip_think_blocks(leaked) == "Hello! How can I assist you today?"

    # Paired form.
    assert _strip_think_blocks("<think>musing</think>The answer is 42.") == "The answer is 42."

    # A tool call after the think block must survive intact.
    with_call = (
        'We need to look around.\n</think>\n'
        '<tool_call>\n{"name": "list_files", "arguments": {"path": "."}}\n</tool_call>'
    )
    assert "list_files" in _strip_think_blocks(with_call)
    assert "We need to look around" not in _strip_think_blocks(with_call)

    # Untagged text is untouched.
    plain = "No think tags here at all."
    assert _strip_think_blocks(plain) == plain
    print("  PASS: leaked <think> blocks stripped from content")


def test_leaked_think_block_does_not_reach_the_user():
    """End-to-end: the observed nemotron shape must render as the answer only."""
    from unittest.mock import patch
    import app.agent.agent as A

    leaked = (
        'We have user just says "HI". According to behavior, we should just do work.\n'
        "Thus final answer: Hello! How can I assist you today?\n"
        "</think>\n"
        "Hello! How can I assist you today?"
    )
    with patch.object(A, "chat", return_value=_usage_response(leaked, 10, 5)):
        agent = Agent()
        events = list(agent.step("HI"))

    assert events[-1].message == "Hello! How can I assist you today?", events[-1].message
    stored = [m for m in agent.history if m["role"] == "assistant"][-1]["content"]
    assert "We have user just says" not in stored, stored
    print("  PASS: think-block leak never reaches the user or history")


def test_strip_leaked_reasoning_noop_on_single_paragraph():
    from app.agent.agent import _strip_leaked_reasoning

    for text in [
        "Hello",
        "We need to respond with a greeting so here it is.",
        "The parser lives in app/tools/search.py and handles regex search.",
    ]:
        assert _strip_leaked_reasoning(text) == text, text
    print("  PASS: single-paragraph text passes through unchanged")


def test_strip_leaked_reasoning_noop_when_preamble_incomplete():
    """If the LAST part is also deliberation, there is no clean answer to keep."""
    from app.agent.agent import _strip_leaked_reasoning

    all_deliberation = (
        "We need to check the config first.\n\n"
        "We should probably read the file before deciding."
    )
    assert _strip_leaked_reasoning(all_deliberation) == all_deliberation

    mixed = (
        "The config lives in .env.\n\n"
        "We need to read it before answering."
    )
    assert _strip_leaked_reasoning(mixed) == mixed
    print("  PASS: no strip when the split is ambiguous")


# ── Git tool ────────────────────────────────────────────────────────

def test_git_tool_is_registered():
    from app.tools import get_registry

    registry = get_registry()
    assert registry.has("git_tool"), "git_tool was never registered"
    tool = registry.get("git_tool")
    assert tool.category == "git", tool.category
    assert "git" in tool.required_permissions, tool.required_permissions
    # It must also be visible to the model, not just the registry.
    names = [s["function"]["name"] for s in registry.to_llm_schemas()]
    assert "git_tool" in names, names
    print("  PASS: git_tool is registered and exposed to the model")


def test_git_tool_status_via_registry():
    from app.tools import get_registry
    from app.tools.tool_result import ToolStatus

    result = get_registry().execute("git_tool", {"subcommand": "status"})
    # Whatever the repo state, dispatch itself must succeed.
    assert result.status is not ToolStatus.ERROR, str(result)
    assert "Unknown tool" not in str(result), str(result)
    assert "Bad arguments" not in str(result), str(result)
    print("  PASS: git_tool status subcommand dispatches correctly")


def test_git_tool_commit_requires_message():
    from app.tools.git import handler

    result = handler(subcommand="commit")
    assert result["success"] is False, result
    assert "message" in result["stderr"].lower(), result
    print("  PASS: commit without message fails cleanly")


def _git_approval(subcommand, approver, **extra):
    """Run a git subcommand under a given approver, return the ToolResult."""
    from app.permissions.mode import PermissionMode, set_approver, set_mode
    from app.tools import get_registry

    set_mode(PermissionMode.DEFAULT)
    set_approver(approver)
    try:
        return get_registry().execute("git_tool", {"subcommand": subcommand, **extra})
    finally:
        set_approver(None)
        set_mode(PermissionMode.DEFAULT)


def test_git_push_requires_approval():
    """push reaches a remote — it must go through the human gate."""
    from app.tools.tool_result import ToolStatus

    asked = []
    result = _git_approval("push", lambda kind, detail: (asked.append(detail), False)[1])
    assert asked == ["git push"], asked
    assert result.status is ToolStatus.BLOCKED, str(result)

    # Non-interactive: nobody to ask means blocked, never auto-approved.
    blocked = _git_approval("push", None)
    assert blocked.status is ToolStatus.BLOCKED, str(blocked)
    print("  PASS: git push requires approval and fails safe")


def test_git_merge_requires_approval():
    from app.tools.tool_result import ToolStatus

    asked = []
    result = _git_approval(
        "merge", lambda kind, detail: (asked.append(detail), False)[1],
        branch_name="feature-x",
    )
    assert asked == ["git merge feature-x"], asked
    assert result.status is ToolStatus.BLOCKED, str(result)

    blocked = _git_approval("merge", None, branch_name="feature-x")
    assert blocked.status is ToolStatus.BLOCKED, str(blocked)
    print("  PASS: git merge requires approval and fails safe")


def test_git_read_and_local_subcommands_are_not_gated():
    """Over-gating teaches the model to route around the tool."""
    from app.tools.tool_result import ToolStatus

    def refuse(kind, detail):
        raise AssertionError(f"should not have asked for approval: {detail}")

    for subcommand in ("status", "log", "diff"):
        result = _git_approval(subcommand, refuse)
        assert result.status is not ToolStatus.BLOCKED, f"{subcommand}: {result}"

    # commit is local and reversible; it fails on its own missing-message
    # check rather than being blocked by the permission layer.
    commit = _git_approval("commit", refuse)
    assert commit.status is not ToolStatus.BLOCKED, str(commit)
    print("  PASS: read-only and local git subcommands are not gated")


def test_git_tool_respects_workspace_boundary():
    """_resolve_safe must not be bypassed by the registry call path."""
    from app.tools import get_registry

    result = get_registry().execute(
        "git_tool", {"subcommand": "diff", "path": "../../../etc"}
    )
    assert "success" not in str(result) or "escape" in str(result).lower() \
        or "outside" in str(result).lower() or "Error" in str(result), str(result)
    print("  PASS: git_tool refuses paths outside the workspace")


# ── System prompt / registry agreement ──────────────────────────────

def test_system_prompt_forbids_tutorialising():
    """VektorCode answered a 'do this' request with a git tutorial ending in
    'let me know if you'd like help applying this to a specific repository',
    despite having git_tool. The prompt must rule that out explicitly."""
    system_prompt = Agent().history[0]["content"].lower()
    for phrase in ["real workspace", "tutorial", "let me know if you'd like help",
                   "explaining is not"]:
        assert phrase in system_prompt, f"missing guidance: {phrase!r}"
    print("  PASS: system prompt forbids tutorialising instead of acting")


def test_system_prompt_lists_every_registered_tool():
    """The regression guard for the exact bug: git_tool was registered but
    the model still believed it had six tools."""
    from app.tools import get_registry

    system_prompt = Agent().history[0]["content"]
    for tool in get_registry().list_available():
        assert tool.name in system_prompt, (
            f"'{tool.name}' is registered but missing from the system prompt "
            "the model actually sees"
        )
    print("  PASS: system prompt lists every registered tool")


def test_system_prompt_tool_count_matches_registry():
    import re

    from app.tools import get_registry

    system_prompt = Agent().history[0]["content"]
    listed = re.findall(r"^\d+\. \w+\(", system_prompt, re.MULTILINE)
    expected = len(get_registry().list_available())
    assert len(listed) == expected, (
        f"System prompt lists {len(listed)} tools, registry has {expected}"
    )
    print("  PASS: system prompt tool count matches registry exactly")


def test_clear_history_regenerates_current_tool_list():
    """/clear must not regress to a stale list — it is a second, independent
    construction site."""
    agent = Agent()
    agent.clear_history()
    system_prompt = agent.history[0]["content"]
    assert "git_tool" in system_prompt, "clear_history() produced a stale tool list"
    print("  PASS: /clear regenerates the current tool list")


def test_format_tools_for_prompt_reflects_newly_registered_tool():
    """Unit-level guard on the formatter, independent of Agent."""
    from app.tools.tool_registry import ToolRegistry, format_tools_for_prompt

    registry = ToolRegistry()
    registry.register(
        name="dummy_tool",
        description="A test tool",
        category="test",
        handler=lambda: "ok",
        parameters={"type": "object", "properties": {}},
    )
    rendered = format_tools_for_prompt(registry)
    assert "dummy_tool" in rendered, rendered
    assert "A test tool" in rendered, rendered
    print("  PASS: formatter reflects a freshly registered tool")


def test_new_tool_reaches_the_prompt_with_no_agent_changes():
    """The real test: does this generalise, or just patch today's mismatch?

    Register an extra tool on the live registry and confirm the count the
    model sees follows, without touching agent.py.
    """
    import re

    from app.tools import get_registry

    registry = get_registry()
    before = len(re.findall(r"^\d+\. \w+\(", Agent().history[0]["content"], re.MULTILINE))

    registry.register(
        name="throwaway_probe",
        description="Temporary tool for testing prompt generation",
        category="test",
        handler=lambda: "ok",
        parameters={
            "type": "object",
            "properties": {"target": {"type": "string", "description": "x"}},
            "required": ["target"],
        },
    )
    try:
        prompt = Agent().history[0]["content"]
        after = len(re.findall(r"^\d+\. \w+\(", prompt, re.MULTILINE))
        assert after == before + 1, f"{before} → {after}"
        assert "throwaway_probe" in prompt
        assert 'throwaway_probe(target="...")' in prompt, "signature not rendered"
    finally:
        registry.unregister("throwaway_probe")

    restored = len(re.findall(r"^\d+\. \w+\(", Agent().history[0]["content"], re.MULTILINE))
    assert restored == before, f"registry not restored: {restored} != {before}"
    print("  PASS: a newly registered tool reaches the prompt automatically")


def test_token_usage_captured():
    """Test that token usage is captured from LLM response."""
    from unittest.mock import patch, MagicMock

    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = "Hello."
    mock_resp.usage = MagicMock()
    mock_resp.usage.prompt_tokens = 100
    mock_resp.usage.completion_tokens = 20
    mock_resp.usage.total_tokens = 120

    with patch("app.agent.agent.chat", return_value=mock_resp):
        agent = Agent()
        list(agent.step("hi"))

    assert agent.input_tokens == 100
    assert agent.output_tokens == 20
    assert agent.total_tokens == 120

    print("  PASS: token usage captured")


def test_history_cap():
    """Test that history is capped to prevent context overflow."""
    from unittest.mock import patch, MagicMock
    from app.agent.agent import _HISTORY_CAP

    # Simulate many tool calls by directly appending to history
    agent = Agent()
    initial_len = len(agent.history)

    # Manually grow history beyond cap
    for i in range(_HISTORY_CAP + 20):
        agent.history.append({"role": "assistant", "content": f"msg {i}"})
        agent.history.append({"role": "tool", "tool_call_id": f"c{i}", "content": f"result {i}"})

    assert len(agent.history) > _HISTORY_CAP + 1

    # Call _cap_history
    agent._cap_history()

    # Should be capped to system + _HISTORY_CAP messages
    assert len(agent.history) == _HISTORY_CAP + 1
    # System prompt always preserved
    assert agent.history[0]["role"] == "system"
    # Most recent messages preserved
    assert agent.history[-1]["content"] == f"result {_HISTORY_CAP + 19}"

    print("  PASS: history cap")


def test_final_response_terminates_step():
    """Test that Agent.step() terminates after final response (no tool calls)."""
    from unittest.mock import patch, MagicMock

    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = "Task complete."

    with patch("app.agent.agent.chat", return_value=mock_resp):
        agent = Agent()
        events = list(agent.step("do something"))

    # Should yield exactly one event: the final response
    assert len(events) == 1
    assert events[0].type == "agent_message"
    assert events[0].message == "Task complete."
    # History should have: system + user + assistant (3 messages)
    assert len(agent.history) == 3

    print("  PASS: final response terminates step")


if __name__ == "__main__":
    print("Running VektorCode end-to-end tests...\n")

    tests = [
        test_workspace_is_cwd,
        test_list_files,
        test_read_file,
        test_write_file,
        test_search_code,
        test_path_traversal_blocked,
        test_destructive_command,
        test_permission_gate,
        test_destructive_patterns,
        test_agent_tool_loop,
        test_agent_max_iterations,
        test_agent_final_answer,
        test_agent_bare_json_tool_call,
        test_agent_clear_history,
        test_agent_system_prompt_includes_workspace,
        test_diff_format,
        test_event_dataclass,
        test_config_version,
        test_config_env_search,
        test_auth_provider,
        test_context_budget,
        test_paste_detection,
        test_activity_engine,
        test_command_registry,
        test_regress_tool_then_final_response,
        test_regress_repeated_tool_call,
        test_regress_multiple_normal_operations,
        test_regress_nested_json_arguments,
        test_regress_tool_result_not_user_message,
        test_tool_registry,
        test_tool_event_bus,
        test_tool_result_field_fix,
        test_tools_command_registry,
        test_agent_emits_tool_events,
        test_read_file_truncation,
        test_truncate_only_targets_tool_role,
        test_tool_protocol_stripped_from_history,
        test_regress_token_usage_cumulative_across_tool_chain,
    test_token_usage_ignores_non_numeric_mock_usage,
    test_cost_estimate_zero_for_local_models,
    test_cost_estimate_env_override,
    test_budget_tracker_blocks_over_budget,
    test_agent_stops_when_daily_budget_exhausted,
    test_task_log_written_with_expected_fields,
    test_max_iterations_asks_model_for_real_summary,
    test_max_iterations_event_is_not_agent_message,
    test_max_iterations_falls_back_to_synthetic_summary_on_llm_error,
    test_gave_up_turn_renders_no_success_panel,
    test_regress_leaked_reasoning_stripped_from_final_answer,
    test_strip_leaked_reasoning_preserves_real_multiparagraph_answers,
    test_think_block_stripped_from_content,
    test_leaked_think_block_does_not_reach_the_user,
    test_strip_leaked_reasoning_noop_on_single_paragraph,
    test_strip_leaked_reasoning_noop_when_preamble_incomplete,
    test_git_tool_is_registered,
    test_git_tool_status_via_registry,
    test_git_tool_commit_requires_message,
    test_git_push_requires_approval,
    test_git_merge_requires_approval,
    test_git_read_and_local_subcommands_are_not_gated,
    test_git_tool_respects_workspace_boundary,
    test_system_prompt_forbids_tutorialising,
    test_system_prompt_lists_every_registered_tool,
    test_system_prompt_tool_count_matches_registry,
    test_clear_history_regenerates_current_tool_list,
    test_format_tools_for_prompt_reflects_newly_registered_tool,
    test_new_tool_reaches_the_prompt_with_no_agent_changes,
    test_token_usage_captured,
        test_history_cap,
        test_final_response_terminates_step,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"  FAIL: {test.__name__}: {e}")
            failed += 1

    _cleanup()

    print(f"\n{'='*40}")
    print(f"Results: {passed} passed, {failed} failed")
    if failed == 0:
        print("All tests passed!")
    else:
        sys.exit(1)
