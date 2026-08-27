"""Load and match the markdown skills in this directory.

Each skill is a markdown file with YAML-ish frontmatter:

    ---
    name: stall-recognition
    triggers: [stuck, not working, same error]
    ---

    # Recognizing a stall
    ...guidance the model should follow...

The bodies total ~13KB — too much to paste into every system prompt when
the budget is 27.6k on some models. So skills are matched against the
user's message by trigger and only the relevant ones are injected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# Frontmatter delimited by --- fences at the very start of the file.
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)
_TRIGGERS_RE = re.compile(r"^triggers:\s*\[(.*?)\]\s*$", re.MULTILINE)
_NAME_RE = re.compile(r"^name:\s*(.+?)\s*$", re.MULTILINE)

# Injecting every match could swamp a small context window.
MAX_SKILLS_PER_TURN = 3


@dataclass(frozen=True)
class Skill:
    """One piece of guidance, activated by trigger phrases."""

    name: str
    triggers: tuple[str, ...]
    body: str
    path: Path | None = None

    def matches(self, text: str) -> list[str]:
        """Trigger phrases present in text, case-insensitively."""
        lowered = text.lower()
        return [t for t in self.triggers if t and t in lowered]


def parse_skill(text: str, path: Path | None = None) -> Skill | None:
    """Parse one skill file. Returns None if it has no usable frontmatter."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return None
    frontmatter, body = match.group(1), match.group(2).strip()

    name_match = _NAME_RE.search(frontmatter)
    name = name_match.group(1).strip() if name_match else (path.stem if path else "")
    if not name:
        return None

    triggers: tuple[str, ...] = ()
    triggers_match = _TRIGGERS_RE.search(frontmatter)
    if triggers_match:
        triggers = tuple(
            t.strip().strip("\"'").lower()
            for t in triggers_match.group(1).split(",")
            if t.strip()
        )

    return Skill(name=name, triggers=triggers, body=body, path=path)


def skills_dir() -> Path:
    """Directory holding the skill markdown files."""
    return Path(__file__).resolve().parent


def load_skills(directory: Path | str | None = None) -> list[Skill]:
    """Load every skill in a directory, sorted by name.

    Never raises: an unreadable or malformed skill is skipped rather than
    breaking startup over a documentation file.
    """
    target = Path(directory) if directory is not None else skills_dir()
    skills: list[Skill] = []
    try:
        paths = sorted(target.glob("*.md"))
    except OSError:
        return skills

    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        skill = parse_skill(text, path)
        if skill is not None:
            skills.append(skill)
    return skills


def match_skills(
    message: str,
    skills: list[Skill] | None = None,
    limit: int = MAX_SKILLS_PER_TURN,
) -> list[Skill]:
    """Skills whose triggers appear in message, most specific first.

    Ranked by how many triggers matched, then by the longest match, so a
    skill fired by a precise phrase outranks one caught by a common word.
    """
    if not message.strip():
        return []
    candidates = skills if skills is not None else load_skills()

    scored: list[tuple[int, int, str, Skill]] = []
    for skill in candidates:
        hits = skill.matches(message)
        if hits:
            scored.append((len(hits), max(len(h) for h in hits), skill.name, skill))

    scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
    return [skill for _, _, _, skill in scored[:limit]]


def render_skills(skills: list[Skill]) -> str:
    """Render matched skills as a single guidance block for the model."""
    if not skills:
        return ""
    parts = [
        "Relevant guidance for this request "
        "(follow it; it was written for situations like this one):",
        "",
    ]
    for skill in skills:
        parts.append(skill.body)
        parts.append("")
    return "\n".join(parts).strip()
