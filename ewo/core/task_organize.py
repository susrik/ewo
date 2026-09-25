"""AI task organization: propose create/merge/split/retitle; apply on confirm.

Proposals are computed on demand and never persisted — the GUI renders them
with confirm/dismiss buttons; confirming calls the deterministic ``apply_*``
functions below. Re-running may surface a dismissed idea again (the LLM is
stateless); that keeps the schema simple.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ewo.core import nuggets, tasks
from ewo.core.llm import LLMClient
from ewo.db.models import Nugget, NuggetStatus, Task, TaskSource, TaskStatus

_MAX_PROPOSALS = 10

_SYSTEM = """You tidy an engineering manager's task list. Tasks collect "nuggets" (work items
extracted from notes). Propose only clearly useful changes, at most 10, fewest possible:

- merge: two tasks track the same work. Picks the better-titled survivor.
- split: one task mixes two clearly separate topics; the listed nuggets move to a new task.
- create: two or more unattached inbox nuggets are obviously the same new piece of work.
- retitle: the title no longer reflects what the attached nuggets say the task is about.

Respond with JSON only, matching: {"proposals": [
  {"kind": "merge", "into_id": int, "from_id": int, "reason": str},
  {"kind": "split", "task_id": int, "nugget_ids": [int], "title": str, "reason": str},
  {"kind": "create", "nugget_ids": [int], "title": str, "reason": str},
  {"kind": "retitle", "task_id": int, "title": str, "reason": str}
]}
Use only the listed task/nugget ids. If nothing needs doing, return an empty list.
"""


class Proposal(BaseModel):
    """One organization suggestion; the GUI renders it with confirm/dismiss."""

    kind: Literal["merge", "split", "create", "retitle"]
    reason: str = ""
    title: str | None = None
    task_id: int | None = None
    into_id: int | None = None
    from_id: int | None = None
    nugget_ids: list[int] = Field(default_factory=list)


class _Proposals(BaseModel):
    proposals: list[Proposal] = Field(default_factory=list)


def _strip_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        stripped = stripped.rsplit("```", 1)[0]
    return stripped.strip()


def _task_digest(task: Task) -> str:
    nuggets_text = "; ".join(n.summary for n in task.nuggets[:5])
    line = f"- #{task.id} [{task.status.value}] {task.title}"
    if nuggets_text:
        line += f" — nuggets: {nuggets_text}"
    return line


def propose_organization(session: Session, llm: LLMClient) -> tuple[list[Proposal], int]:
    """One smart-tier LLM call (+ one retry). Returns (proposals, tokens)."""
    open_tasks = list(
        session.scalars(
            select(Task).where(Task.status.not_in([TaskStatus.DONE, TaskStatus.DROPPED]))
        )
    )
    new_nuggets = list(session.scalars(select(Nugget).where(Nugget.status == NuggetStatus.NEW)))
    task_lines = "\n".join(_task_digest(task) for task in open_tasks) or "- (none)"
    nugget_lines = "\n".join(f"- #{n.id} {n.summary}" for n in new_nuggets) or "- (none)"
    prompt = f"Open tasks:\n{task_lines}\n\nUnattached inbox nuggets:\n{nugget_lines}"

    tokens = 0
    last_error = ""
    parsed: _Proposals | None = None
    for attempt in range(2):
        request = prompt
        if attempt:
            request += (
                f"\n\nYour previous reply was not valid JSON ({last_error}). Reply with JSON only."
            )
        result = llm.complete(request, system=_SYSTEM, smart=True)
        tokens += result.tokens_used
        try:
            parsed = _Proposals.model_validate_json(_strip_fence(result.text))
            break
        except ValidationError as exc:
            last_error = str(exc).splitlines()[0]
    if parsed is None:
        raise ValueError(f"LLM did not return valid proposals: {last_error}")

    return _validate(parsed.proposals, open_tasks, new_nuggets), tokens


def _validate(
    proposals: list[Proposal], open_tasks: list[Task], new_nuggets: list[Nugget]
) -> list[Proposal]:
    """Drop proposals that reference unknown/closed ids or are malformed."""
    task_ids = {task.id for task in open_tasks}
    new_ids = {n.id for n in new_nuggets}
    attached_by_task = {task.id: {n.id for n in task.nuggets} for task in open_tasks}
    valid: list[Proposal] = []
    for proposal in proposals[:_MAX_PROPOSALS]:
        if proposal.kind == "merge":
            ids = {proposal.into_id, proposal.from_id}
            if len(ids) != 2 or not ids <= task_ids:
                continue
        elif proposal.kind == "split":
            if proposal.task_id not in task_ids or not proposal.title:
                continue
            if not set(proposal.nugget_ids) <= attached_by_task.get(proposal.task_id, set()):
                continue
        elif proposal.kind == "create":
            if not proposal.title or len(proposal.nugget_ids) < 2:
                continue
            if not set(proposal.nugget_ids) <= new_ids:
                continue
        elif proposal.kind == "retitle":
            if proposal.task_id not in task_ids or not proposal.title:
                continue
        valid.append(proposal)
    return valid


def apply_merge(session: Session, into_id: int, from_id: int) -> Task:
    """Merge *from_id* into *into_id*: everything moves, the loser is deleted.

    Moves use relationship assignments — plain FK-column writes would leave the
    objects in the loser's in-memory collections, and the delete cascade would
    then nullify (nuggets/notes/children) or delete (links) them at flush.
    """
    into = tasks.get_task(session, into_id)
    loser = tasks.get_task(session, from_id)
    if into.id == loser.id:
        raise ValueError("cannot merge a task into itself")
    for nugget in list(loser.nuggets):
        nugget.task = into
    for note in list(loser.notes):
        note.task = into
    for child in list(loser.children):
        child.parent = into
    existing = {(link.system, link.external_key) for link in into.external_links}
    for link in list(loser.external_links):
        if (link.system, link.external_key) in existing:
            session.delete(link)
        else:
            link.task = into
    session.delete(loser)
    session.commit()
    return into


def apply_split(session: Session, task_id: int, nugget_ids: list[int], title: str) -> Task:
    """Create a sibling task and move the listed nuggets onto it."""
    source = tasks.get_task(session, task_id)
    new_task = tasks.create_task(
        session,
        title=title,
        priority=source.priority,
        source=source.source,
        assignee_id=source.assignee_id,
        parent_id=source.parent_id,
    )
    for nugget_id in nugget_ids:
        nugget = nuggets.get_nugget(session, nugget_id)
        if nugget.task_id == source.id:
            nuggets.move_nugget(session, nugget.id, new_task.id)
    return new_task


def apply_create(
    session: Session,
    title: str,
    nugget_ids: list[int],
    jira_base_url: str | None = None,
) -> Task:
    """Create a task from a cluster of unattached nuggets (they are attached)."""
    task = tasks.create_task(session, title=title, source=TaskSource.NOTES)
    for nugget_id in nugget_ids:
        nugget = nuggets.get_nugget(session, nugget_id)
        if nugget.status == NuggetStatus.NEW:
            nuggets.attach_nugget(session, nugget.id, task_id=task.id, jira_base_url=jira_base_url)
    return task
