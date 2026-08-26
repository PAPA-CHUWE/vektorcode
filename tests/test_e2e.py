"""End-to-end tests for the VektorCode agent."""

from __future__ import annotations

import json
import sys
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure app package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import WORKSPACE
from app.agent import Agent, AgentEvent
from app.tools.filesystem import list_files, read_file, write_file
from app.tools.terminal import run_command, _is_destructive
from app.tools.search import search_code
from app.ui.diff import format_diff


TEST_DIR = WORKSPACE / "_test_e2e"


def _setup():
    TEST_DIR.mkdir(exist_ok=True)


def _cleanup():
    import shutil
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)


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

    with patch("app.agent.chat", side_effect=[tool_call_resp, final_resp]):
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

    with patch("app.agent.chat", return_value=mock_response):
        agent = Agent()
        events = list(agent.step("show config"))
        types = [e.type for e in events]
        assert types[-1] == "error", f"Expected error event: {types}"
        assert "maximum iterations" in events[-1].message
        print("  PASS: max iterations")


def test_agent_final_answer():
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "The workspace is empty."

    with patch("app.agent.chat", return_value=mock_response):
        agent = Agent()
        events = list(agent.step("what's in the workspace?"))
        assert len(events) == 1
        assert events[0].type == "agent_message"
        assert events[0].message == "The workspace is empty."
        print("  PASS: agent final answer")


def test_agent_clear_history():
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "OK."

    with patch("app.agent.chat", return_value=mock_response):
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
    assert env_path.suffix == ".env", f"Expected .env file: {env_path}"
    print(f"  PASS: env path = {env_path}")


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
        test_agent_clear_history,
        test_agent_system_prompt_includes_workspace,
        test_diff_format,
        test_event_dataclass,
        test_config_version,
        test_config_env_search,
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
