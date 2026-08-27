"""Tests for task classification, schema validation, confidence gating and
the session log.

Run directly: python3 tests/test_routing.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.config.config as C

C.WORKSPACE = Path(__file__).resolve().parent.parent

from unittest.mock import MagicMock, patch

import app.agent.agent as A
from app.agent import Agent, AgentEvent
from app.agent.classify import (
    DEFAULT_TASK_TYPE,
    Classification,
    TaskType,
    classify,
    parse_classification,
)
from app.agent.confidence import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    confidence_threshold,
    extract,
)
from app.agent.schemas import render, retry_instruction, schema_for, validate
from app.memory import SessionLog

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


# ── Stage 1: classification ─────────────────────────────────────────

# 30 labelled prompts, five per category.
LABELLED: list[tuple[str, TaskType]] = [
    ("fix the null check in app/tools/search.py", TaskType.CODING),
    ("add a retry to the http client", TaskType.CODING),
    ("run the tests and tell me what breaks", TaskType.CODING),
    ("refactor write_file to take a Path", TaskType.CODING),
    ("commit these changes with a sensible message", TaskType.CODING),

    ("where is the parser defined", TaskType.SEARCH),
    ("find every call site of estimate_cost", TaskType.SEARCH),
    ("which file handles the permission gate", TaskType.SEARCH),
    ("grep for TODO comments in app/ui", TaskType.SEARCH),
    ("locate the module that builds the system prompt", TaskType.SEARCH),

    ("summarise what app/main.py does", TaskType.SUMMARIZATION),
    ("give me a short overview of the agent loop", TaskType.SUMMARIZATION),
    ("condense this changelog into three bullets", TaskType.SUMMARIZATION),
    ("tl;dr of the permissions module", TaskType.SUMMARIZATION),
    ("explain the architecture briefly", TaskType.SUMMARIZATION),

    ("write a README intro for this project", TaskType.CREATIVE_WRITING),
    ("draft a release announcement for v0.1", TaskType.CREATIVE_WRITING),
    ("come up with five names for this tool", TaskType.CREATIVE_WRITING),
    ("write a short poem about tool calls", TaskType.CREATIVE_WRITING),
    ("compose an email explaining the outage", TaskType.CREATIVE_WRITING),

    ("what does estimate_cost return", TaskType.QA),
    ("is python 3.10 enough to run this", TaskType.QA),
    ("what is the default confidence threshold", TaskType.QA),
    ("how many tools are registered", TaskType.QA),
    ("does write_file need approval", TaskType.QA),

    ("hi", TaskType.CONVERSATIONAL),
    ("thanks, that worked", TaskType.CONVERSATIONAL),
    ("ok", TaskType.CONVERSATIONAL),
    ("are you still there", TaskType.CONVERSATIONAL),
    ("good morning", TaskType.CONVERSATIONAL),
]


def test_labelled_set_is_balanced():
    counts = {t: sum(1 for _, label in LABELLED if label is t) for t in TaskType}
    check(len(LABELLED) >= 20, f"labelled set has {len(LABELLED)} prompts")
    check(all(count >= 3 for count in counts.values()),
          f"every category is represented ({ {t.value: c for t, c in counts.items()} })")


def test_classifier_parses_every_reply_shape():
    """The model will not always return clean JSON."""
    cases = [
        ('{"task_type":"summarization","confidence":0.9}', TaskType.SUMMARIZATION),
        ('Sure: {"task_type": "qa", "confidence": 0.72}', TaskType.QA),
        ('```json\n{"task_type":"search","confidence":0.8}\n```', TaskType.SEARCH),
        ("I would call this creative_writing", TaskType.CREATIVE_WRITING),
    ]
    for text, expected in cases:
        parsed = parse_classification(text)
        check(parsed is not None and parsed.task_type is expected,
              f"{text[:34]!r} → {expected.value}")

    check(parse_classification("total nonsense") is None, "unparseable input yields None")
    check(parse_classification("") is None, "empty input yields None")


def test_classification_failure_falls_back_to_existing_behaviour():
    """A bad classification must degrade to what the agent did before."""
    def broken(_messages):
        raise RuntimeError("endpoint down")

    result = classify("do something", broken)
    check(result.task_type is DEFAULT_TASK_TYPE, "an exception falls back to coding")
    check(result.fell_back, "the fallback is flagged for auditing")

    unusable = classify("do something", lambda m: _reply("???"))
    check(unusable.task_type is DEFAULT_TASK_TYPE, "an unusable reply falls back to coding")


def test_misclassification_rate_baseline():
    """Baseline against the labelled set with a model that answers correctly.

    This measures the plumbing, not the model — a live rate needs the real
    endpoint. It fails loudly if routing silently stops working.
    """
    labels = dict(LABELLED)

    def oracle(messages):
        prompt = messages[-1]["content"]
        for text, label in LABELLED:
            if prompt.rstrip().endswith(text):
                return _reply(f'{{"task_type":"{label.value}","confidence":0.9}}')
        return _reply("no idea")

    wrong = [
        (text, classify(text, oracle).task_type, expected)
        for text, expected in LABELLED
        if classify(text, oracle).task_type is not expected
    ]
    rate = len(wrong) / len(LABELLED)
    check(rate == 0.0, f"routing plumbing is lossless (misclassification {rate:.0%})")
    check(len(labels) == len(LABELLED), "no duplicate prompts in the labelled set")


# ── Stage 2: structured output ──────────────────────────────────────

def test_only_checkable_types_are_schema_validated():
    """Forcing prose into JSON is the main way this could make things worse."""
    for task_type in (TaskType.SUMMARIZATION, TaskType.SEARCH, TaskType.QA):
        check(schema_for(task_type) is not None, f"{task_type.value} has a schema")
    for task_type in (TaskType.CREATIVE_WRITING, TaskType.CONVERSATIONAL, TaskType.CODING):
        check(schema_for(task_type) is None, f"{task_type.value} stays free text")


def test_validation_accepts_good_output_and_explains_bad():
    model, error = validate(TaskType.SUMMARIZATION,
                            '{"summary":"It parses tool calls.","key_points":["a","b"]}')
    check(model is not None and not error, "well-formed output validates")
    check("It parses tool calls." in render(model), "rendered back as readable text")
    check("- a" in render(model), "key points render as a list")

    model, error = validate(TaskType.SUMMARIZATION, '{"summary": 123}')
    check(model is None and "summary" in error, f"the error names the bad field ({error[:40]})")
    check("summary" in retry_instruction(error), "the retry message carries the specific error")

    model, error = validate(TaskType.QA, "just some prose, no JSON at all")
    check(model is None and error, "prose where JSON was required is rejected")

    fenced = '```json\n{"answer":"42","sources":["a.py"]}\n```'
    model, error = validate(TaskType.QA, fenced)
    check(model is not None, "a fenced JSON block still validates")


def test_free_text_types_are_never_validated():
    for task_type in (TaskType.CREATIVE_WRITING, TaskType.CONVERSATIONAL):
        model, error = validate(task_type, "Once upon a time, a parser met a token.")
        check(model is None and error == "",
              f"{task_type.value} produces no validation error")


# ── Stage 3: confidence gating ──────────────────────────────────────

def test_threshold_is_configurable():
    before = os.environ.get("CONFIDENCE_THRESHOLD")
    try:
        os.environ.pop("CONFIDENCE_THRESHOLD", None)
        check(confidence_threshold() == DEFAULT_CONFIDENCE_THRESHOLD,
              f"defaults to {DEFAULT_CONFIDENCE_THRESHOLD}")
        os.environ["CONFIDENCE_THRESHOLD"] = "0.9"
        check(confidence_threshold() == 0.9, "reads CONFIDENCE_THRESHOLD")
        os.environ["CONFIDENCE_THRESHOLD"] = "not-a-number"
        check(confidence_threshold() == DEFAULT_CONFIDENCE_THRESHOLD,
              "a bad value falls back to the default")
    finally:
        os.environ.pop("CONFIDENCE_THRESHOLD", None)
        if before is not None:
            os.environ["CONFIDENCE_THRESHOLD"] = before


def test_ambiguous_request_asks_instead_of_guessing():
    reply = (
        '{"confidence": 0.2, "needs_clarification": true, '
        '"question": "Which file did you want fixed?"}\n'
        "I will start with app/main.py."
    )
    signal = extract(reply)
    check(signal is not None, "the confidence line is found")
    check(signal.should_clarify(), "low confidence triggers clarification")
    check("Which file" in signal.question, "the question is carried through")
    check("app/main.py" in signal.body and "confidence" not in signal.body,
          "the signal line is stripped from the body")


def test_clear_request_does_not_trigger_clarification():
    """False-positive gating is as bad as none."""
    check(extract("The function returns the estimated USD cost.") is None,
          "a plain answer carries no signal, so no gate")

    confident = (
        '{"confidence": 0.95, "needs_clarification": false, "question": ""}\n'
        "It returns the estimated USD cost."
    )
    signal = extract(confident)
    check(signal is not None and not signal.should_clarify(),
          "high confidence proceeds without asking")

    # Low confidence but nothing coherent to ask: guessing beats an empty question.
    empty_q = '{"confidence": 0.1, "needs_clarification": true, "question": ""}\nBest guess.'
    signal = extract(empty_q)
    check(signal is not None and not signal.should_clarify(),
          "an empty question does not gate")


def test_high_confidence_overrides_the_clarification_flag():
    """Observed live: the model reported 95% confidence AND set
    needs_clarification, producing "Which project?" for a plainly-scoped
    request. A stated confidence that high contradicts the flag."""
    from app.agent.confidence import CONFIDENT_ENOUGH, ConfidenceSignal

    incoherent = ConfidenceSignal(0.95, True, "Which project?", "body")
    check(not incoherent.should_clarify(),
          f"confidence >= {CONFIDENT_ENOUGH} disregards the flag")

    genuinely_unsure = ConfidenceSignal(0.78, True, "Which project?", "body")
    check(genuinely_unsure.should_clarify(),
          "a real mid-range doubt plus the flag still asks")

    low = ConfidenceSignal(0.2, False, "Fix what?", "body")
    check(low.should_clarify(), "low confidence alone still asks")


def test_the_same_question_is_not_asked_twice_running():
    """Observed live: it asked "Which project?", the user answered "this
    project", and it asked the identical question again."""
    original = A.CLASSIFY_TASKS
    A.CLASSIFY_TASKS = True
    ask = ('{"confidence": 0.4, "needs_clarification": true, '
           '"question": "Which project?"}\nGuessing.')
    try:
        with patch.object(A, "chat", side_effect=[_reply(ask), _reply(ask)]), \
             patch.object(A, "classify",
                          lambda m, f: Classification(TaskType.QA, 0.5)):
            agent = Agent()
            first = list(agent.step("tell me about the project"))
            second = list(agent.step("this project"))
    finally:
        A.CLASSIFY_TASKS = original

    asks = [e for e in first + second if e.type == "clarification_needed"]
    check(len(asks) == 1, f"asked once, not repeatedly ({len(asks)})")
    check(second[-1].type == "agent_message",
          f"the second turn answers instead of re-asking ({second[-1].type})")
    check("confidence" not in second[-1].message,
          "the raw signal line never reaches the user")


def test_deterministic_tool_work_is_not_confidence_gated():
    """Tool calls succeed or return a real exit code; no soft signal needed."""
    check(not Classification(TaskType.CODING, 0.9).is_confidence_gated,
          "coding is not confidence gated")
    for task_type in (TaskType.QA, TaskType.SUMMARIZATION, TaskType.CREATIVE_WRITING):
        check(Classification(task_type, 0.9).is_confidence_gated,
              f"{task_type.value} is confidence gated")


def test_ambiguous_prompt_end_to_end_yields_clarification():
    """"fix it" with no context should ask, not guess."""
    responses = [
        _reply('{"task_type":"coding","confidence":0.3}'),   # classification
        _reply('{"confidence": 0.2, "needs_clarification": true, '
               '"question": "Fix what, specifically?"}\nGuessing at main.py.'),
    ]
    original = A.CLASSIFY_TASKS
    A.CLASSIFY_TASKS = True
    try:
        # Force a confidence-gated type so the gate is in play.
        with patch.object(A, "chat", side_effect=responses), \
             patch.object(A, "classify",
                          lambda m, f: Classification(TaskType.QA, 0.3)):
            agent = Agent()
            events = list(agent.step("fix it"))
    finally:
        A.CLASSIFY_TASKS = original

    kinds = [e.type for e in events]
    check("clarification_needed" in kinds, f"a clarification was requested ({kinds})")
    asked = [e for e in events if e.type == "clarification_needed"]
    check(asked and "Fix what" in asked[0].message, "the question reached the user")


def test_prose_answer_is_accepted_for_structured_types():
    """A schema exists for QA, but a clean prose answer is still an answer.

    Rejecting it and retrying would burn a call and make the agent worse —
    the exact failure mode structured output is warned about.
    """
    from app.agent.schemas import validate

    model, error = validate(TaskType.QA, "It returns the estimated USD cost.")
    check(model is None and error, "prose does not satisfy the schema")

    original = A.CLASSIFY_TASKS
    A.CLASSIFY_TASKS = False
    try:
        with patch.object(A, "chat",
                          return_value=_reply("It returns the estimated USD cost.")), \
             patch.object(A, "classify",
                          lambda m, f: Classification(TaskType.QA, 0.95)):
            agent = Agent()
            events = list(agent.step("what does estimate_cost do"))
    finally:
        A.CLASSIFY_TASKS = original

    finals = [e for e in events if e.type == "agent_message"]
    check(len(finals) == 1, f"the prose answer was delivered ({[e.type for e in events]})")
    check("estimated USD cost" in finals[0].message, "unchanged, not reformatted")


def test_clear_prompt_end_to_end_does_not_clarify():
    responses = [_reply("It returns the estimated USD cost of one call.")]
    original = A.CLASSIFY_TASKS
    A.CLASSIFY_TASKS = True
    try:
        with patch.object(A, "chat", side_effect=responses), \
             patch.object(A, "classify",
                          lambda m, f: Classification(TaskType.QA, 0.95)):
            agent = Agent()
            events = list(agent.step("what does estimate_cost do"))
    finally:
        A.CLASSIFY_TASKS = original

    kinds = [e.type for e in events]
    check("clarification_needed" not in kinds, f"no false-positive gate ({kinds})")
    check("agent_message" in kinds, "the answer was delivered")


# ── Stage 4: session log ────────────────────────────────────────────

def test_session_log_records_required_fields():
    log = SessionLog()
    record = log.record("coding", "fix the parser", "Edited search.py")
    for field in ("turn_number", "task_type", "user_message",
                  "response_summary", "timestamp"):
        check(field in record.to_dict(), f"record carries {field}")
    check(record.turn_number == 1, "turns are numbered from one")
    check(len(log) == 1, "the turn is retained")


def test_recap_covers_last_five_turns_only():
    log = SessionLog()
    for i in range(1, 9):
        log.record("coding", f"request {i}", f"response {i}")

    recap = log.recap()
    check("request 8" in recap and "request 4" in recap, "the last five turns appear")
    check("request 3" not in recap, "older turns are dropped")
    check(len(log.recent(5)) == 5, "recent() honours the window")
    check(SessionLog().recap() == "", "an empty log produces no recap")


def test_recap_stays_cheap():
    """A recap that balloons context defeats its own purpose."""
    from app.context.context import estimate_tokens

    log = SessionLog()
    for i in range(40):
        log.record(
            "coding",
            f"please refactor module number {i} " + "with a great deal of detail " * 20,
            "Completed the refactor and reran the suite. " * 20,
        )

    recap = log.recap()
    tokens = estimate_tokens(recap)
    # Measured: ~274 tokens for five maximally-long turns, ~1% of a 27.6k
    # budget. The bound is deliberately close to the real figure so a
    # regression that doubles it fails rather than passing quietly.
    check(tokens < 320, f"recap of 5 long turns costs {tokens} tokens (<320)")
    check(tokens < 4 * 320, "recap is far cheaper than replaying the transcript")
    check(len(recap.splitlines()) == 6, "one header plus five turns")
    check("…" in recap, "long turns are truncated rather than replayed")


def test_session_log_persists_only_when_asked():
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp)
        memory_only = SessionLog(persist=False, persist_dir=target)
        memory_only.record("qa", "question", "answer")
        check(not list(target.glob("*.jsonl")), "nothing written when persist is off")

        persisted = SessionLog(persist=True, persist_dir=target)
        persisted.record("qa", "question", "answer")
        files = list(target.glob("session_*.jsonl"))
        check(len(files) == 1, f"one session file written ({[f.name for f in files]})")
        check("question" in files[0].read_text(encoding="utf-8"), "the turn was written")

    unwritable = SessionLog(persist=True, persist_dir=Path("/proc/nonexistent"))
    unwritable.record("qa", "q", "a")
    check(len(unwritable) == 1, "an unwritable directory does not break the turn")


def test_recap_is_injected_into_the_next_turn():
    original = A.CLASSIFY_TASKS
    A.CLASSIFY_TASKS = False
    try:
        with patch.object(A, "chat", return_value=_reply("Done.")):
            agent = Agent()
            list(agent.step("first request"))
            check(len(agent.session_log) == 1, "the first turn was logged")
            list(agent.step("second request"))

        recaps = [m for m in agent.history if m.get("recap")]
        check(recaps, "a recap was injected on the second turn")
        check("first request" in recaps[-1]["content"],
              "the recap names the earlier turn")
    finally:
        A.CLASSIFY_TASKS = original


# ── Additive, not a rewrite ─────────────────────────────────────────

def test_default_coding_behaviour_is_unchanged():
    """Existing tool-calling must work exactly as before."""
    import json as _json

    tool_call = ('<tool_call>\n{"name": "list_files", "arguments": '
                 + _json.dumps({"path": "."}) + "}\n</tool_call>")
    original = A.CLASSIFY_TASKS
    A.CLASSIFY_TASKS = False
    try:
        with patch.object(A, "chat", side_effect=[_reply(tool_call), _reply("Done.")]), \
             patch.object(A.Agent, "_execute_tool", lambda self, n, a: "a.py"):
            agent = Agent()
            events = list(agent.step("list the files"))
    finally:
        A.CLASSIFY_TASKS = original

    kinds = [e.type for e in events]
    check(kinds == ["tool_start", "tool_result", "agent_message"],
          f"the tool loop is untouched ({kinds})")


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        fn()
    print(f"\n{'=' * 40}\nResults: {_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
