"""Focused regression tests for the VektorCode agent orchestration loop.

Covers the failure where the model re-requested the same file ranges
until MAX_ITERATIONS / loop detection fired.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unittest.mock import MagicMock, patch

import app.agent.agent as A
from app.agent.agent import Agent
from app.context.context import (
    ContextManager,
    _truncate_tool_outputs,
    check_budget,
    get_model_config,
)

_passed, _failed = 0, 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS: {label}")
    else:
        _failed += 1
        print(f"  FAIL: {label}")


def _resp(content):
    r = MagicMock()
    r.choices = [MagicMock()]
    r.choices[0].message.content = content
    r.usage = None
    return r


def _tc(name, **args):
    return f'<tool_call>\n{{"name": "{name}", "arguments": {json.dumps(args)}}}\n</tool_call>'


def _run(script, tool_output=lambda n, a: f"<<{n} {json.dumps(a)}>>"):
    """Run agent.step over a scripted LLM, capturing every chat() input."""
    seen = []

    def fake_chat(messages, tools=None, model=None):
        seen.append([dict(m) for m in messages])
        return _resp(script[min(len(seen) - 1, len(script) - 1)])

    with patch.object(A, "chat", fake_chat), \
         patch.object(A.Agent, "_execute_tool", lambda self, n, a: tool_output(n, a)):
        agent = Agent()
        events = list(agent.step("investigate the project"))
    return agent, events, seen


# ── 1. user → final response ────────────────────────────────────────
def test_final_response_only():
    agent, events, seen = _run(["The project is a CLI agent."])
    check(len(seen) == 1, "single LLM call for a no-tool turn")
    check([e.type for e in events] == ["agent_message"], "user -> final response")


# ── 2. user → tool → result → final ─────────────────────────────────
def test_single_tool_then_final():
    agent, events, seen = _run([_tc("list_files", path="."), "Done."])
    check([e.type for e in events] == ["tool_start", "tool_result", "agent_message"],
          "user -> tool -> result -> final response")
    check(any(m["role"] == "tool" for m in seen[1]), "tool result visible to next LLM call")


# ── 3. multi-step, different tools ──────────────────────────────────
def test_multi_tool_sequence():
    agent, events, seen = _run([
        _tc("list_files", path="."),
        _tc("read_file", path="app/main.py"),
        _tc("search_code", pattern="def main", path="."),
        "Summary.",
    ])
    tools = [e.tool for e in events if e.type == "tool_start"]
    check(tools == ["list_files", "read_file", "search_code"], "multi-step tool chain preserved")
    check(events[-1].type == "agent_message", "multi-step chain reaches final response")


# ── 4. repeated identical call terminates ───────────────────────────
def test_repeated_identical_call():
    agent, events, seen = _run([_tc("read_file", path="a.py")] * 30)
    check(len(seen) <= A.MAX_ITERATIONS, "repeated identical call does not loop forever")
    check(events[-1].type in ("error", "agent_message"), "repeated call terminates the turn")


# ── 5. THE ROOT CAUSE: newest tool result must not be truncated ─────
def test_newest_tool_result_not_truncated():
    big = "L" * 18000
    msgs = [{"role": "system", "content": "S" * 3000},
            {"role": "user", "content": "read main.py"}]
    for i in range(12):
        msgs.append({"role": "assistant", "content": f"[called read_file({i})]"})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": big})

    cm = ContextManager("nemotron-3-super")
    check(check_budget(msgs, "nemotron-3-super").is_overflow, "history is in the overflow regime")

    safe = cm.prepare_messages(msgs)
    tools = [m for m in safe if m["role"] == "tool"]
    check(len(tools[-1]["content"]) >= 18000,
          "newest tool result survives compaction intact")
    check("truncated" in tools[0]["content"],
          "older tool results are still compacted")


def test_preserve_recent_flag_is_opt_in():
    msgs = [{"role": "tool", "tool_call_id": "c1", "content": "x" * 5000}]
    check("truncated" in _truncate_tool_outputs(msgs, max_output_chars=200)[0]["content"],
          "preserve_recent defaults to 0 (existing callers unaffected)")
    check(_truncate_tool_outputs(msgs, max_output_chars=200, preserve_recent=1)[0]["content"]
          == msgs[0]["content"],
          "preserve_recent=1 keeps the only tool result intact")


# ── 6. assistant tool-call state is preserved ───────────────────────
def test_tool_request_recorded_in_history():
    agent, events, seen = _run([_tc("read_file", path="app/main.py"), "Done."])
    tools = [m for m in agent.history if m["role"] == "tool"]
    check(any("read_file" in m["content"] and "app/main.py" in m["content"] for m in tools),
          "tool result records which tool+args it answers")
    assistants = [m for m in agent.history if m["role"] == "assistant"]
    check(all("[called " not in m["content"] for m in assistants),
          "assistant turn carries no imitable call-record syntax")
    check(all("<tool_call>" not in m["content"] for m in assistants),
          "protocol markup still stripped (no re-parse on next turn)")


def test_result_header_is_not_reparsed_as_a_call():
    header = 'Result of read_file({"path": "app/main.py"}):\nfile body here'
    agent = Agent.__new__(Agent)
    check(agent._extract_tool_calls(header) == [],
          "tool-result header is not parsed as a fresh tool call")


# ── 7/8. context trimming + over-budget behaviour ───────────────────
def test_context_trimming_keeps_task_and_system():
    msgs = [{"role": "system", "content": "S" * 3000},
            {"role": "user", "content": "refactor the auth module"}]
    for i in range(12):
        msgs.append({"role": "assistant", "content": f"[called read_file({i})]"})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": "x" * 18000})

    safe = ContextManager("nemotron-3-super").prepare_messages(msgs)
    check(safe[0]["role"] == "system", "system prompt survives compaction")
    check(any("refactor the auth module" in (m.get("content") or "") for m in safe),
          "original user task survives compaction")
    check(check_budget(safe, "nemotron-3-super").estimated_tokens
          <= get_model_config("nemotron-3-super").input_budget,
          "compacted messages fit the input budget")


# ── 12. model switching resolves a sane window ──────────────────────
def test_model_resolution():
    check(get_model_config("gpt-oss-120b").context_window == 131072, "exact model match")
    check(get_model_config("nemotron-3-super").context_window == 32768,
          "suffixed deployment name resolves via prefix")
    check(get_model_config("totally-unknown-model").context_window == 32768,
          "unknown model falls back safely")


# ── Reasoning-model channel handling ────────────────────────────────
def _resp_channels(content, reasoning=None):
    r = MagicMock()
    r.choices = [MagicMock()]
    r.choices[0].message.content = content
    r.choices[0].message.reasoning_content = reasoning
    r.usage = None
    return r


def test_reasoning_content_fallback():
    """Empty content + tool call in reasoning_content must still dispatch."""
    seq = [_resp_channels("", _tc("list_files", path=".")), _resp_channels("Done.", None)]
    with patch.object(A, "chat", side_effect=seq), \
         patch.object(A.Agent, "_execute_tool", lambda self, n, a: "ok"):
        agent = Agent()
        events = list(agent.step("list files"))
    check(any(e.type == "tool_start" for e in events),
          "tool call in reasoning_content is dispatched when content is empty")


def test_empty_turn_is_not_a_blank_final_answer():
    """A wholly empty turn must never surface as an empty agent_message."""
    seq = [_resp_channels("", None), _resp_channels("Real answer.", None)]
    with patch.object(A, "chat", side_effect=seq):
        agent = Agent()
        events = list(agent.step("hi"))
    finals = [e for e in events if e.type == "agent_message"]
    check(len(finals) == 1 and finals[0].message == "Real answer.",
          "empty turn is nudged, not reported as a blank answer")


def test_repeated_empty_turns_surface_an_error():
    seq = [_resp_channels("", None)] * 8
    with patch.object(A, "chat", side_effect=seq):
        agent = Agent()
        events = list(agent.step("hi"))
    check(events[-1].type == "error" and "failed to emit a tool call" in events[-1].message,
          "consecutive empty turns surface an error, not silence")


def test_chat_sends_tool_call_stop_sequence():
    import app.llm.llm as L
    captured = {}

    class FakeCompletions:
        def create(self, **kw):
            captured.update(kw)
            return MagicMock()

    fake = MagicMock()
    fake.chat.completions = FakeCompletions()
    with patch.object(L, "_get_client", lambda: fake):
        L.chat([{"role": "user", "content": "hi"}], model="m")
    check(captured.get("stop") == ["</tool_call>"],
          "chat() sends the </tool_call> stop sequence")
    check(captured.get("max_tokens") == L.MAX_OUTPUT_TOKENS, "chat() bounds max_tokens")


def test_unterminated_tool_call_parses():
    from app.agent.agent import _extract_json_from_tags
    text = '<tool_call>\n{\n  "name": "list_files",\n  "arguments": {\n    "path": "."\n  }\n}\n'
    calls = _extract_json_from_tags(text)
    check(calls == [{"name": "list_files", "arguments": {"path": "."}}],
          "tool call with stop-stripped closing tag still parses")


# ── UI regressions surfaced by wiring up unused renderers ───────────
def test_all_theme_styles_resolve():
    """Every style string in the UI layer must exist in the theme.

    Panel titles resolve styles eagerly, so an invalid one is a hard crash
    at render time, not a cosmetic fallback.
    """
    import re as _re
    from app.ui.console import console as _c

    bad = []
    for f in ["app/ui/panels.py", "app/ui/banner.py", "app/ui/progress.py",
              "app/ui/diff.py", "app/main.py"]:
        path = Path(__file__).resolve().parent.parent / f
        if not path.exists():
            continue
        for i, line in enumerate(path.read_text().splitlines(), 1):
            for style in _re.findall(r'style="([^"]+)"', line):
                try:
                    _c.get_style(style)
                except Exception:
                    bad.append(f"{f}:{i} {style!r}")
    check(not bad, f"all UI style strings resolve against the theme ({bad})")


def test_panels_render_without_error():
    """Every newly wired panel must render; invalid styles crash at render."""
    from app.ui import (show_tool_panel, show_command_panel, show_change_panel,
                        show_final_summary, show_code_block, show_truncated)
    from app.ui.console import console as ui_console

    with ui_console.capture() as cap:
        show_tool_panel("Searching", '"def main"')
        show_command_panel("pytest -q")
        show_change_panel("a.py", "old line\n", "new line\n")
        show_final_summary(files_changed=["a.py"], tests_passed=1,
                           tests_failed=0, summary="s", workspace="/w")
        show_code_block("x = 1", "python")
        show_truncated(5, "lines")
    out = cap.get()

    check("COMMAND" in out, "command panel renders")
    check("demo" in out or "a.py" in out, "change panel renders with the file path as title")
    check("+1" in out and "-1" in out, "change panel reports added/removed counts")
    check("Task completed" in out, "final summary renders")
    check("output truncated" in out, "truncation notice is not eaten by Rich markup")


def test_blocked_is_not_inferred_from_output_text():
    """A file whose contents contain 'BLOCKED' must not render as blocked."""
    body = 'content = f"BLOCKED: {loop_msg} Do NOT call {name} again."'
    seq = [_resp(_tc("read_file", path="agent.py")), _resp("Done.")]
    with patch.object(A, "chat", side_effect=seq), \
         patch.object(A.Agent, "_execute_tool", lambda self, n, a: body):
        agent = Agent()
        events = list(agent.step("read agent.py"))
    results = [e for e in events if e.type == "tool_result"]
    check(results and not results[0].data.get("blocked"),
          "output containing 'BLOCKED' is not misclassified as blocked")
    check(results and not results[0].data.get("error"),
          "successful read is not misclassified as an error")


def test_abandoned_turn_detection():
    from app.agent.agent import _is_abandoned_turn as f
    for t in ["", "We need to call list_files.", "We should search for demo.py.",
              "Let me check the config.", "I'll read the file first."]:
        check(f(t), f"deliberation detected: {t[:40]!r}")
    for t in ["4", "Hello! How can I assist you with your code today?",
              "app/main.py is the CLI entry point; it parses args and runs the loop.",
              "We need to consider several things. " + "detail " * 40]:
        check(not f(t), f"real answer not flagged: {t[:40]!r}")


# ── Paste handling ──────────────────────────────────────────────────
def _prompt(seq: str):
    """Drive VektorInput through prompt_toolkit with a scripted key sequence."""
    import tempfile
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    from app.ui.input import VektorInput

    with tempfile.TemporaryDirectory() as d:
        with create_pipe_input() as pin:
            pin.send_text(seq)
            with create_app_session(input=pin, output=DummyOutput()):
                vi = VektorInput(history_path=Path(d) / "h")
                return vi.get_input(), vi


def _paste(text: str) -> str:
    return f"\x1b[200~{text}\x1b[201~"


def test_multiline_paste_is_collapsed_in_prompt():
    body = "\n".join(f"line {i}" for i in range(59))
    res, vi = _prompt(_paste(body) + "\r")
    check(len(vi._pastes) == 1, "multi-line paste is collapsed behind a placeholder")
    check(res.text == body, "agent still receives the full pasted body")
    check(res.paste_info.is_paste and "59 LINES" in res.display_text,
          "paste indicator reports the real line count")


def test_typed_text_and_multiple_pastes_survive():
    a, b = "alpha\nbeta", "gamma\ndelta"
    res, vi = _prompt(f"review {_paste(a)} and {_paste(b)} please\r")
    check(len(vi._pastes) == 2, "each paste gets its own placeholder")
    check(a in res.text and b in res.text, "every paste body is expanded")
    check(res.text.startswith("review") and res.text.endswith("please"),
          "text typed around pastes is preserved")


def test_short_paste_is_not_collapsed():
    res, vi = _prompt(_paste("short one-liner") + "\r")
    check(not vi._pastes and res.text == "short one-liner",
          "a short single-line paste is inserted inline")


def test_pasted_block_starting_with_slash_is_not_a_command():
    res, _ = _prompt(_paste("/help\nextra content") + "\r")
    check(not res.is_command, "multi-line paste beginning with / is content, not a command")
    res2, _ = _prompt("/help\r")
    check(res2.is_command, "a real single-line slash command is still detected")


def test_paste_indicator_survives_rich_rendering():
    """Bracketed text must not be parsed as Rich markup and dropped."""
    from app.ui.console import console as ui_console
    from app.ui.input import analyze_paste, _placeholder

    pi = analyze_paste("\n".join("x" * 90 for _ in range(59)))
    with ui_console.capture() as cap:
        ui_console.print(f"  {pi.format_display()}", style="dim", markup=False)
        ui_console.print(_placeholder(1, 59), markup=False)
    out = cap.get()
    check("59 LINES" in out, "paste indicator renders under Rich")
    check("Pasted 59 lines" in out, "paste placeholder renders under Rich")


def test_unknown_placeholder_is_left_alone():
    from app.ui.input import expand_pastes
    check(expand_pastes("[#9 Pasted 3 lines]", {}) == "[#9 Pasted 3 lines]",
          "an unmatched placeholder is passed through untouched")


# ── Cursor signalling ───────────────────────────────────────────────

def test_cursor_functions_never_raise_on_broken_stdout():
    from app.ui.cursor import reset_cursor, set_cursor_busy, set_cursor_ready

    with patch("sys.stdout.isatty", return_value=True), \
         patch("sys.stdout.write", side_effect=OSError("broken pipe")):
        set_cursor_ready()
        set_cursor_busy()
        reset_cursor()
    check(True, "cursor functions degrade silently on broken stdout")

    # A stdout replaced by something without isatty (some capture shims).
    class Bare:
        pass

    with patch("sys.stdout", Bare()):
        set_cursor_ready()
        reset_cursor()
    check(True, "cursor functions survive a stdout without isatty")


def test_cursor_codes_suppressed_when_not_a_tty():
    """Escape bytes must never land in redirected output."""
    import io
    import sys as _sys
    from app.ui.cursor import reset_cursor, set_cursor_busy, set_cursor_ready

    buffer = io.StringIO()          # StringIO.isatty() is False
    real = _sys.stdout
    _sys.stdout = buffer
    try:
        set_cursor_ready()
        set_cursor_busy()
        reset_cursor()
    finally:
        _sys.stdout = real
    check(buffer.getvalue() == "",
          f"no escape codes written to a non-tty (got {buffer.getvalue()!r})")


def test_cursor_codes_written_when_a_tty():
    import io
    import sys as _sys
    from app.ui.cursor import reset_cursor, set_cursor_busy, set_cursor_ready

    class FakeTTY(io.StringIO):
        def isatty(self):
            return True

    buffer = FakeTTY()
    real = _sys.stdout
    _sys.stdout = buffer
    try:
        set_cursor_ready()
        set_cursor_busy()
        reset_cursor()
    finally:
        _sys.stdout = real
    written = buffer.getvalue()
    # ready = blinking block (widest), busy = same block without the blink.
    check(written == "\x1b[1 q\x1b[2 q\x1b[0 q",
          f"correct DECSCUSR codes emitted to a tty (got {written!r})")


def test_cursor_shape_is_configurable():
    import io
    import os as _os
    import sys as _sys
    from app.ui.cursor import SHAPES, set_cursor_ready

    class FakeTTY(io.StringIO):
        def isatty(self):
            return True

    def emit():
        buf = FakeTTY()
        real = _sys.stdout
        _sys.stdout = buf
        try:
            set_cursor_ready()
        finally:
            _sys.stdout = real
        return buf.getvalue()

    before = _os.environ.get("VEKTOR_CURSOR_READY")
    try:
        _os.environ["VEKTOR_CURSOR_READY"] = "blinking-underline"
        check(emit() == SHAPES["blinking-underline"], "env var selects the cursor shape")
        _os.environ["VEKTOR_CURSOR_READY"] = "not-a-shape"
        check(emit() == SHAPES["blinking-block"], "an unknown shape falls back to the default")
    finally:
        if before is None:
            _os.environ.pop("VEKTOR_CURSOR_READY", None)
        else:
            _os.environ["VEKTOR_CURSOR_READY"] = before


# ── Presentation layer ──────────────────────────────────────────────
def test_tool_feed_records_steps_and_timing():
    from app.ui.toolfeed import ToolFeed
    from app.ui.console import console as ui_console

    feed = ToolFeed()
    with ui_console.capture() as cap:
        feed.start("list_files", "app/ui")
        feed.finish(True)
        feed.start("search_code", '"nope"')
        feed.finish(False, "Error: invalid regex\nat position 4")
    out = cap.get()

    check("✓" in out and "list_files" in out, "successful step renders with a check mark")
    check("✗" in out and "search_code" in out, "failed step renders with a cross")
    check("invalid regex" in out and "position 4" not in out,
          "failure shows only the first line of the reason")
    check(len(feed.steps) == 2, "feed keeps the full step list, not just a count")

    tally = feed.summary()
    check(tally is not None and "2 tools" in tally.plain and "1 failed" in tally.plain,
          "summary tallies steps and failures")
    check(ToolFeed().summary() is None, "no summary when no tools ran")


def test_status_bar_hierarchy_and_token_toggle():
    from app.ui.panels import show_context_bar, set_token_display, token_display_enabled
    from app.ui.console import console as ui_console

    set_token_display(False)
    with ui_console.capture() as cap:
        show_context_bar("VektorCode", "gpt-oss-120b", 6, 3777, 877, 4654)
    hidden = cap.get()
    check("VektorCode" in hidden and "gpt-oss-120b" in hidden, "workspace and model render")
    check("3,777" not in hidden, "token counts hidden by default")
    check("ready" not in hidden, "status word replaced by a dot")

    set_token_display(True)
    with ui_console.capture() as cap:
        show_context_bar("VektorCode", "gpt-oss-120b", 6, 3777, 877, 4654)
    shown = cap.get()
    check("3,777" in shown and "4,654" in shown, "/tokens reveals token counts")
    check(token_display_enabled(), "toggle state is readable")
    set_token_display(False)


def test_error_panel_collapses_traceback():
    from app.ui.panels import show_error, last_traceback, show_traceback
    from app.ui.console import console as ui_console

    tb = "boom\nTraceback (most recent call last):\n  File 'x.py', line 3\nValueError: boom"
    with ui_console.capture() as cap:
        show_error("ToolError", "boom", tb)
    out = cap.get()
    check("boom" in out, "summary line shown")
    check("Traceback" not in out, "traceback collapsed out of the panel")
    check("/trace to expand" in out, "expansion hint offered")
    check(last_traceback() == tb, "traceback stashed for /trace")

    with ui_console.capture() as cap:
        show_traceback(last_traceback())
    check("ValueError: boom" in cap.get(), "/trace prints the stashed traceback")

    # A later error replaces the stash, so /trace always reflects the latest.
    with ui_console.capture() as cap:
        show_error("ToolError", "boom", "boom")
    check("/trace" not in cap.get(), "no hint when there is nothing extra to show")
    check(last_traceback() == "boom", "stash tracks the most recent error")

    with ui_console.capture() as cap:
        show_traceback("")
    check("No traceback recorded" in cap.get(), "/trace with nothing stashed says so")


def test_diff_panel_reports_counts():
    from app.ui.panels import show_change_panel
    from app.ui.console import console as ui_console

    with ui_console.capture() as cap:
        show_change_panel("demo.py", 'return "hello"\n', 'return "hi"\n')
    out = cap.get()
    check("+1" in out and "-1" in out, "diff header reports added/removed counts")
    check('return "hi"' in out and 'return "hello"' in out, "both sides of the diff render")

    with ui_console.capture() as cap:
        show_change_panel("demo.py", "same\n", "same\n")
    check("no changes" in cap.get(), "identical content reports no changes")


def test_prompt_shows_workspace_and_branch():
    from app.ui.input import prompt_fragments, git_branch

    rendered = "".join(text for _, text in prompt_fragments(Path.cwd()))
    check(Path.cwd().name in rendered, "prompt carries the workspace name")
    check(rendered.rstrip().endswith("›"), "prompt ends with the arrow")
    branch = git_branch(Path.cwd())
    if branch:
        check(f"({branch})" in rendered, "git branch shown inline when in a repo")
    check(git_branch(Path("/")) == "", "no branch outside a repo")


def test_tools_and_commands_tables_render():
    from app.ui.panels import show_tools_table, show_commands_table
    from app.ui.console import console as ui_console

    with ui_console.capture() as cap:
        show_tools_table({"filesystem": [("read_file", "Read file contents")]}, 1)
        show_commands_table([("/help", "Show available commands")])
    out = cap.get()
    check("FILESYSTEM" in out and "read_file" in out, "tool table groups by category")
    check("/help" in out, "command table renders")


from tests._log_isolation import restore as _restore_logs, snapshot as _snapshot_logs

_LOGS_BEFORE = _snapshot_logs()


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        fn()
    _restore_logs(_LOGS_BEFORE)
    print(f"\n{'='*40}\nResults: {_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
