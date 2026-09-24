"""Extract outstanding items from notes with the LLM (the ``notes_scan`` job).

Reproduces the manual "overview" process from the notes tree: for each note in
scope, ask the LLM for open actions, questions, deadlines and risks, each with a
``line`` citation, attributed to a team member where the note says so. The
per-folder ``AGENTS.md`` chain is supplied as system context, together with the
team roster and the disambiguation rules, so status-changing facts ("proposal
submitted", "James L ≠ James S") are respected.

Results land in the ``note_items`` inbox; the notes themselves are never modified.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ewo.config import NotesConfig
from ewo.core import note_items
from ewo.core.llm import LLMClient
from ewo.core.notes import (
    NotesReader,
    format_numbered,
    in_window_lines,
    numbered_lines,
)
from ewo.core.people import list_people, resolve_person
from ewo.db.models import JobRun, JobRunStatus, NoteItemKind, Person

ROLLING_FILENAMES = frozenset({"old.md"})

_SYSTEM = """You are an assistant that extracts outstanding work from an engineering manager's
personal meeting notes. Report only what the notes say; do not editorialise or soften.
Preserve ticket numbers, system names, figures and dates exactly as written.

Rules:
- Only report items that are still open as of the note. If the note (or the context) says
  something has been completed, submitted or superseded, do NOT report it.
- Each item cites the 1-based line number of the note where it appears (lines are prefixed
  "N: " in the input). Never invent an item that has no line.
- `owner` is the person responsible: the manager themself when the notes say "I", "me",
  "Erik" or the action is clearly theirs; a team member's name when it is theirs; otherwise
  null. Use the roster spelling. If a bare first name is ambiguous, leave it exactly as
  written and do not guess.
- Kinds: action (something to do), question (decision pending / open question),
  deadline (a hard future date), risk (concern or topic to review).
- Merge duplicates within the note. Keep `summary` under 120 characters, imperative mood.
- Respond with JSON only, matching: {"items": [{"summary": str, "line": int,
  "kind": "action|question|deadline|risk", "owner": str|null, "due_date": "YYYY-MM-DD"|null}]}
"""


class ExtractedItem(BaseModel):
    summary: str = Field(min_length=1)
    line: int = Field(ge=1)
    kind: NoteItemKind = NoteItemKind.ACTION
    owner: str | None = None
    due_date: date | None = None


class ExtractionResult(BaseModel):
    items: list[ExtractedItem] = Field(default_factory=list)


@dataclass
class ScanSummary:
    files: int = 0
    created: int = 0
    seen: int = 0
    tokens: int = 0
    errors: list[str] = field(default_factory=list)

    def as_text(self) -> str:
        text = f"files={self.files} new={self.created} seen={self.seen} tokens={self.tokens}"
        if self.errors:
            text += f" errors={len(self.errors)}"
        return text


def roster_text(people: list[Person]) -> str:
    lines = []
    for person in people:
        role = " (the manager — use for first-person items)" if person.is_self else ""
        aliases = f" — also written as: {', '.join(person.aliases)}" if person.aliases else ""
        lines.append(f"- {person.name}{role}{aliases}")
    return "\n".join(lines) or "- (no roster configured)"


def _strip_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        stripped = stripped.rsplit("```", 1)[0]
    return stripped.strip()


def extract_items(
    llm: LLMClient,
    context: str,
    roster: str,
    path: str,
    body: str,
    today: date,
) -> tuple[ExtractionResult, int]:
    """One LLM call (plus one retry on invalid JSON). Returns (result, tokens)."""
    system = _SYSTEM
    if context:
        system += f"\n\nContext for this note's folder (general → specific):\n\n{context}"
    prompt = f"Today is {today.isoformat()}.\n\nTeam roster:\n{roster}\n\nNote `{path}`:\n\n{body}"
    tokens = 0
    last_error = ""
    for attempt in range(2):
        request = prompt
        if attempt:
            request += (
                f"\n\nYour previous reply was not valid JSON ({last_error}). Reply with JSON only."
            )
        result = llm.complete(request, system=system, smart=True)
        tokens += result.tokens_used
        try:
            return ExtractionResult.model_validate_json(_strip_fence(result.text)), tokens
        except ValidationError as exc:
            last_error = str(exc).splitlines()[0]
    raise ValueError(f"LLM did not return valid items for {path}: {last_error}")


def last_successful_scan(session: Session, job_name: str = "notes_scan") -> datetime | None:
    return session.scalar(
        select(JobRun.started_at)
        .where(JobRun.job_name == job_name, JobRun.status == JobRunStatus.SUCCESS)
        .order_by(JobRun.started_at.desc())
        .limit(1)
    )


def scan_notes(
    session: Session,
    llm: LLMClient,
    config: NotesConfig,
    since: datetime | None,
    job_run_id: int | None = None,
    today: date | None = None,
) -> ScanSummary:
    """Scan notes changed since *since* (or the whole window when None)."""
    today = today or date.today()
    window_start = today - timedelta(days=config.window_days)
    reader = NotesReader(config.root, exclude=tuple(config.exclude))
    people = list_people(session)
    roster = roster_text(people)
    summary = ScanSummary()

    for note in reader.changed_since(since, window_start):
        lines = numbered_lines(reader.read(note.path))
        if note.path.rsplit("/", 1)[-1] in ROLLING_FILENAMES:
            lines = in_window_lines(lines, window_start)
        if not any(text.strip() for _, text in lines):
            continue
        body = format_numbered(lines)
        if len(body) > config.max_file_chars:
            body = body[: config.max_file_chars]
        summary.files += 1
        try:
            result, tokens = extract_items(
                llm, reader.context_chain(note.path), roster, note.path, body, today
            )
        except ValueError as exc:
            summary.errors.append(str(exc))
            continue
        summary.tokens += tokens
        line_text = dict(lines)
        for extracted in result.items:
            owner = resolve_person(session, extracted.owner, note.path)
            _, created = note_items.upsert_item(
                session,
                path=note.path,
                line=extracted.line,
                summary=extracted.summary,
                kind=extracted.kind,
                excerpt=line_text.get(extracted.line),
                owner_id=owner.id if owner else None,
                owner_name=extracted.owner,
                due_date=extracted.due_date,
                job_run_id=job_run_id,
            )
            summary.created += created
            summary.seen += not created
        session.commit()
    return summary
