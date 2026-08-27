"""Tests for plan-before-execute and reversible checkpoints.

Run directly: python3 tests/test_steering.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.config.config as C

C.WORKSPACE = Path(__file__).resolve().parent.parent

from unittest.mock import MagicMock, patch

import app.agent.agent as A
from app.agent import Agent
from app.agent.classify import Classification, TaskType
from app.agent.planning import (
    PLAN_STEP_THRESHOLD,
    Plan,
    estimate_steps,
    parse_plan,
    propose,
    should_plan,
)
from app.memory.checkpoints import CheckpointStore, set_store

_passed, _failed = 0, 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS: {label}")
    else:
        _failed += 1
        print(f"  FAIL: {label}")


def _reply(text):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = text
    resp.choices[0].message.reasoning_content = None
    resp.usage = None
    return resp


CODING = Classification(TaskType.CODING, 0.9)


# ── Stage 1: the triviality threshold ───────────────────────────────

def test_trivial_requests_skip_planning():
    """Over-asking trains people to rubber-stamp, which defeats the point."""
    trivial = [
        "read app/main.py",
        "show me the last 3 commits",
        "what does estimate_cost do",
        "list the files in app/ui",
        "cat pyproject.toml",
        "fix the typo in README",
        "is python 3.10 enough",
    ]
    for message in trivial:
        check(not should_plan(CODING, message),
              f"no plan for {message!r} (steps={estimate_steps(message)})")


def test_multi_step_requests_do_plan():
    substantial = [
        "refactor the parser and update all the call sites",
        "add a retry to the http client, then write tests, then run them",
        "migrate every tool to the new registry API",
        "rename the config module across the codebase",
    ]
    for message in substantial:
        check(should_plan(CODING, message),
              f"plan for {message!r} (steps={estimate_steps(message)})")


def test_non_coding_types_never_plan():
    """Answering a question or writing prose needs no advance agreement."""
    message = "refactor and rewrite everything across all files"
    for task_type in (TaskType.QA, TaskType.CONVERSATIONAL,
                      TaskType.CREATIVE_WRITING, TaskType.SUMMARIZATION,
                      TaskType.SEARCH):
        check(not should_plan(Classification(task_type, 0.9), message),
              f"{task_type.value} does not plan even for a broad request")


def test_plan_parsing_handles_real_replies():
    plan = parse_plan("1. Read search.py\n2. Add the null check\n3. Run pytest")
    check(plan.steps == ["Read search.py", "Add the null check", "Run pytest"],
          f"numbered list parses ({plan.steps})")

    fallback = parse_plan("- Read the file\n- Patch it\n- Test it")
    check(len(fallback) == 3, f"unnumbered list still parses ({fallback.steps})")

    check(len(parse_plan("")) == 0, "empty reply yields an empty plan")
    check(not parse_plan(""), "an empty plan is falsy")

    long_plan = parse_plan("\n".join(f"{i}. step {i}" for i in range(1, 20)))
    check(len(long_plan) <= 8, f"plans are capped ({len(long_plan)} steps)")


def test_plan_failure_does_not_block_the_turn():
    def broken(_messages):
        raise RuntimeError("endpoint down")

    check(not propose("refactor everything", broken),
          "a failed plan call yields an empty plan rather than raising")


def test_approved_plan_reaches_the_prompt():
    original = A.CLASSIFY_TASKS
    A.CLASSIFY_TASKS = False
    try:
        plan_reply = _reply("1. Read search.py\n2. Patch it\n3. Run tests")
        with patch.object(A, "chat", side_effect=[plan_reply, _reply("Done.")]), \
             patch.object(A, "should_plan", lambda c, m: True):
            agent = Agent()
            agent.plan_reviewer = lambda plan: (True, plan)
            list(agent.step("refactor the parser and update all call sites"))

        injected = [m for m in agent.history if m.get("plan")]
        check(injected, "the approved plan was injected into context")
        check("Read search.py" in injected[0]["content"], "the steps are present")
        check("approved this plan" in injected[0]["content"],
              "framed as agreed, so deviation is explicit")
    finally:
        A.CLASSIFY_TASKS = original


def test_rejected_plan_stops_before_any_work():
    original = A.CLASSIFY_TASKS
    A.CLASSIFY_TASKS = False
    executed = []
    try:
        plan_reply = _reply("1. Delete everything\n2. Start over")
        with patch.object(A, "chat", side_effect=[plan_reply]), \
             patch.object(A, "should_plan", lambda c, m: True), \
             patch.object(A.Agent, "_execute_tool",
                          lambda self, n, a: executed.append(n) or "done"):
            agent = Agent()
            agent.plan_reviewer = lambda plan: (False, None)
            events = list(agent.step("refactor everything across all files"))
    finally:
        A.CLASSIFY_TASKS = original

    check([e.type for e in events] == ["plan_rejected"],
          f"the turn ends at rejection ({[e.type for e in events]})")
    check(not executed, "no tool ran")


def test_planning_is_skipped_without_a_reviewer():
    """One-shot mode has nobody to ask, so it must not stall."""
    original = A.CLASSIFY_TASKS
    A.CLASSIFY_TASKS = False
    try:
        with patch.object(A, "chat", return_value=_reply("Done.")), \
             patch.object(A, "should_plan", lambda c, m: True):
            agent = Agent()
            agent.plan_reviewer = None
            events = list(agent.step("refactor everything across all files"))
    finally:
        A.CLASSIFY_TASKS = original

    check("agent_message" in [e.type for e in events],
          "the turn completes with no reviewer present")
    check(not [m for m in agent.history if m.get("plan")], "no plan was injected")


# ── Stage 2: mid-turn steering ──────────────────────────────────────

def test_corrections_steer_and_follow_ups_wait():
    from app.agent.steering import is_steering

    corrections = [
        "wait, don't touch that file",
        "stop, use a different approach",
        "actually use pytest not unittest",
        "no, the other file",
        "scratch that",
        "instead of rewriting it, just patch the one line",
        "leave the config alone",
    ]
    follow_ups = [
        "also update the README",
        "when you are done, run the tests",
        "and also add a changelog entry",
        "after you finish, commit it",
        "what does estimate_cost do",
        "thanks",
        "add a docstring to the new function",
    ]
    for message in corrections:
        check(is_steering(message), f"steers: {message!r}")
    for message in follow_ups:
        check(not is_steering(message), f"waits: {message!r}")


def test_steering_changes_the_next_tool_call():
    """The core of the feature: a correction must redirect the CURRENT turn,
    not sit in the queue until the original plan has finished."""
    original = A.CLASSIFY_TASKS
    A.CLASSIFY_TASKS = False
    pending = ["wait, don't touch that file"]
    seen_before_next_call = {}

    def source():
        return pending.pop(0) if pending else None

    calls = []

    def scripted(messages, tools=None, model=None):
        calls.append(len(messages))
        # Record whether the correction is visible to this decision.
        seen_before_next_call[len(calls)] = any(
            m.get("steering") for m in messages if isinstance(m, dict)
        )
        if len(calls) == 1:
            return _reply('<tool_call>\n{"name": "read_file", "arguments": {"path": "a.py"}}\n</tool_call>')
        if len(calls) == 2:
            return _reply('<tool_call>\n{"name": "read_file", "arguments": {"path": "b.py"}}\n</tool_call>')
        return _reply("Switched approach as asked.")

    try:
        with patch.object(A, "chat", scripted), \
             patch.object(A.Agent, "_execute_tool", lambda self, n, a: "contents"):
            agent = Agent()
            agent.steering_source = source
            events = list(agent.step("read the files"))
    finally:
        A.CLASSIFY_TASKS = original

    kinds = [e.type for e in events]
    check("steering_applied" in kinds, f"a steering event was emitted ({kinds})")
    check(seen_before_next_call.get(2) is True,
          "the correction was in context for the SECOND decision, not after the turn")
    injected = [m for m in agent.history if m.get("steering")]
    check(injected and "don't touch that file" in injected[0]["content"],
          "the correction text reached the model")
    check("adjust your approach" in injected[0]["content"].lower(),
          "framed as a redirection instruction")


def test_no_steering_source_leaves_the_loop_untouched():
    """One-shot mode has nobody typing; the loop must be unchanged."""
    original = A.CLASSIFY_TASKS
    A.CLASSIFY_TASKS = False
    try:
        tool_call = '<tool_call>\n{"name": "read_file", "arguments": {"path": "a.py"}}\n</tool_call>'
        with patch.object(A, "chat", side_effect=[_reply(tool_call), _reply("Done.")]), \
             patch.object(A.Agent, "_execute_tool", lambda self, n, a: "x"):
            agent = Agent()
            agent.steering_source = None
            events = list(agent.step("read a.py"))
    finally:
        A.CLASSIFY_TASKS = original

    check([e.type for e in events] == ["tool_start", "tool_result", "agent_message"],
          f"the loop is untouched ({[e.type for e in events]})")


def test_queue_extracts_corrections_and_keeps_follow_ups():
    from app.agent.steering import is_steering
    from app.ui.queue import MessageQueue

    queue = MessageQueue()
    queue.submit("also update the README")
    queue.submit("wait, wrong file")
    queue.submit("and add a changelog entry")

    taken = queue.take_matching(is_steering)
    check(taken == "wait, wrong file",
          f"the correction is pulled past the follow-up ({taken!r})")
    check(len(queue) == 2, "the follow-ups stay queued")
    check(queue.items() == ["also update the README", "and add a changelog entry"],
          f"and keep their order ({queue.items()})")
    check(queue.take_matching(is_steering) is None,
          "no further corrections are found")


# ── Stage 4: checkpoints and /undo ──────────────────────────────────

def _fresh_store(tmp: Path) -> CheckpointStore:
    store = CheckpointStore(workspace=tmp)
    store.begin_turn()
    set_store(store)
    return store


def test_undo_restores_previous_content():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = _fresh_store(root)
        target = root / "file.txt"
        target.write_text("ORIGINAL")

        store.snapshot("file.txt")
        target.write_text("EDITED")
        check(target.read_text() == "EDITED", "the edit landed")

        reverted, failed = store.undo()
        check(not failed and target.read_text() == "ORIGINAL",
              f"undo restored the original ({reverted})")
        set_store(None)


def test_undo_removes_a_file_the_agent_created():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = _fresh_store(root)
        target = root / "new.txt"

        store.snapshot("new.txt")   # does not exist yet
        target.write_text("BRAND NEW")

        reverted, failed = store.undo()
        check(not target.exists(), f"the created file was removed ({reverted})")
        check(not failed, "no failures")
        set_store(None)


def test_repeated_undo_does_not_corrupt_state():
    """Called several times in sequence, and past the end."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = _fresh_store(root)
        target = root / "file.txt"
        target.write_text("v0")

        for version in range(1, 4):
            store.snapshot("file.txt")
            target.write_text(f"v{version}")
        check(len(store) == 3 and target.read_text() == "v3", "three edits recorded")

        store.undo()
        check(target.read_text() == "v2", "one step back")
        store.undo(2)
        check(target.read_text() == "v0", "two more steps back to the original")
        check(len(store) == 0, "the stack is empty")

        reverted, failed = store.undo()
        check(reverted == [] and failed == [], "undo past the end is a no-op")
        check(target.read_text() == "v0", "content is unchanged by the no-op")
        set_store(None)


def test_undo_n_reverts_several_files():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = _fresh_store(root)
        first, second = root / "a.txt", root / "b.txt"
        first.write_text("A0")
        second.write_text("B0")

        store.snapshot("a.txt")
        first.write_text("A1")
        store.snapshot("b.txt")
        second.write_text("B1")

        store.undo(2)
        check(first.read_text() == "A0" and second.read_text() == "B0",
              "both files were restored")
        set_store(None)


def test_checkpoint_warns_when_a_commit_would_be_contradicted():
    """Undoing after the agent has committed leaves tree and history out of
    step; the user should be told before it happens, not after."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        run = lambda *a: subprocess.run(a, cwd=str(root), capture_output=True, text=True)
        run("git", "init", "-q")
        run("git", "config", "user.email", "t@example.com")
        run("git", "config", "user.name", "t")
        (root / "file.txt").write_text("v0")
        run("git", "add", ".")
        run("git", "commit", "-q", "-m", "first")

        store = _fresh_store(root)
        store.snapshot("file.txt")
        (root / "file.txt").write_text("v1")

        check(store.conflicts_with_git() is None,
              "no warning while HEAD is unchanged")

        run("git", "add", ".")
        run("git", "commit", "-q", "-m", "second")
        warning = store.conflicts_with_git()
        check(warning is not None and "commit" in warning,
              f"a commit since the snapshot is flagged ({warning})")
        set_store(None)


def test_checkpoints_work_without_git():
    """/undo must not require a repository or any git knowledge."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = _fresh_store(root)
        target = root / "file.txt"
        target.write_text("ORIGINAL")

        store.snapshot("file.txt")
        target.write_text("EDITED")
        check(store.conflicts_with_git() is None, "no git, no warning")
        store.undo()
        check(target.read_text() == "ORIGINAL", "undo works outside a repository")
        set_store(None)


def test_write_tool_snapshots_automatically():
    """The snapshot happens in the tool layer, so no caller can skip it."""
    from app.permissions.mode import PermissionMode, set_mode
    from app.tools import get_registry

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        original_ws = C.WORKSPACE
        C.WORKSPACE = root.resolve()
        set_mode(PermissionMode.FULL_AUTO)
        store = _fresh_store(root)
        try:
            (root / "file.txt").write_text("ORIGINAL")
            get_registry().execute(
                "write_file", {"path": "file.txt", "content": "EDITED"})
            check(len(store) == 1, "the write was checkpointed automatically")
            store.undo()
            check((root / "file.txt").read_text() == "ORIGINAL",
                  "and is reversible")
        finally:
            set_store(None)
            set_mode(PermissionMode.DEFAULT)
            C.WORKSPACE = original_ws


def test_workspace_boundary_uses_containment_not_a_string_prefix():
    """"/work-evil/secret" starts with "/work" as text but is outside it."""
    from app.tools.filesystem import _resolve_safe

    original_ws = C.WORKSPACE
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        (root / "work").mkdir()
        (root / "work-evil").mkdir()
        (root / "work-evil" / "secret.txt").write_text("private")

        C.WORKSPACE = root / "work"
        try:
            for escape in ("../work-evil/secret.txt", "/etc/passwd", "../../etc"):
                try:
                    _resolve_safe(escape)
                    check(False, f"{escape!r} escaped the workspace")
                except PermissionError:
                    check(True, f"{escape!r} is blocked")

            (root / "work" / "ok.txt").write_text("fine")
            check(_resolve_safe("ok.txt").name == "ok.txt",
                  "a legitimate path inside the workspace still resolves")
        finally:
            C.WORKSPACE = original_ws


def test_workspace_boundary_survives_a_symlinked_path():
    """A symlinked workspace (/var -> /private/var on macOS) must not reject
    every path as an escape."""
    from app.tools.filesystem import _resolve_safe

    original_ws = C.WORKSPACE
    with tempfile.TemporaryDirectory() as tmp:
        real = Path(tmp).resolve() / "real"
        real.mkdir()
        link = Path(tmp).resolve() / "link"
        try:
            link.symlink_to(real, target_is_directory=True)
        except OSError:
            check(True, "symlinks unavailable here; skipped")
            C.WORKSPACE = original_ws
            return

        (real / "inside.txt").write_text("content")
        C.WORKSPACE = link          # deliberately the unresolved symlink
        try:
            check(_resolve_safe("inside.txt").name == "inside.txt",
                  "a symlinked workspace does not reject its own files")
        finally:
            C.WORKSPACE = original_ws


def test_unreadable_file_is_not_falsely_reversible():
    """A binary file cannot be safely restored from a text snapshot."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = _fresh_store(root)
        binary = root / "image.bin"
        binary.write_bytes(b"\x00\x01\x02\xff\xfe")

        check(store.snapshot("image.bin") is None,
              "no checkpoint is claimed for unreadable content")
        check(len(store) == 0, "and none is recorded")
        set_store(None)


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        fn()
    print(f"\n{'=' * 40}\nResults: {_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
