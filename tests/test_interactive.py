"""Tests for the interactive layer: permissions, queueing, interrupts.

Run directly: python3 tests/test_interactive.py
"""
from __future__ import annotations

import asyncio
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.config.config as C

C.WORKSPACE = Path(__file__).resolve().parent.parent

from unittest.mock import MagicMock, patch

from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

import app.main as m
from app.agent import AgentEvent
from app.permissions.mode import (
    DESTRUCTIVE,
    SHELL,
    WRITE,
    PermissionMode,
    cycle_mode,
    get_mode,
    request_approval,
    set_approver,
    set_mode,
)
from app.ui.queue import MessageQueue

_passed, _failed = 0, 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS: {label}")
    else:
        _failed += 1
        print(f"  FAIL: {label}")


# ── Permission modes ────────────────────────────────────────────────

def test_mode_cycling():
    set_mode(PermissionMode.DEFAULT)
    check(cycle_mode() is PermissionMode.AUTO_EDIT, "default → auto-edit")
    check(cycle_mode() is PermissionMode.FULL_AUTO, "auto-edit → full-auto")
    check(cycle_mode() is PermissionMode.DEFAULT, "full-auto wraps to default")


def test_mode_governs_approval():
    """Each mode auto-allows exactly the kinds it should."""
    expected = {
        PermissionMode.DEFAULT: {WRITE: False, SHELL: False, DESTRUCTIVE: False},
        PermissionMode.AUTO_EDIT: {WRITE: True, SHELL: False, DESTRUCTIVE: False},
        PermissionMode.FULL_AUTO: {WRITE: True, SHELL: True, DESTRUCTIVE: True},
    }
    set_approver(None)  # nobody to ask
    try:
        for mode, kinds in expected.items():
            set_mode(mode)
            for kind, auto in kinds.items():
                got = request_approval(kind, "x")
                check(got is auto, f"{mode.label}: {kind} auto-allowed={auto}")
    finally:
        set_approver(None)
        set_mode(PermissionMode.DEFAULT)


def test_agent_cannot_self_approve():
    """The decisive safety property: no model-settable bypass exists."""
    import inspect

    from app.tools import get_registry
    from app.tools.terminal import run_command

    check("confirm" not in inspect.signature(run_command).parameters,
          "run_command takes no confirm parameter")
    schema = [s for s in get_registry().to_llm_schemas()
              if s["function"]["name"] == "run_command"][0]
    check("confirm" not in schema["function"]["parameters"]["properties"],
          "confirm is not exposed in the LLM tool schema")

    set_mode(PermissionMode.DEFAULT)
    set_approver(None)
    try:
        result = str(get_registry().execute(
            "run_command", {"command": "rm -rf /tmp/_vk_probe", "confirm": True}))
        check("BLOCKED" in result or "unexpected keyword" in result,
              "a model passing confirm=True cannot run a destructive command")
    finally:
        set_approver(None)
        set_mode(PermissionMode.DEFAULT)


def test_human_decision_is_respected():
    from app.tools.terminal import run_command

    set_mode(PermissionMode.DEFAULT)
    try:
        set_approver(lambda kind, detail: False)
        check("BLOCKED" in str(run_command("echo hi")), "declined command is blocked")
        set_approver(lambda kind, detail: True)
        check("BLOCKED" not in str(run_command("echo hi")), "approved command runs")
    finally:
        set_approver(None)
        set_mode(PermissionMode.DEFAULT)


def test_write_is_gated_but_free_in_auto_edit():
    from app.tools.filesystem import write_file

    target = "_vk_perm_probe/x.txt"
    path = C.WORKSPACE / target
    try:
        set_mode(PermissionMode.DEFAULT)
        set_approver(None)
        check("BLOCKED" in str(write_file(target, "data")), "write blocked in default mode")
        check(not path.exists(), "blocked write did not touch the filesystem")

        set_mode(PermissionMode.AUTO_EDIT)
        check("BLOCKED" not in str(write_file(target, "data")), "write allowed in auto-edit")
        check(path.exists(), "auto-edit write reached the filesystem")
    finally:
        set_approver(None)
        set_mode(PermissionMode.DEFAULT)
        if path.exists():
            path.unlink()
        if path.parent.exists():
            path.parent.rmdir()


# ── Queue ───────────────────────────────────────────────────────────

def test_queue_is_fifo():
    q = MessageQueue()
    check(not q and len(q) == 0, "new queue is empty")
    q.push("one")
    q.push("two")
    q.push("   ")  # whitespace ignored
    check(len(q) == 2, "blank messages are not queued")
    check(q.pop() == "one" and q.pop() == "two", "messages come back in order")
    check(q.pop() is None, "empty queue pops None")

    q.push("a")
    q.push("b")
    check(q.pop_last() == "b", "pop_last recalls the newest message")
    check(q.clear() == 1, "clear reports how many were dropped")


# ── Concurrent REPL ─────────────────────────────────────────────────

def _drive(script, step_impl, settle=2.5):
    """Run the async REPL against scripted keystrokes and a stubbed agent."""
    async def feed(pin):
        for keys, delay in script:
            pin.send_text(keys)
            await asyncio.sleep(delay)

    async def main():
        with create_pipe_input() as pin:
            with create_app_session(input=pin, output=DummyOutput()):
                with patch("app.agent.Agent.step", step_impl), \
                     patch("app.main.show_banner", MagicMock()):
                    asyncio.create_task(feed(pin))
                    await asyncio.wait_for(m.run_interactive_async(), timeout=30)

    asyncio.run(main())


def test_messages_typed_during_a_turn_queue_and_run_in_order():
    started = []

    def slow(self, message):
        started.append(message)
        yield AgentEvent(type="tool_start", tool="list_files", target=".", data={})
        time.sleep(0.4)
        yield AgentEvent(type="tool_result", tool="list_files", target=".", message="a", data={})
        yield AgentEvent(type="agent_message", message="done")

    _drive([("first\r", 0.15), ("second\r", 0.05), ("third\r", 1.8), ("exit\r", 0.2)], slow)
    check(started == ["first", "second", "third"],
          f"queued messages ran in FIFO order (got {started})")


def test_ctrl_c_interrupts_and_promotes_queue():
    started, finished = [], []

    def long(self, message):
        started.append(message)
        for i in range(8):
            yield AgentEvent(type="tool_start", tool="read_file", target=f"f{i}", data={})
            time.sleep(0.15)
            yield AgentEvent(type="tool_result", tool="read_file", target=f"f{i}", message="x", data={})
        finished.append(message)
        yield AgentEvent(type="agent_message", message="done")

    _drive([("long\r", 0.2), ("queued\r", 0.3), ("\x03", 2.2), ("exit\r", 0.2)], long)
    check(started == ["long", "queued"], f"interrupt promoted the queued message (got {started})")
    check("long" not in finished, "the interrupted turn did not run to completion")
    check("queued" in finished, "the promoted message ran to completion")


def test_interrupt_when_idle_returns_to_prompt():
    calls = []

    def quick(self, message):
        calls.append(message)
        yield AgentEvent(type="agent_message", message="ok")

    # Ctrl+C with nothing running must not exit the REPL.
    _drive([("hello\r", 0.4), ("\x03", 0.3), ("again\r", 0.4), ("exit\r", 0.2)], quick)
    check(calls == ["hello", "again"], f"REPL survived an idle Ctrl+C (got {calls})")


def test_no_threads_leak_after_interrupt():
    def long(self, message):
        for i in range(10):
            yield AgentEvent(type="tool_start", tool="read_file", target=f"f{i}", data={})
            time.sleep(0.1)
            yield AgentEvent(type="tool_result", tool="read_file", target=f"f{i}", message="x", data={})
        yield AgentEvent(type="agent_message", message="done")

    before = threading.active_count()
    _drive([("go\r", 0.3), ("\x03", 0.6), ("exit\r", 0.3)], long)
    time.sleep(0.6)  # let any worker wind down
    check(threading.active_count() <= before,
          f"no worker threads left running ({before} → {threading.active_count()})")


def test_blocked_action_reports_as_a_failure():
    """A refused command must not render as a green success."""
    from app.tools.tool_result import ToolStatus
    from app.tools import get_registry

    set_mode(PermissionMode.DEFAULT)
    set_approver(None)
    try:
        result = get_registry().execute("run_command", {"command": "rm -rf /tmp/_vk_x"})
        check(result.status == ToolStatus.BLOCKED, "refused command carries BLOCKED status")
        check(not str(result).startswith("Error: Error"), "blocked text is not double-prefixed")
        check("BLOCKED" in str(result), "blocked text still identifies itself")

        written = get_registry().execute(
            "write_file", {"path": "_vk_x/y.txt", "content": "z"})
        check(written.status == ToolStatus.BLOCKED, "refused write carries BLOCKED status")
    finally:
        set_approver(None)
        set_mode(PermissionMode.DEFAULT)


def test_agent_mutating_command_waits_for_the_turn():
    """/clear must not wipe history while a turn is reading it."""
    from app.agent import Agent as _Agent

    observed: list[int] = []
    cleared_at: dict[str, int] = {}

    def step(self, message):
        self.history.extend({"role": "user", "content": f"h{i}"} for i in range(5))
        for i in range(6):
            yield AgentEvent(type="tool_start", tool="read_file", target=f"f{i}", data={})
            time.sleep(0.12)
            observed.append(len(self.history))
            yield AgentEvent(type="tool_result", tool="read_file", target=f"f{i}", message="x", data={})
        yield AgentEvent(type="agent_message", message="done")

    real_clear = _Agent.clear_history

    def spy_clear(self):
        cleared_at["at"] = len(observed)
        real_clear(self)

    with patch.object(_Agent, "clear_history", spy_clear):
        _drive([("go\r", 0.3), ("/clear\r", 1.9), ("exit\r", 0.2)], step)

    check(observed and all(n == observed[0] for n in observed),
          f"history was stable for the whole turn (saw {observed})")
    check(cleared_at.get("at") == len(observed),
          "/clear executed only after the turn finished")


def test_readonly_command_runs_immediately_mid_turn():
    """/mode is safe mid-turn and must stay responsive."""
    ran: list[str] = []

    def step(self, message):
        for i in range(6):
            yield AgentEvent(type="tool_start", tool="read_file", target=f"f{i}", data={})
            time.sleep(0.12)
            yield AgentEvent(type="tool_result", tool="read_file", target=f"f{i}", message="x", data={})
        yield AgentEvent(type="agent_message", message="done")

    real_dispatch = m.dispatch_command

    def spy(name, args, agent, queue=None, vi=None):
        ran.append(name)
        return real_dispatch(name, args, agent, queue, vi)

    with patch.object(m, "dispatch_command", spy):
        _drive([("go\r", 0.3), ("/mode\r", 0.3), ("exit\r", 1.6)], step)

    check("/mode" in ran, "read-only command still dispatched while busy")
    set_mode(PermissionMode.DEFAULT)


def test_confirmation_reaches_the_worker_thread():
    """Approval asked from a turn thread must return the human's answer."""
    from app.permissions.mode import request_approval

    answers, results = [], []

    def step(self, message):
        yield AgentEvent(type="tool_start", tool="write_file", target="a.py", data={"path": "a.py"})
        results.append(request_approval(WRITE, "a.py (10 bytes)"))
        yield AgentEvent(type="tool_result", tool="write_file", target="a.py", message="x", data={})
        yield AgentEvent(type="agent_message", message="done")

    def fake_confirm(detail, kind="destructive"):
        answers.append((kind, detail))
        return True

    set_mode(PermissionMode.DEFAULT)
    with patch.object(m, "show_confirmation", fake_confirm):
        _drive([("write it\r", 1.2), ("exit\r", 0.2)], step)

    check(answers == [("write", "a.py (10 bytes)")], f"confirmation was asked (got {answers})")
    check(results == [True], "the human's answer reached the tool")


def test_cursor_signals_ready_busy_ready_reset():
    """The whole point: idle and working must be distinguishable."""
    import app.ui.cursor as cursor
    from app.ui.cursor import SHAPES

    names = {
        SHAPES["blinking-block"]: "ready",
        SHAPES["block"]: "busy",
        "\x1b[0 q": "reset",
    }
    seen: list[str] = []

    def step(self, message):
        yield AgentEvent(type="tool_start", tool="list_files", target=".", data={})
        time.sleep(0.3)
        yield AgentEvent(type="tool_result", tool="list_files", target=".", message="a", data={})
        yield AgentEvent(type="agent_message", message="done")

    with patch.object(cursor, "_write", lambda code: seen.append(names.get(code, code))):
        _drive([("go\r", 1.5), ("exit\r", 0.2)], step)

    collapsed = [c for i, c in enumerate(seen) if i == 0 or c != seen[i - 1]]
    check("busy" in collapsed, f"cursor goes busy during a turn (saw {collapsed})")
    check("busy" in collapsed and "ready" in collapsed[collapsed.index("busy") + 1:],
          "cursor returns to ready once the turn finishes")
    check(collapsed and collapsed[-1] == "reset", "cursor is reset on exit")


def test_write_diff_uses_captured_args_not_tool_result_payload():
    """tool_result carries no arguments, so the diff must use the stash.

    Reading them from event.data made every write render "(no changes)".
    """
    import app.main as _main
    from app.agent import AgentEvent
    from app.ui.console import console as ui_console
    from app.ui.toolfeed import ToolFeed

    target = C.WORKSPACE / "workspace" / "_diff_probe.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('def greet():\n    return "hello"\n')
    new_text = 'def greet(name):\n    return f"hi {name}"\n'

    try:
        _main.reset_turn()
        feed = ToolFeed()
        _main.render_event(AgentEvent(
            type="tool_start", tool="write_file", target=str(target),
            data={"path": "workspace/_diff_probe.py", "content": new_text}), feed)
        target.write_text(new_text)
        with ui_console.capture() as cap:
            # Exactly what the agent emits: no arguments here.
            _main.render_event(AgentEvent(
                type="tool_result", tool="write_file", target="workspace/_diff_probe.py",
                message="Wrote 40 bytes",
                data={"error": False, "blocked": False}), feed)
        out = cap.get()
    finally:
        if target.exists():
            target.unlink()
        _main.reset_turn()

    check("no changes" not in out, "a real write does not render as '(no changes)'")
    check('return "hello"' in out and 'f"hi {name}"' in out,
          "both sides of the diff are rendered")


def test_confirmation_precedes_the_write():
    """The safety property: approval is asked before the file changes."""
    from app.tools import get_registry

    target = C.WORKSPACE / "workspace" / "_order_probe.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("ORIGINAL")
    seen = {}

    def approver(kind, detail):
        seen["when_asked"] = target.read_text()
        return True

    set_mode(PermissionMode.DEFAULT)
    set_approver(approver)
    try:
        get_registry().execute(
            "write_file",
            {"path": "workspace/_order_probe.txt", "content": "REPLACED"},
        )
        check(seen.get("when_asked") == "ORIGINAL",
              "the file is untouched at the moment approval is requested")
        check(target.read_text() == "REPLACED", "the approved write then lands")
    finally:
        set_approver(None)
        set_mode(PermissionMode.DEFAULT)
        if target.exists():
            target.unlink()


def test_confirmation_offers_selectable_choices():
    """The prompt is a selectable dialog, not a typed y/N."""
    import app.ui.panels as panels
    from app.ui.confirm import ALWAYS, APPROVE, DECLINE

    seen = {}

    def fake_ask(title, detail, caution, choices):
        seen["title"] = title
        seen["detail"] = detail
        seen["choices"] = [c.value for c in choices]
        return "approve"

    set_mode(PermissionMode.DEFAULT)
    with patch.object(panels, "ask_choice", fake_ask, create=True), \
         patch("app.ui.confirm.ask_choice", fake_ask):
        approved = panels.show_confirmation("app/skills/notes.md (961 bytes)", "write")

    check(approved is True, "approving returns True")
    check("notes.md" in seen["detail"], f"the target is named (got {seen['detail']!r})")
    check("Write" in seen["title"], f"the action is named (got {seen['title']!r})")
    check(seen["choices"][-1] == "decline",
          "declining is last, so it is the safe default on abort")
    check("always" in seen["choices"],
          "default mode offers 'stop asking' for writes")
    set_mode(PermissionMode.DEFAULT)


def test_confirmation_decline_and_always():
    import app.ui.panels as panels
    from app.permissions.mode import get_mode

    set_mode(PermissionMode.DEFAULT)
    with patch("app.ui.confirm.ask_choice", lambda *a, **k: "decline"):
        check(panels.show_confirmation("rm -rf build/", "destructive") is False,
              "declining returns False")

    with patch("app.ui.confirm.ask_choice", lambda *a, **k: "always"):
        approved = panels.show_confirmation("a.py (10 bytes)", "write")
    check(approved is True, "'always' approves this action")
    check(get_mode() is PermissionMode.AUTO_EDIT,
          "'always' switches to auto-edit so it stops asking")
    set_mode(PermissionMode.DEFAULT)


def test_confirmation_never_blocks_without_a_terminal():
    """A dialog that waits for a keypress nobody can send would hang the CLI."""
    import time

    from app.ui.confirm import APPROVE, DECLINE, ask_choice

    started = time.monotonic()
    result = ask_choice("File Write", "a.py", "overwrites", [APPROVE, DECLINE])
    elapsed = time.monotonic() - started
    check(elapsed < 2.0, f"returns immediately with no tty ({elapsed:.2f}s)")
    check(result == "decline", "falls back to the safe choice")


# ── Status surfaces ─────────────────────────────────────────────────

def test_status_bar_shows_mode():
    from app.ui.console import console as ui_console
    from app.ui.panels import show_context_bar

    with ui_console.capture() as cap:
        show_context_bar("VektorCode", "gpt-oss-120b", 6, mode="full-auto")
    check("full-auto" in cap.get(), "permission mode appears in the status bar")


def test_toolbar_reports_running_state():
    from app.ui.input import VektorInput
    from app.ui.runstate import run_state

    set_mode(PermissionMode.DEFAULT)
    vi = VektorInput()
    idle = "".join(t for _, t in vi._bottom_toolbar())
    check("default" in idle and "shift+tab" in idle, "idle toolbar shows mode and hint")

    run_state.begin(15)
    run_state.tool_started("read_file", "app/main.py")
    run_state.queued = 2
    busy = "".join(t for _, t in vi._bottom_toolbar())
    check("running" in busy and "read_file" in busy, "toolbar shows the active tool")
    check("step 1/15" in busy, "toolbar shows the step counter")
    check("2 queued" in busy, "toolbar shows queue depth")
    run_state.end()
    run_state.queued = 0


from tests._log_isolation import restore as _restore_logs, snapshot as _snapshot_logs

_LOGS_BEFORE = _snapshot_logs()


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        fn()
    _restore_logs(_LOGS_BEFORE)
    print(f"\n{'=' * 40}\nResults: {_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
