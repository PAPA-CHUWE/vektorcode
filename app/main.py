"""VektorCode CLI entry point — consumes agent events, renders via UI layer."""

from __future__ import annotations

import argparse
import asyncio
import os
import threading
import re
import shlex
import sys
from pathlib import Path
from typing import Any

from rich.text import Text

from app.agent import Agent, AgentEvent
from app.agent.agent import MAX_ITERATIONS
from app.commands import COMMAND_registry, COMMANDS
from app.config import get_workspace, __version__, load_env
from app.tools import get_registry
from app.ui import (
    console,
    show_banner,
    show_tool_panel,
    show_tool_line,
    show_error_panel,
    show_change_panel,
    show_command_panel,
    show_confirmation,
    show_final_summary,
    show_plan,
    show_plan_update,
    show_markdown,
    show_code_block,
    show_truncated,
    show_context_bar,
    spinner,
    status_line,
    VektorInput,
)
from app.ui.panels import (
    set_token_display,
    show_commands_table,
    show_error,
    show_status_table,
    show_tools_table,
    show_traceback,
    last_traceback,
    token_display_enabled,
)
from app.ui.cursor import reset_cursor, set_cursor_busy, set_cursor_ready
from app.ui.toolfeed import ToolFeed
from app.ui.queue import MessageQueue
from app.ui.input import INTERRUPT
from app.ui.runstate import run_state
from app.permissions.mode import (
    PermissionMode,
    all_modes,
    get_mode,
    mode_from_name,
    set_approver,
    set_mode,
)
from prompt_toolkit.application.run_in_terminal import run_in_terminal
from prompt_toolkit.patch_stdout import patch_stdout
from app.ui.activity import ActivityEngine


def _tool_count() -> int:
    return len(get_registry().list_available())


def _apply_mode_flag(name: str | None) -> None:
    """Apply --mode. Non-interactive runs get no implicit consent, so a
    scripted run that needs to write or shell out must say so explicitly."""
    if not name:
        return
    target = mode_from_name(name)
    if target is None:
        names = ", ".join(m.label for m in all_modes())
        console.print(f"  Unknown mode {name!r}. Choose one of: {names}", style="error")
        raise SystemExit(2)
    set_mode(target)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="vektorcode",
        description="VektorCode — AI Coding Agent",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"vektorcode {__version__}",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Reload configuration from .env before starting",
    )
    parser.add_argument(
        "--mode",
        choices=["default", "auto-edit", "full-auto"],
        help=(
            "Permission mode. Non-interactive runs cannot prompt for approval, "
            "so writes and shell commands need this set explicitly."
        ),
    )
    parser.add_argument(
        "prompt",
        nargs="*",
        help="Direct prompt mode — execute and exit",
    )
    return parser.parse_args()


# Extensions Rich can syntax-highlight, for read_file previews.
_LEXERS = {
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "tsx",
    ".jsx": "jsx", ".json": "json", ".toml": "toml", ".yaml": "yaml",
    ".yml": "yaml", ".sh": "bash", ".rs": "rust", ".go": "go", ".md": "markdown",
}

# Lines of a file preview rendered before the truncation notice.
_PREVIEW_LINES = 24

# Per-turn state the renderer accumulates for the closing summary. render_event
# is called once per event, so cross-event details (a file's contents before a
# write, which paths changed) have to be carried here.
_turn: dict[str, Any] = {
    "changed": [], "old": {}, "passed": None, "failed": None,
    "tools": 0, "gave_up": False, "pending_write": None,
}


def reset_turn() -> None:
    """Clear per-turn render state. Called before each agent turn."""
    _turn.update(changed=[], old={}, passed=None, failed=None, tools=0,
                 gave_up=False, pending_write=None)


def _record_test_counts(output: str) -> None:
    """Pull pass/fail counts out of a test-runner's output, if present."""
    passed = re.search(r"(\d+) passed", output)
    failed = re.search(r"(\d+) failed", output)
    if passed:
        _turn["passed"] = int(passed.group(1))
    if failed:
        _turn["failed"] = int(failed.group(1))


def render_event(event: AgentEvent, feed: ToolFeed | None = None) -> None:
    """Render a single agent event via the UI layer."""
    t = event.type

    if t == "agent_message":
        if event.message.strip():
            show_markdown(event.message)

    elif t == "tool_start":
        _turn["tools"] += 1
        label = {
            "list_files": "Inspecting",
            "read_file": "Reading",
            "write_file": "Editing",
            "search_code": "Searching",
            "run_command": "Running",
            "git_tool": "Git",
            "get_config": "Checking config",
        }.get(event.tool, event.tool)

        if event.tool == "run_command":
            # A shell command is worth showing verbatim before it runs.
            show_command_panel(event.target)

        if feed is not None:
            feed.start(event.tool, event.target)
        else:
            show_tool_line(label, event.target)

        if event.tool == "write_file":
            # Stash both sides of the diff now. tool_result carries only
            # {"error", "blocked"} — no arguments — so the path and the new
            # content have to be captured here or the panel has nothing to
            # compare and reports "(no changes)" for every write.
            path = event.data.get("path", "") or event.target
            existing = get_workspace() / path
            try:
                previous = existing.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                previous = ""
            _turn["pending_write"] = {
                "path": path,
                "old": previous,
                "new": event.data.get("content", ""),
            }

    elif t == "tool_result":
        blocked = event.data.get("blocked", False)
        is_error = event.data.get("error", False)

        if feed is not None:
            feed.finish(not (blocked or is_error), event.message)

        if blocked:
            show_error("Command Blocked", event.message.strip().splitlines()[0]
                       if event.message.strip() else "blocked",
                       event.message,
                       "This command was refused by the permission layer.")
        elif is_error:
            show_error(f"{event.tool} failed",
                       event.message.strip().splitlines()[0]
                       if event.message.strip() else "unknown error",
                       event.message)
        elif event.tool == "read_file":
            lines = event.message.splitlines()
            lang = _LEXERS.get(Path(event.target).suffix, "text")
            show_code_block("\n".join(lines[:_PREVIEW_LINES]), lang)
            if len(lines) > _PREVIEW_LINES:
                show_truncated(len(lines) - _PREVIEW_LINES, "lines")
        elif event.tool == "write_file":
            pending = _turn.get("pending_write") or {}
            path = pending.get("path") or event.target
            show_change_panel(path, pending.get("old", ""), pending.get("new", ""))
            _turn["pending_write"] = None
            if path and path not in _turn["changed"]:
                _turn["changed"].append(path)
        elif event.tool == "run_command":
            _record_test_counts(event.message)

    elif t == "skills_applied":
        console.print(f"  ◆ guidance: {event.message}", style="dim")

    elif t == "max_iterations_reached":
        # Not a completion: the model ran out of iterations. Rendered inline
        # so the "✓ Task completed" panel never claims this as a success.
        _turn["gave_up"] = True
        console.print()
        console.print("  ⚠ Stopped without finishing — hit the iteration limit",
                      style="warning")
        if event.message.strip():
            show_markdown(event.message)

    elif t == "error":
        show_error("Agent Error", event.message.strip().splitlines()[0]
                   if event.message.strip() else "unknown error",
                   event.message)

    elif t == "context_status":
        status = event.data.get("status", "OK")
        if status in ("WARNING", "CRITICAL", "OVERFLOW"):
            style = "warning" if status == "WARNING" else "error"
            console.print(f"  ⚠ {event.message}", style=style)

    elif t == "plan_created":
        steps = event.data.get("steps", [])
        show_plan(steps)

    elif t == "plan_update":
        steps = event.data.get("steps", [])
        completed = event.data.get("completed", set())
        show_plan_update(steps, completed)

    elif t == "file_read":
        show_tool_line("Reading", event.target, style="file.read")

    elif t in ("file_created", "file_modified"):
        show_tool_line("Created" if "created" in t else "Editing", event.target)

    elif t == "confirmation_required":
        cmd = event.data.get("command", "")
        approved = show_confirmation(cmd)
        event.data["approved"] = approved


def _render_turn_summary(final_message: str = "") -> None:
    """Close out a turn that did real work with a completion summary.

    Skipped for pure question-and-answer turns, where a summary panel
    would just repeat the answer that was already rendered.
    """
    if _turn.get("gave_up"):
        # Already rendered its own "stopped" message inline.
        return
    if not _turn["tools"]:
        return
    if not (_turn["changed"] or _turn["passed"] is not None or _turn["failed"] is not None):
        return

    summary = final_message.strip().splitlines()[0] if final_message.strip() else ""
    show_final_summary(
        files_changed=_turn["changed"],
        tests_passed=_turn["passed"],
        tests_failed=_turn["failed"],
        summary=summary,
        workspace=str(get_workspace()),
    )


def _resolve_activity(engine: ActivityEngine, event: AgentEvent) -> Activity | None:
    """Map an agent event to a dynamic activity label for the spinner."""
    from app.ui.activity import Activity

    if event.type == "tool_start":
        return engine.resolve_tool_start(event.tool, event.target)
    elif event.type == "agent_message":
        return engine.resolve_thinking()
    elif event.type == "final_answer":
        return engine.resolve_completed()
    elif event.type in ("error", "max_iterations_reached"):
        return engine.resolve_error()
    elif event.type == "context_status":
        # Context warning — show status but don't change activity
        return None
    elif event.type in {"tool_result", "change"}:
        # These are output events, keep current activity
        return None
    return None


def dispatch_command(
    cmd_name: str,
    cmd_args: list[str],
    agent: Agent,
    queue: "MessageQueue | None" = None,
    vektor_input: "VektorInput | None" = None,
) -> None:
    """Handle slash commands, including UI-specific ones."""
    if cmd_name == "/queue":
        if queue is None:
            console.print("\n  Queue is only available in interactive mode.\n", style="dim")
            return
        if cmd_args and cmd_args[0] == "clear":
            console.print(f"\n  Dropped {queue.clear()} queued message(s).\n", style="dim")
            run_state.queued = 0
            return
        if cmd_args and cmd_args[0] == "pop":
            recalled = queue.pop_last()
            run_state.queued = len(queue)
            if recalled is None:
                console.print("\n  Nothing queued to recall.\n", style="dim")
            else:
                console.print(f"\n  Recalled: {recalled}\n", style="dim")
            return
        queue.show_pending()
        return
    if cmd_name == "/mode":
        if cmd_args:
            target = mode_from_name(cmd_args[0])
            if target is None:
                names = ", ".join(m.label for m in all_modes())
                console.print(f"\n  Unknown mode. Choose one of: {names}\n", style="error")
                return
            set_mode(target)
        show_status_table([
            ("Mode", get_mode().label),
            ("Writes", "auto" if get_mode() is not PermissionMode.DEFAULT else "confirm"),
            ("Shell", "auto" if get_mode() is PermissionMode.FULL_AUTO else "confirm"),
            ("Cycle", "shift+tab"),
        ])
        return
    if cmd_name == "/clear":
        agent.clear_history()
        console.print("\n  ✓ Conversation cleared.\n", style="success")
        return
    if cmd_name == "/help":
        show_commands_table(
            [(c.name, c.description) for c in COMMAND_registry] + [("exit", "Exit VektorCode")]
        )
        return
    if cmd_name == "/status":
        ctx = agent.context_manager.get_status(agent.history)
        show_status_table([
            ("Model", os.getenv("LITELLM_MODEL", "(not set)")),
            ("Workspace", str(get_workspace())),
            ("History", f"{len(agent.history)} messages"),
            ("Tools", str(_tool_count())),
            ("Context", ctx["display"]),
            ("Status", ctx["status"]),
        ])
        return
    if cmd_name == "/workspace":
        console.print(f"\n  {get_workspace()}\n", style="file.path")
        return
    if cmd_name == "/tools":
        registry = get_registry()
        grouped = {
            category: [(t.name, t.description) for t in registry.list_by_category(category)]
            for category in registry.get_categories()
        }
        show_tools_table(grouped, len(registry.list_available()))
        return
    if cmd_name == "/skills":
        from app.skills.loader import load_skills, match_skills

        skills = load_skills()
        if cmd_args:
            probe = " ".join(cmd_args)
            matched = match_skills(probe, skills)
            show_status_table(
                [(s.name, ", ".join(s.matches(probe))) for s in matched]
                or [("(none)", f"nothing matches {probe!r}")]
            )
            return
        show_tools_table(
            {"skills": [(s.name, ", ".join(s.triggers) or "(no triggers)") for s in skills]},
            len(skills),
        )
        return
    if cmd_name == "/tokens":
        enabled = set_token_display(not token_display_enabled())
        state = "shown" if enabled else "hidden"
        console.print(f"\n  Token counts {state}.\n", style="dim")
        return
    if cmd_name == "/trace":
        show_traceback(last_traceback())
        return

    handler = COMMANDS.get(cmd_name)
    if handler:
        result = handler(cmd_args)
        if result and result != "__CLEAR__":
            console.print(f"\n  {result}\n", style="blue")
    else:
        console.print(f"\n  Unknown command: {cmd_name}. Type /help for available commands.\n", style="error")



# ── Concurrent turn execution ───────────────────────────────────────

_TURN_DONE = object()

# Commands that mutate Agent state and therefore cannot run concurrently
# with an in-flight step().
_AGENT_MUTATING_COMMANDS = {"/clear", "/reload"}


async def _stream_turn(agent: Agent, message: str, stop: threading.Event):
    """Yield the turn's AgentEvents while keeping the event loop free.

    agent.step() is a blocking generator (network I/O), so it runs in a
    worker thread and hands events back through an asyncio queue. That is
    what lets the prompt stay responsive while a turn executes.
    """
    loop = asyncio.get_running_loop()
    events: asyncio.Queue = asyncio.Queue()

    def produce() -> None:
        generator = agent.step(message)
        try:
            for event in generator:
                if stop.is_set():
                    # Closing the generator unwinds agent.step() cleanly at
                    # its current yield point.
                    generator.close()
                    break
                loop.call_soon_threadsafe(events.put_nowait, event)
        finally:
            loop.call_soon_threadsafe(events.put_nowait, _TURN_DONE)

    worker = asyncio.create_task(asyncio.to_thread(produce))
    try:
        while True:
            event = await events.get()
            if event is _TURN_DONE:
                break
            yield event
    finally:
        stop.set()
        await worker


async def _execute_turn(agent: Agent, message: str, stop: threading.Event) -> bool:
    """Run one turn, rendering as it goes. True if it completed normally."""
    feed = ToolFeed()
    reset_turn()
    run_state.begin(MAX_ITERATIONS)
    # The async REPL has no Rich spinner (it would fight prompt_toolkit for
    # the cursor), so the busy signal is set here at the real turn boundary
    # rather than inside spinner().
    set_cursor_busy()
    last_message = ""
    failed = False

    try:
        async for event in _stream_turn(agent, message, stop):
            if event.type == "tool_start":
                run_state.tool_started(event.tool, event.target)
            elif event.type == "agent_message":
                last_message = event.message
            elif event.type in ("error", "max_iterations_reached"):
                failed = True
            render_event(event, feed)
    finally:
        run_state.end()

    console.print()
    tally = feed.summary()
    if tally is not None:
        console.print(tally)
    _render_turn_summary(last_message)
    _footer(agent, status="error" if failed else "ready")
    return not failed


def _footer(agent: Agent, status: str = "ready") -> None:
    show_context_bar(
        workspace=get_workspace().name,
        model=os.getenv("LITELLM_MODEL", ""),
        tool_count=_tool_count(),
        input_tokens=agent.input_tokens,
        output_tokens=agent.output_tokens,
        total_tokens=agent.total_tokens,
        status=status,
        mode=get_mode().label,
        budget_remaining_usd=agent.budget.remaining_usd,
    )
    console.print()


async def run_interactive_async() -> None:
    """Concurrent REPL: input is read while a turn executes.

    Enter during a turn queues the message; Ctrl+C interrupts the turn and
    promotes the next queued message. When a turn ends, the next queued
    message starts automatically — no extra keypress.
    """
    vektor_input = VektorInput(workspace=get_workspace())
    show_banner(model=os.getenv("LITELLM_MODEL", ""), workspace=str(get_workspace()))

    agent = Agent()
    queue = MessageQueue()

    # Turns run in a worker thread while prompt_toolkit owns the terminal, so
    # a bare input() prompt gets overwritten by the running prompt and cannot
    # be answered. run_in_terminal suspends the prompt, asks, then resumes.
    loop = asyncio.get_running_loop()

    async def _ask(kind: str, detail: str) -> bool:
        return await run_in_terminal(lambda: show_confirmation(detail, kind))

    def approve(kind: str, detail: str) -> bool:
        if threading.current_thread() is threading.main_thread():
            # Already on the loop thread; scheduling would deadlock.
            return show_confirmation(detail, kind)
        return asyncio.run_coroutine_threadsafe(_ask(kind, detail), loop).result()

    # The only path by which a privileged action can be approved.
    set_approver(approve)

    turn: asyncio.Task | None = None
    stop = threading.Event()
    last_prompt: str = ""
    # Agent-mutating commands submitted mid-turn, run once the turn ends.
    deferred: list[tuple[str, list[str]]] = []

    def start_turn(message: str) -> asyncio.Task:
        nonlocal stop
        stop = threading.Event()
        return asyncio.create_task(_execute_turn(agent, message, stop))

    def promote() -> asyncio.Task | None:
        """Start the next queued message, if any."""
        nxt = queue.pop()
        run_state.queued = len(queue)
        if nxt is None:
            return None
        console.print(f"  ▸ {nxt}", style="dim")
        return start_turn(nxt)

    async def cancel_turn() -> None:
        nonlocal turn
        if turn is None:
            return
        stop.set()
        turn.cancel()
        try:
            await turn
        except (asyncio.CancelledError, Exception):
            pass
        turn = None
        run_state.end()

    # Exactly one reader is alive at a time; it is only recreated once its
    # result has been consumed. Creating one per loop iteration would leak
    # tasks and leave several readers racing for stdin.
    reader = asyncio.create_task(vektor_input.get_input_async())

    try:
        with patch_stdout(raw=True):
            while True:
                waits = {reader} | ({turn} if turn is not None else set())
                await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)

                # ── the running turn finished → promote the queue ────
                if turn is not None and turn.done():
                    turn = None
                    # The reader is still awaiting the same prompt_async call,
                    # so get_input_async() will not run again to restore the
                    # ready cursor. Without this the cursor stays "busy" while
                    # we are in fact idle and waiting.
                    set_cursor_ready()
                    while deferred:
                        name, cmd_args = deferred.pop(0)
                        dispatch_command(name, cmd_args, agent, queue, vektor_input)
                    turn = promote()

                if not reader.done():
                    continue

                result = reader.result()
                reader = asyncio.create_task(vektor_input.get_input_async())

                # ── Ctrl+C: stop the turn, never leave the REPL ─────
                if result is INTERRUPT:
                    if turn is not None:
                        await cancel_turn()
                        turn = promote()
                        console.print(
                            "  ✗ Interrupted — resuming with queued message"
                            if turn is not None else
                            "  ✗ Interrupted — back to idle",
                            style="warning",
                        )
                    continue

                # ── Ctrl+D / EOF: leave ─────────────────────────────
                if result is None:
                    console.print("\n[dim]Goodbye.[/dim]")
                    return

                if not result.text:
                    continue

                if result.text.lower() in {"exit", "quit"}:
                    pending = len(queue)
                    if pending:
                        console.print(f"  Discarding {pending} queued message(s).", style="dim")
                    await cancel_turn()
                    console.print("[dim]Goodbye.[/dim]")
                    return

                if result.paste_info.is_paste:
                    console.print(f"  {result.display_text}", style="dim", markup=False)

                # /retry re-runs the previous prompt. It was previously not a
                # command at all, so typing it sent the literal word "retry"
                # to the model, which had no idea what to repeat.
                if result.is_command and result.text.split()[0].lower() == "/retry":
                    if not last_prompt:
                        console.print("\n  Nothing to retry yet.\n", style="dim")
                        continue
                    console.print(f"  ▸ retrying: {last_prompt}", style="dim")
                    if turn is not None:
                        queue.submit(last_prompt)
                        run_state.queued = len(queue)
                        queue.show_queued(last_prompt, len(queue) - 1)
                    else:
                        console.print()
                        turn = start_turn(last_prompt)
                    continue

                # ── slash commands ──────────────────────────────────
                if result.is_command:
                    try:
                        parts = shlex.split(result.text)
                    except ValueError:
                        parts = result.text.split()
                    name = parts[0].lower()

                    # Commands that mutate agent state must not run while a
                    # turn is reading/writing self.history in a worker thread.
                    # Read-only commands (/queue, /mode, /tokens…) are safe and
                    # stay responsive mid-turn.
                    if name in _AGENT_MUTATING_COMMANDS and turn is not None:
                        deferred.append((name, parts[1:]))
                        console.print(
                            f"  ⏳ {name} queued — runs when the current turn finishes",
                            style="dim",
                        )
                        continue

                    dispatch_command(name, parts[1:], agent, queue, vektor_input)
                    continue

                # ── busy → queue; idle → start a turn ───────────────
                if turn is not None:
                    position = queue.submit(result.text)
                    run_state.queued = len(queue)
                    queue.show_queued(result.text, position)
                    continue

                console.print()
                last_prompt = result.text
                turn = start_turn(result.text)
    finally:
        # Every exit from the REPL passes through here — exit/quit, Ctrl+D,
        # and exceptions alike — so the terminal never keeps a cursor shape
        # VektorCode set once the process is gone.
        reset_cursor()
        reader.cancel()
        if turn is not None:
            stop.set()
            turn.cancel()


def run_interactive() -> None:
    """Entry point for the interactive REPL."""
    try:
        asyncio.run(run_interactive_async())
    except KeyboardInterrupt:
        reset_cursor()
        console.print("\n[dim]Goodbye.[/dim]")


def run_direct(prompt: str) -> None:
    """Execute a single prompt and exit."""
    model = os.getenv("LITELLM_MODEL", "")
    console.print(f"[dim]Workspace: {get_workspace()}[/dim]")
    console.print(f"[dim]Model: {model}[/dim]\n")

    agent = Agent()
    activity_engine = ActivityEngine()
    reset_turn()
    last_message = ""

    feed = ToolFeed()
    with spinner("VektorCode is initializing") as s:
        feed.attach(s)
        for event in agent.step(prompt):
            if event.type != "tool_start":
                activity = _resolve_activity(activity_engine, event)
                if activity:
                    s.update(f"[bold blue]●[/] {activity.label}")
            render_event(event, feed)
            if event.type == "agent_message":
                last_message = event.message
            if event.type == "final_answer":
                s.update(f"[bold green]✓[/] resolved")
    # One-shot mode uses the same spinner, which sets the busy cursor.
    # Without this the shell inherits a block cursor after the process exits.
    reset_cursor()
    console.print()
    tally = feed.summary()
    if tally is not None:
        console.print(tally)
    _render_turn_summary(last_message)


def main() -> None:
    args = parse_args()

    if args.reload:
        load_env(force=True)
        model = os.getenv("LITELLM_MODEL", "(not set)")
        endpoint = os.getenv("LITELLM_BASE_URL", "(not set)")
        status_line(f"Model: {model}")
        status_line(f"Endpoint: {endpoint}")
        console.print()
    else:
        load_env()

    _apply_mode_flag(args.mode)

    if args.prompt:
        prompt = " ".join(args.prompt)
        run_direct(prompt)
    else:
        run_interactive()


if __name__ == "__main__":
    main()
