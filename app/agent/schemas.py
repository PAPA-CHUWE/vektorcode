"""Structured output schemas for task types that have a checkable shape.

Only summarization, search and Q&A are validated. Creative writing and open
conversation are deliberately excluded — forcing prose through a JSON schema
is the single most likely way this makes the agent worse rather than better.

Schemas are kept minimal on purpose. Over-specifying output makes the model
fill fields for the sake of filling them.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.agent.classify import TaskType


class Summary(BaseModel):
    summary: str = Field(description="A few sentences condensing the material")
    key_points: list[str] = Field(default_factory=list)


class SearchMatch(BaseModel):
    source: str = Field(description="File path or location")
    snippet: str = Field(description="The matching text")


class SearchResults(BaseModel):
    matches: list[SearchMatch] = Field(default_factory=list)


class Answer(BaseModel):
    answer: str
    sources: list[str] = Field(default_factory=list)


_SCHEMAS: dict[TaskType, type[BaseModel]] = {
    TaskType.SUMMARIZATION: Summary,
    TaskType.SEARCH: SearchResults,
    TaskType.QA: Answer,
}


def schema_for(task_type: TaskType) -> type[BaseModel] | None:
    """The model for a task type, or None if its output is free text."""
    return _SCHEMAS.get(task_type)


def schema_instruction(task_type: TaskType) -> str:
    """The prompt fragment asking for schema-conformant JSON."""
    model = schema_for(task_type)
    if model is None:
        return ""
    fields = json.dumps(
        {name: str(field.annotation) for name, field in model.model_fields.items()},
        indent=2,
    )
    return (
        "\nReturn your answer as a single JSON object with exactly these "
        f"fields:\n{fields}\n"
        "Output only the JSON object, with no surrounding prose or markdown fence.\n"
    )


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _candidates(text: str) -> list[str]:
    """Plausible JSON payloads inside a model reply, best first."""
    found: list[str] = []
    stripped = text.strip()
    if stripped.startswith("{"):
        found.append(stripped)
    found.extend(match.strip() for match in _FENCE_RE.findall(text))
    found.extend(_OBJECT_RE.findall(text))
    return found


def validate(task_type: TaskType, text: str) -> tuple[BaseModel | None, str]:
    """Validate a reply against its schema.

    Returns (model, "") on success, or (None, error) describing what was
    wrong — the error is fed back to the model so the retry is informed
    rather than a blind second attempt.
    """
    model = schema_for(task_type)
    if model is None:
        return None, ""  # free text: nothing to validate

    if not text or not text.strip():
        return None, "The response was empty."

    last_error = "No JSON object was found in the response."
    for candidate in _candidates(text):
        try:
            payload = json.loads(candidate)
        except (json.JSONDecodeError, ValueError) as exc:
            last_error = f"The response was not valid JSON: {exc}"
            continue
        if not isinstance(payload, dict):
            last_error = "The response was JSON, but not an object."
            continue
        try:
            return model.model_validate(payload), ""
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                for err in exc.errors()[:5]
            )
            last_error = f"The JSON did not match the required shape — {problems}"
            continue

    return None, last_error


def render(model: BaseModel) -> str:
    """Turn a validated model back into readable text for the user.

    The schema exists to keep the model honest about structure, not to make
    the user read JSON.
    """
    if isinstance(model, Summary):
        parts = [model.summary]
        if model.key_points:
            parts.append("")
            parts.extend(f"- {point}" for point in model.key_points)
        return "\n".join(parts)

    if isinstance(model, SearchResults):
        if not model.matches:
            return "No matches found."
        return "\n".join(f"- **{m.source}** — {m.snippet}" for m in model.matches)

    if isinstance(model, Answer):
        parts = [model.answer]
        if model.sources:
            parts.append("")
            parts.append("Sources: " + ", ".join(model.sources))
        return "\n".join(parts)

    return json.dumps(model.model_dump(), indent=2)


def retry_instruction(error: str) -> str:
    """The corrective message sent after a validation failure."""
    return (
        f"Your previous reply could not be used: {error}\n"
        "Send the JSON object again, correctly formed, with no surrounding "
        "prose or markdown fence."
    )
