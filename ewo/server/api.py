"""/api — user-facing JSON API (used by CLI, listeners, MCP, external tools)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ewo.config import Config
from ewo.core import note_items, one_on_ones, people, reports, tasks, whatnext
from ewo.core.llm import LLMClient
from ewo.core.notes import NotesReader
from ewo.core.people import NotFoundError
from ewo.db.models import JobRun, NoteItemStatus, Report, TaskStatus
from ewo.jobs.registry import JobRegistry, UnknownJobError
from ewo.server import schemas
from ewo.server.deps import get_config, get_llm, get_registry, get_session, get_session_factory

router = APIRouter(prefix="/api")

SessionDep = Annotated[Session, Depends(get_session)]


# --- people ---


@router.post("/people", response_model=schemas.PersonOut, status_code=201)
def create_person(body: schemas.PersonCreate, session: SessionDep) -> object:
    return people.create_person(session, **body.model_dump())


@router.get("/people", response_model=list[schemas.PersonOut])
def list_people(session: SessionDep) -> object:
    return people.list_people(session)


@router.get("/people/{person_id}", response_model=schemas.PersonOut)
def get_person(person_id: int, session: SessionDep) -> object:
    try:
        return people.get_person(session, person_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/people/{person_id}", response_model=schemas.PersonOut)
def update_person(person_id: int, body: schemas.PersonUpdate, session: SessionDep) -> object:
    fields = body.model_dump(exclude_unset=True)
    try:
        return people.update_person(session, person_id, **fields)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/people/{person_id}", response_model=schemas.MessageOut)
def delete_person(person_id: int, session: SessionDep) -> object:
    try:
        people.delete_person(session, person_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return schemas.MessageOut(detail="deleted")


@router.post("/people/seed-from-notes", response_model=schemas.SeedResultOut)
def seed_people(session: SessionDep, config: Annotated[Config, Depends(get_config)]) -> object:
    """Create a person per team-member folder in the notes tree."""
    if not config.notes.enabled:
        raise HTTPException(status_code=400, detail="notes are not enabled in config")
    reader = NotesReader(config.notes_root)
    created = people.seed_from_notes(session, reader.people_dirs(config.notes.people_dir))
    return schemas.SeedResultOut(created=[schemas.PersonOut.model_validate(p) for p in created])


# --- tasks ---


@router.post("/tasks", response_model=schemas.TaskOut, status_code=201)
def create_task(body: schemas.TaskCreate, session: SessionDep) -> object:
    return tasks.create_task(session, **body.model_dump())


@router.get("/tasks", response_model=list[schemas.TaskOut])
def list_tasks(
    session: SessionDep,
    status: str | None = None,
    assignee_id: int | None = None,
    tag: str | None = None,
    include_closed: bool = False,
) -> object:
    parsed_status = TaskStatus(status) if status else None
    return tasks.list_tasks(
        session,
        status=parsed_status,
        assignee_id=assignee_id,
        tag=tag,
        include_closed=include_closed,
    )


@router.get("/tasks/{task_id}", response_model=schemas.TaskOut)
def get_task(task_id: int, session: SessionDep) -> object:
    try:
        return tasks.get_task(session, task_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/tasks/{task_id}", response_model=schemas.TaskOut)
def update_task(task_id: int, body: schemas.TaskUpdate, session: SessionDep) -> object:
    fields = body.model_dump(exclude_unset=True)
    tags = fields.pop("tags", None)
    try:
        return tasks.update_task(session, task_id, tags=tags, **fields)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/tasks/{task_id}", response_model=schemas.MessageOut)
def delete_task(task_id: int, session: SessionDep) -> object:
    try:
        tasks.delete_task(session, task_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return schemas.MessageOut(detail="deleted")


@router.post("/notes", response_model=schemas.NoteOut, status_code=201)
def add_note(body: schemas.NoteCreate, session: SessionDep) -> object:
    return tasks.add_note(session, **body.model_dump())


# --- inbox: items discovered in notes ---


@router.get("/note-items", response_model=list[schemas.NoteItemOut])
def list_note_items(
    session: SessionDep,
    status: str | None = "new",
    owner_id: int | None = None,
    path_prefix: str | None = None,
) -> object:
    parsed = NoteItemStatus(status) if status else None
    return note_items.list_items(session, status=parsed, owner_id=owner_id, path_prefix=path_prefix)


@router.patch("/note-items/{item_id}", response_model=schemas.NoteItemOut)
def update_note_item(item_id: int, body: schemas.NoteItemUpdate, session: SessionDep) -> object:
    try:
        return note_items.update_item(session, item_id, **body.model_dump(exclude_unset=True))
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/note-items/{item_id}/accept", response_model=schemas.TaskOut, status_code=201)
def accept_note_item(item_id: int, body: schemas.NoteItemAccept, session: SessionDep) -> object:
    try:
        return note_items.accept_item(session, item_id, **body.model_dump())
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/note-items/{item_id}/dismiss", response_model=schemas.NoteItemOut)
def dismiss_note_item(item_id: int, session: SessionDep) -> object:
    try:
        return note_items.set_item_status(session, item_id, NoteItemStatus.DISMISSED)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/note-items/{item_id}/done", response_model=schemas.NoteItemOut)
def note_item_already_done(item_id: int, session: SessionDep) -> object:
    """The note still lists it, but it is finished — record that without editing the note."""
    try:
        return note_items.set_item_status(session, item_id, NoteItemStatus.ALREADY_DONE)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# --- one-on-ones ---


@router.post("/one-on-ones", response_model=schemas.OneOnOneOut, status_code=201)
def create_one_on_one(body: schemas.OneOnOneCreate, session: SessionDep) -> object:
    try:
        return one_on_ones.create_one_on_one(session, body.person_id, body.scheduled_for)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/one-on-ones", response_model=list[schemas.OneOnOneOut])
def list_one_on_ones(session: SessionDep, person_id: int | None = None) -> object:
    return one_on_ones.list_one_on_ones(session, person_id=person_id)


@router.post(
    "/one-on-ones/{meeting_id}/agenda",
    response_model=schemas.AgendaItemOut,
    status_code=201,
)
def add_agenda_item(meeting_id: int, body: schemas.AgendaItemCreate, session: SessionDep) -> object:
    try:
        return one_on_ones.add_agenda_item(session, meeting_id, body.topic)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/agenda-items/{item_id}", response_model=schemas.AgendaItemOut)
def update_agenda_item(item_id: int, body: schemas.AgendaItemUpdate, session: SessionDep) -> object:
    try:
        return one_on_ones.set_agenda_item_status(session, item_id, body.status)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/one-on-ones/{meeting_id}/agenda.md", response_model=schemas.AgendaMarkdownOut)
def agenda_markdown(meeting_id: int, session: SessionDep) -> object:
    try:
        return schemas.AgendaMarkdownOut(
            markdown=one_on_ones.build_agenda_markdown(session, meeting_id)
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# --- what next / reports ---


@router.get("/whatnext", response_model=schemas.WhatNextOut)
def what_next(
    session: SessionDep,
    llm: Annotated[LLMClient, Depends(get_llm)],
    use_llm: bool = False,
    limit: int = 10,
) -> object:
    markdown = whatnext.what_next_markdown(session, llm=llm if use_llm else None, limit=limit)
    return schemas.WhatNextOut(markdown=markdown)


@router.get("/reports", response_model=list[schemas.ReportOut])
def list_reports(session: SessionDep) -> object:
    return reports.list_reports(session)


@router.get("/reports/{report_id}", response_model=schemas.ReportDetailOut)
def get_report(report_id: int, session: SessionDep) -> object:
    report = session.get(Report, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"report {report_id} not found")
    return report


# --- jobs ---


@router.get("/jobs", response_model=schemas.JobListOut)
def list_jobs(registry: Annotated[JobRegistry, Depends(get_registry)]) -> object:
    return schemas.JobListOut(jobs=registry.names())


@router.post("/jobs/{name}/run", response_model=schemas.JobRunOut)
def run_job(
    name: str,
    registry: Annotated[JobRegistry, Depends(get_registry)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    config: Annotated[Config, Depends(get_config)],
    llm: Annotated[LLMClient, Depends(get_llm)],
    full: bool = False,
) -> object:
    try:
        return registry.run(name, session_factory, config, llm, params={"full": str(full)})
    except UnknownJobError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/jobs/runs", response_model=list[schemas.JobRunOut])
def list_job_runs(session: SessionDep, limit: int = 50) -> object:
    return list(session.scalars(select(JobRun).order_by(JobRun.started_at.desc()).limit(limit)))
