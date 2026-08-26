"""VektorCode CLI entry point — consumes agent events, renders via UI layer."""

from __future__ import annotations

import argparse
import os
import shlex
import sys
from pathlib import Path

from rich.text import Text

from app.agent import Agent, AgentEvent
from app.commands import COMMANDS, format_help_text
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
from app.ui.activity import ActivityEngine


def _tool_count() -> int:
    return len(get_registry().list_available())


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
        "prompt",
        nargs="*",
        help="Direct prompt mode — execute and exit",
    )
    return parser.parse_args()


def render_event(event: AgentEvent) -> None:
    """Render a single agent event via the UI layer."""
    t = event.type

    if t == "agent_message":
        if event.message.strip():
            show_markdown(event.message)

    elif t == "tool_start":
        label = {
            "list_files": "Inspecting",
            "read_file": "Reading",
            "write_file": "Editing",
            "search_code": "Searching",
            "run_command": "Running",
            "get_config": "Checking config",
        }.get(event.tool, event.tool)
        show_tool_line(label, event.target)

    elif t == "tool_result":
        blocked = event.data.get("blocked", False)
        is_error = event.data.get("error", False)
        if blocked:
            show_error_panel("Command Blocked", event.message)
        elif is_error and event.tool == "run_command":
            show_error_panel("Command Failed", event.message)
        elif is_error:
            console.print(f"    {event.message}", style="error")

    elif t == "error":
        show_error_panel("Error", event.message)

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


def _resolve_activity(engine: ActivityEngine, event: AgentEvent) -> Activity | None:
    """Map an agent event to a dynamic activity label for the spinner."""
    from app.ui.activity import Activity

    if event.type == "tool_start":
        return engine.resolve_tool_start(event.tool, event.target)
    elif event.type == "agent_message":
        return engine.resolve_thinking()
    elif event.type == "final_answer":
        return engine.resolve_completed()
    elif event.type == "error":
        return engine.resolve_error()
    elif event.type == "context_status":
        # Context warning — show status but don't change activity
        return None
    elif event.type in {"tool_result", "change"}:
        # These are output events, keep current activity
        return None
    return None


def dispatch_command(cmd_name: str, cmd_args: list[str], agent: Agent) -> None:
    """Handle slash commands, including UI-specific ones."""
    if cmd_name == "/clear":
        agent.clear_history()
        console.print("\n  ✓ Conversation cleared.\n", style="success")
        return
    if cmd_name == "/help":
        console.print(f"\n{format_help_text()}\n", style="blue")
        return
    if cmd_name == "/status":
        console.print()
        console.print(f"  Model       {os.getenv('LITELLM_MODEL', '(not set)')}", style="blue")
        console.print(f"  Workspace   {get_workspace()}", style="blue")
        console.print(f"  History     {len(agent.history)} messages", style="blue")
        console.print(f"  Tools       {_tool_count()}", style="blue")
        # Show context status
        ctx = agent.context_manager.get_status(agent.history)
        console.print(f"  Context     {ctx['display']}", style="blue")
        console.print(f"  Status      {ctx['status']}", style="blue")
        console.print()
        return
    if cmd_name == "/workspace":
        console.print(f"\n  {get_workspace()}\n", style="file.path")
        return
    if cmd_name == "/tools":
        registry = get_registry()
        tools = registry.list_available()
        categories = registry.get_categories()
        table = Text()
        table.append(f"\n  Available tools · {len(tools)}\n\n", style="bold blue")
        for cat in categories:
            cat_tools = registry.list_by_category(cat)
            table.append(f"  {cat.upper()}\n", style="bold")
            for tool in cat_tools:
                table.append(f"    {tool.name:<16}{tool.description}\n", style="dim")
            table.append("\n")
        console.print(table)
        return

    handler = COMMANDS.get(cmd_name)
    if handler:
        result = handler(cmd_args)
        if result and result != "__CLEAR__":
            console.print(f"\n  {result}\n", style="blue")
    else:
        console.print(f"\n  Unknown command: {cmd_name}. Type /help for available commands.\n", style="error")


def run_interactive() -> None:
    """Run the interactive chat loop with paste-aware input and slash-command completion."""
    vektor_input = VektorInput()

    model = os.getenv("LITELLM_MODEL", "")
    show_banner(model=model, workspace=str(get_workspace()))

    agent = Agent()

    while True:
        result = vektor_input.get_input()

        if result is None:
            # EOF or Ctrl+C
            console.print("\n[dim]Goodbye.[/dim]")
            sys.exit(0)

        if not result.text:
            continue

        if result.text.lower() in {"exit", "quit"}:
            console.print("[dim]Goodbye.[/dim]")
            break

        # Display paste indicator for large pastes
        if result.paste_info.is_paste:
            console.print(f"  {result.display_text}", style="dim")

        # Slash commands
        if result.is_command:
            try:
                parts = shlex.split(result.text)
            except ValueError:
                parts = result.text.split()
            cmd_name = parts[0].lower()
            cmd_args = parts[1:]
            dispatch_command(cmd_name, cmd_args, agent)
            continue

        # Agent processing with dynamic activity labels
        console.print()
        activity_engine = ActivityEngine()
        with spinner("VektorCode is initializing") as s:
            for event in agent.step(result.text):
                activity = _resolve_activity(activity_engine, event)
                if activity:
                    s.update(f"[bold blue]●[/] {activity.label}")
                render_event(event)
                # Show completion state briefly
                if event.type == "final_answer":
                    s.update(f"[bold green]✓[/] resolved")
        console.print()
        show_context_bar(
            workspace=get_workspace().name,
            model=os.getenv("LITELLM_MODEL", ""),
            tool_count=_tool_count(),
        )
        console.print()


def run_direct(prompt: str) -> None:
    """Execute a single prompt and exit."""
    model = os.getenv("LITELLM_MODEL", "")
    console.print(f"[dim]Workspace: {get_workspace()}[/dim]")
    console.print(f"[dim]Model: {model}[/dim]\n")

    agent = Agent()
    activity_engine = ActivityEngine()

    with spinner("VektorCode is initializing") as s:
        for event in agent.step(prompt):
            activity = _resolve_activity(activity_engine, event)
            if activity:
                s.update(f"[bold blue]●[/] {activity.label}")
            render_event(event)
            if event.type == "final_answer":
                s.update(f"[bold green]✓[/] resolved")
    console.print()


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

    if args.prompt:
        prompt = " ".join(args.prompt)
        run_direct(prompt)
    else:
        run_interactive()


if __name__ == "__main__":
    main()
