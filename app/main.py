"""VektorCode CLI entry point — consumes agent events, renders via UI layer."""

from __future__ import annotations

import argparse
import os
import shlex
import sys
from pathlib import Path

try:
    import readline
except ImportError:
    readline = None

from rich.markdown import Markdown as RichMarkdown
from rich.text import Text

from app.agent import Agent, AgentEvent
from app.commands import COMMANDS
from app.config import WORKSPACE, __version__, load_env
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
)

TOOL_COUNT = 6

HELP_TEXT = """\
Available commands

  /help        Show available commands
  /status      Show session status
  /model       Show or switch the active model
  /models      List available models from the endpoint
  /endpoint    Show or set the LLM base URL
  /auth        Show, set, or clear the API key
  /workspace   Show current workspace path
  /tools       Show available tools
  /clear       Clear conversation history
  /reload      Reload config from .env
  exit         Exit VektorCode"""


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


def dispatch_command(cmd_name: str, cmd_args: list[str], agent: Agent) -> None:
    """Handle slash commands, including UI-specific ones."""
    if cmd_name == "/clear":
        agent.clear_history()
        console.print("\n  ✓ Conversation cleared.\n", style="success")
        return
    if cmd_name == "/help":
        console.print(f"\n{HELP_TEXT}\n", style="blue")
        return
    if cmd_name == "/status":
        console.print()
        console.print(f"  Model       {os.getenv('LITELLM_MODEL', '(not set)')}", style="blue")
        console.print(f"  Workspace   {WORKSPACE}", style="blue")
        console.print(f"  History     {len(agent.history)} messages", style="blue")
        console.print(f"  Tools       {TOOL_COUNT}", style="blue")
        console.print()
        return
    if cmd_name == "/workspace":
        console.print(f"\n  {WORKSPACE}\n", style="file.path")
        return
    if cmd_name == "/tools":
        table = Text()
        table.append("\n  Available tools\n\n", style="bold blue")
        table.append("  list_files     List files and directories\n", style="dim")
        table.append("  read_file      Read file contents\n", style="dim")
        table.append("  write_file     Write or create files\n", style="dim")
        table.append("  search_code    Search code with regex (like grep)\n", style="dim")
        table.append("  run_command    Execute shell commands\n", style="dim")
        table.append("  get_config     Show current configuration\n", style="dim")
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
    """Run the interactive chat loop."""
    history_path = Path.home() / ".vektorcode_history"
    if readline is not None:
        try:
            readline.read_history_file(str(history_path))
        except FileNotFoundError:
            pass
        readline.set_history_length(1000)

    model = os.getenv("LITELLM_MODEL", "")
    show_banner(model=model, workspace=str(WORKSPACE))

    agent = Agent()

    while True:
        try:
            user_input = console.input("[bold blue]You › [/]").strip()
        except (EOFError, KeyboardInterrupt):
            if readline is not None:
                readline.write_history_file(str(history_path))
            console.print("\n[dim]Goodbye.[/dim]")
            sys.exit(0)

        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit"}:
            if readline is not None:
                readline.write_history_file(str(history_path))
            console.print("[dim]Goodbye.[/dim]")
            break

        if user_input.startswith("/"):
            try:
                parts = shlex.split(user_input)
            except ValueError:
                parts = user_input.split()
            cmd_name = parts[0].lower()
            cmd_args = parts[1:]
            dispatch_command(cmd_name, cmd_args, agent)
            continue

        console.print()
        with spinner("VektorCode is thinking..."):
            events = list(agent.step(user_input))

        for event in events:
            render_event(event)

        console.print()
        show_context_bar(
            workspace=WORKSPACE.name,
            model=os.getenv("LITELLM_MODEL", ""),
            tool_count=TOOL_COUNT,
        )
        console.print()


def run_direct(prompt: str) -> None:
    """Execute a single prompt and exit."""
    model = os.getenv("LITELLM_MODEL", "")
    console.print(f"[dim]Workspace: {WORKSPACE}[/dim]")
    console.print(f"[dim]Model: {model}[/dim]\n")

    agent = Agent()

    with spinner("VektorCode is working..."):
        events = list(agent.step(prompt))

    for event in events:
        render_event(event)

    console.print()


def main() -> None:
    args = parse_args()

    load_env()

    if args.reload:
        load_env()
        model = os.getenv("LITELLM_MODEL", "(not set)")
        endpoint = os.getenv("LITELLM_BASE_URL", "(not set)")
        status_line(f"Model: {model}")
        status_line(f"Endpoint: {endpoint}")
        console.print()

    if args.prompt:
        prompt = " ".join(args.prompt)
        run_direct(prompt)
    else:
        run_interactive()


if __name__ == "__main__":
    main()
