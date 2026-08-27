"""Tests for the skills loader, matcher, and injection.

The previous version of this file opened paths relative to the repo's
*parent* directory, so it raised FileNotFoundError from the project root and
was in no test suite. It also only checked that two files' frontmatter
parsed — nothing about the skills reaching the model, because at the time
nothing loaded them.

Run directly: python3 tests/test_skills.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.config.config as C

C.WORKSPACE = Path(__file__).resolve().parent.parent

from unittest.mock import MagicMock, patch

from app.agent import Agent
from app.skills.loader import (
    MAX_SKILLS_PER_TURN,
    Skill,
    load_skills,
    match_skills,
    parse_skill,
    render_skills,
    skills_dir,
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


# ── Parsing ─────────────────────────────────────────────────────────

def test_every_shipped_skill_parses():
    files = sorted(skills_dir().glob("*.md"))
    skills = load_skills()
    check(len(files) > 0, f"skill files are present ({len(files)} found)")
    check(len(skills) == len(files),
          f"every skill file parses ({len(skills)}/{len(files)})")

    names = [s.name for s in skills]
    check(len(names) == len(set(names)), "skill names are unique")

    missing = [s.name for s in skills if not s.body.strip()]
    check(not missing, f"every skill has a body ({missing})")


def test_frontmatter_fields_are_extracted():
    skill = parse_skill(
        "---\n"
        "name: demo-skill\n"
        "triggers: [alpha, beta gamma, 'quoted']\n"
        "---\n\n"
        "# Demo\n\nGuidance body here.\n"
    )
    check(skill is not None, "a well-formed skill parses")
    check(skill.name == "demo-skill", f"name extracted ({skill.name})")
    check(skill.triggers == ("alpha", "beta gamma", "quoted"),
          f"triggers parsed and unquoted ({skill.triggers})")
    check("Guidance body here." in skill.body, "body captured")
    check("---" not in skill.body, "frontmatter stripped from the body")


def test_malformed_skill_is_skipped_not_fatal():
    check(parse_skill("no frontmatter at all") is None, "a file without frontmatter is skipped")
    # A real file with no `name:` falls back to its filename...
    from_file = parse_skill("---\ntriggers: [x]\n---\nbody", Path("some-skill.md"))
    check(from_file is not None and from_file.name == "some-skill",
          "a missing name falls back to the filename")
    # ...but with no name and no path there is nothing to identify it by.
    check(parse_skill("---\ntriggers: [x]\n---\nbody") is None,
          "an unidentifiable skill is skipped")
    check(load_skills(Path("/nonexistent/skills")) == [],
          "a missing directory returns no skills instead of raising")


# ── Matching ────────────────────────────────────────────────────────

def test_triggers_match_case_insensitively():
    skills = [Skill(name="s", triggers=("same error", "stuck"), body="b")]
    check(match_skills("I am STUCK again", skills), "matching ignores case")
    check(not match_skills("everything is fine", skills), "unrelated text matches nothing")
    check(match_skills("", skills) == [], "empty input matches nothing")


def test_shipped_skills_match_their_intended_situations():
    skills = load_skills()
    expectations = {
        "im stuck, the same error keeps happening": "stall-recognition",
        "rename and move the parser to another folder": "cross-file-refactor-caution",
    }
    for message, expected in expectations.items():
        names = [s.name for s in match_skills(message, skills)]
        check(expected in names, f"{message!r} → {expected} (got {names})")


def test_capability_honesty_skill_matches_vision_question():
    """The vision hallucination: the model claimed it could analyse images."""
    skills = load_skills()
    for question in [
        "can you see this image and describe it",
        "look at this screenshot",
        "analyze this image for me",
    ]:
        names = [s.name for s in match_skills(question, skills)]
        check("capability-honesty" in names,
              f"{question!r} → capability-honesty (got {names})")


def test_negative_result_skill_matches_existence_questions():
    """Asked "is the colour palette being used", the agent searched for the
    literal word "palette", found nothing, and answered no — while the
    palette sat in console.py as VEKTOR_THEME."""
    skills = load_skills()
    for question in [
        "is the colour palette being used now",
        "do we have any rate limiting",
        "is there a config file for this",
        "does it have dark mode",
    ]:
        names = [s.name for s in match_skills(question, skills)]
        check("negative-result-caution" in names,
              f"{question!r} → negative-result-caution (got {names})")

    # Must not fire on ordinary work.
    quiet = [s.name for s in match_skills("read app/main.py and summarise it", skills)]
    check("negative-result-caution" not in quiet,
          f"a plain read request does not trigger it (got {quiet})")


def test_injection_is_capped():
    """All 16 bodies are ~13KB; a small context window cannot take them all."""
    many = [Skill(name=f"s{i}", triggers=("common",), body=f"body {i}") for i in range(10)]
    matched = match_skills("common", many)
    check(len(matched) <= MAX_SKILLS_PER_TURN,
          f"no more than {MAX_SKILLS_PER_TURN} skills per turn (got {len(matched)})")


def test_more_specific_match_ranks_first():
    skills = [
        Skill(name="broad", triggers=("file",), body="b"),
        Skill(name="precise", triggers=("cross-file refactor", "file"), body="b"),
    ]
    ranked = [s.name for s in match_skills("planning a cross-file refactor", skills)]
    check(ranked and ranked[0] == "precise",
          f"the more specific skill ranks first (got {ranked})")


def test_render_produces_guidance_or_nothing():
    check(render_skills([]) == "", "no skills renders to an empty string")
    rendered = render_skills([Skill(name="s", triggers=(), body="Do the thing.")])
    check("Do the thing." in rendered, "the body is rendered")


# ── Injection into the agent ────────────────────────────────────────

def _reply(text):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = text
    resp.choices[0].message.reasoning_content = None
    resp.usage = None
    return resp


def test_matching_skill_is_injected_into_history():
    import app.agent.agent as A

    with patch.object(A, "chat", return_value=_reply("Understood.")):
        agent = Agent()
        events = list(agent.step("im stuck, the same error keeps happening"))

    injected = [m for m in agent.history if m.get("skill")]
    check(injected, "a matching skill is injected into history")
    check(any(e.type == "skills_applied" for e in events),
          "the UI is told which skills fired")
    check(any("stall" in m["content"].lower() for m in injected),
          "the injected content is the skill body")


def test_skill_is_not_reinjected_on_later_turns():
    """A long conversation must not accumulate the same guidance repeatedly."""
    import app.agent.agent as A

    with patch.object(A, "chat", return_value=_reply("Understood.")):
        agent = Agent()
        list(agent.step("im stuck, same error"))
        first = len([m for m in agent.history if m.get("skill")])
        events = list(agent.step("still stuck with the same error"))
        second = len([m for m in agent.history if m.get("skill")])

    check(first == second, f"no duplicate injection ({first} → {second})")
    check(not any(e.type == "skills_applied" for e in events),
          "no skills_applied event when nothing new fired")


def test_unrelated_request_injects_nothing():
    import app.agent.agent as A

    with patch.object(A, "chat", return_value=_reply("Hello.")):
        agent = Agent()
        events = list(agent.step("hello"))

    check(not [m for m in agent.history if m.get("skill")],
          "an unrelated message injects no skills")
    check(not any(e.type == "skills_applied" for e in events),
          "no skills_applied event for an unrelated message")


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        fn()
    print(f"\n{'=' * 40}\nResults: {_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
