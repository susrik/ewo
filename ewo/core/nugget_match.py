"""Suggest candidate tasks for unreviewed nuggets (the ``nuggets_match`` job).

Two passes. First a deterministic one: a nugget whose normalised summary
matches an already-attached nugget inherits that nugget's task — this routes
items repeated across several notes files to the same task for free. Then an
LLM pass (cheap tier) matches the rest against the open tasks, and may say
"no task fits" (suggestion cleared → attaching creates a new task).

Suggestions land in ``Nugget.suggested_task_id``; nothing is attached until a
human confirms in the inbox.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ewo.core.llm import LLMClient
from ewo.core.nuggets import norm_words
from ewo.db.models import Nugget, NuggetStatus, Task, TaskStatus

_CHUNK = 30
_MAX_TASKS = 150

_SYSTEM = """You match inbox items extracted from an engineering manager's notes to their
existing tracked tasks. For each item decide which single task it is an update for or a
detail of — or null when no existing task fits (a genuinely new piece of work).

Match on meaning, not keywords: the same work is often phrased differently across notes.
Only match when you are confident the item belongs to the task.

Respond with JSON only, matching: {"matches": [{"item": int, "task": int|null}]}
Cover every item exactly once; task must be one of the listed task ids or null.
"""


class _Match(BaseModel):
    item: int
    task: int | None = None


class _MatchResult(BaseModel):
    matches: list[_Match] = Field(default_factory=list)


@dataclass
class SuggestSummary:
    reviewed: int = 0
    suggested: int = 0
    no_match: int = 0
    tokens: int = 0
    errors: list[str] = field(default_factory=list)

    def as_text(self) -> str:
        text = (
            f"reviewed={self.reviewed} suggested={self.suggested} "
            f"new={self.no_match} tokens={self.tokens}"
        )
        if self.errors:
            text += f" errors={len(self.errors)}"
        return text


def _open_tasks(session: Session) -> list[Task]:
    return list(
        session.scalars(
            select(Task)
            .where(Task.status.not_in([TaskStatus.DONE, TaskStatus.DROPPED]))
            .order_by(Task.updated_at.desc())
            .limit(_MAX_TASKS)
        )
    )


def _suggest_from_attached(session: Session, candidates: list[Nugget]) -> dict[int, int]:
    """nugget id → task id, reusing earlier attach decisions on identical text."""
    attached = session.scalars(
        select(Nugget).where(Nugget.status == NuggetStatus.ATTACHED, Nugget.task_id.is_not(None))
    )
    task_ids = {task.id for task in _open_tasks(session)}
    by_words: dict[str, int] = {}
    for other in attached:
        if other.task_id in task_ids:
            by_words.setdefault(norm_words(other.summary), other.task_id)
    suggestions: dict[int, int] = {}
    for nugget in candidates:
        task_id = by_words.get(norm_words(nugget.summary))
        if task_id is not None:
            suggestions[nugget.id] = task_id
    return suggestions


def _strip_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        stripped = stripped.rsplit("```", 1)[0]
    return stripped.strip()


def _match_chunk(
    llm: LLMClient, candidates: list[Nugget], tasks: list[Task]
) -> tuple[_MatchResult, int]:
    """One LLM call (plus one retry on invalid JSON). Returns (result, tokens)."""
    task_lines = "\n".join(f"- #{task.id} {task.title}" for task in tasks) or "- (none)"
    item_lines = "\n".join(
        f"- #{nugget.id} [{nugget.kind.value}] {nugget.summary} (note: {nugget.path})"
        for nugget in candidates
    )
    prompt = f"Open tasks:\n{task_lines}\n\nInbox items:\n{item_lines}"
    tokens = 0
    last_error = ""
    for attempt in range(2):
        request = prompt
        if attempt:
            request += (
                f"\n\nYour previous reply was not valid JSON ({last_error}). Reply with JSON only."
            )
        result = llm.complete(request, system=_SYSTEM, smart=False)
        tokens += result.tokens_used
        try:
            return _MatchResult.model_validate_json(_strip_fence(result.text)), tokens
        except ValidationError as exc:
            last_error = str(exc).splitlines()[0]
    raise ValueError(f"LLM did not return valid matches: {last_error}")


def suggest_matches(
    session: Session,
    llm: LLMClient,
    nugget_ids: list[int] | None = None,
) -> SuggestSummary:
    """Compute suggested tasks for NEW nuggets; store them on the nuggets."""
    summary = SuggestSummary()
    query = select(Nugget).where(Nugget.status == NuggetStatus.NEW).order_by(Nugget.id)
    if nugget_ids is not None:
        query = query.where(Nugget.id.in_(nugget_ids))
    candidates = list(session.scalars(query))
    if not candidates:
        return summary
    summary.reviewed = len(candidates)

    # deterministic pass: duplicates of already-attached nuggets
    deterministic = _suggest_from_attached(session, candidates)
    remaining = [n for n in candidates if n.id not in deterministic]

    # LLM pass over the rest, in chunks
    tasks = _open_tasks(session)
    llm_suggestions: dict[int, int | None] = {}
    if tasks:
        for start in range(0, len(remaining), _CHUNK):
            chunk = remaining[start : start + _CHUNK]
            try:
                result, tokens = _match_chunk(llm, chunk, tasks)
            except ValueError as exc:
                summary.errors.append(str(exc))
                continue
            summary.tokens += tokens
            task_ids = {task.id for task in tasks}
            matched_items: set[int] = set()
            for match in result.matches:
                if match.item in matched_items or match.item not in {n.id for n in chunk}:
                    continue
                matched_items.add(match.item)
                llm_suggestions[match.item] = match.task if match.task in task_ids else None

    for nugget in candidates:
        if nugget.id in deterministic:
            nugget.suggested_task_id = deterministic[nugget.id]
            summary.suggested += 1
        elif nugget.id in llm_suggestions:
            nugget.suggested_task_id = llm_suggestions[nugget.id]
            if llm_suggestions[nugget.id] is None:
                summary.no_match += 1
            else:
                summary.suggested += 1
    session.commit()
    return summary
