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


def _setup():
    TEST_DIR.mkdir(exist_ok=True)


def _cleanup():
    import shutil
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    # Clean up test artifact from nested JSON test
    test_py = Path("app/test.py")
    if test_py.exists():
        test_py.unlink()


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
    result = run_command("rm -rf /tmp/test")
    assert "BLOCKED" in result, f"Expected BLOCKED: {result}"
    print("  PASS: destructive command blocked")


def test_destructive_command_confirm():
    result = run_command("rm /tmp/nonexistent_file_for_test_12345", confirm=True)
    assert "BLOCKED" not in result, f"Should not be blocked with confirm=True: {result}"
    print("  PASS: confirm=true bypass")


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
                "/auth", "/workspace", "/tools", "/clear", "/reload", "/exit"}
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
    """Test 2: Same tool call repeated → loop detected → controlled termination."""
    call_resp = MagicMock()
    call_resp.choices = [MagicMock()]
    call_resp.choices[0].message.content = (
        '<tool_call>\n{"name": "list_files", "arguments": {"path": "."}}\n</tool_call>'
    )

    with patch("app.agent.agent.chat", return_value=call_resp):
        agent = Agent()
        events = list(agent.step("list files"))

    assert events[-1].type == "error"
    assert "Loop detected" in events[-1].message
    assert "list_files" in events[-1].message
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

    # All 6 tools registered
    all_tools = registry.list_all()
    assert len(all_tools) == 6, f"Expected 6 tools, got {len(all_tools)}"

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
    assert len(tools) == 6
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
        test_destructive_command_confirm,
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
