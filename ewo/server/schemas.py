"""Pydantic models for ALL JSON bodies in and out of the HTTP API."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from ewo.db.models import (
    AgendaItemStatus,
    JobRunStatus,
    NoteItemKind,
    NoteItemStatus,
    TaskPriority,
    TaskSource,
    TaskStatus,
)


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- people ---


class PersonCreate(BaseModel):
    name: str
    jira_account_id: str | None = None
    discord_user_id: str | None = None
    is_self: bool = False
    notes_dir: str | None = None
    aliases: list[str] = []


class PersonUpdate(BaseModel):
    name: str | None = None
    jira_account_id: str | None = None
    discord_user_id: str | None = None
    is_self: bool | None = None
    notes_dir: str | None = None
    aliases: list[str] | None = None


class PersonOut(ORMModel):
    id: int
    name: str
    jira_account_id: str | None
    discord_user_id: str | None
    is_self: bool
    notes_dir: str | None
    aliases: list[str]


class SeedResultOut(BaseModel):
    created: list[PersonOut]


# --- tasks ---


class TagOut(ORMModel):
    id: int
    name: str


class ExternalLinkOut(ORMModel):
    id: int
    system: str
    external_key: str
    url: str | None
    external_status: str | None
    last_synced_at: datetime | None


class TaskCreate(BaseModel):
    title: str
    description: str | None = None
    priority: TaskPriority = TaskPriority.NORMAL
    source: TaskSource = TaskSource.MANUAL
    assignee_id: int | None = None
    due_date: date | None = None
    tags: list[str] = []


class TaskUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    assignee_id: int | None = None
    due_date: date | None = None
    tags: list[str] | None = None


class TaskOut(ORMModel):
    id: int
    title: str
    description: str | None
    status: TaskStatus
    priority: TaskPriority
    source: TaskSource
    assignee_id: int | None
    assignee: PersonOut | None
    due_date: date | None
    created_at: datetime
    updated_at: datetime
    tags: list[TagOut]
    external_links: list[ExternalLinkOut]


class NoteCreate(BaseModel):
    body: str
    task_id: int | None = None
    person_id: int | None = None


class NoteOut(ORMModel):
    id: int
    body: str
    task_id: int | None
    person_id: int | None
    created_at: datetime


# --- note items (inbox) ---


class NoteItemOut(ORMModel):
    id: int
    path: str
    line: int
    summary: str
    excerpt: str | None
    kind: NoteItemKind
    status: NoteItemStatus
    owner_id: int | None
    owner: PersonOut | None
    owner_name: str | None
    due_date: date | None
    task_id: int | None
    first_seen_at: datetime
    last_seen_at: datetime
    reviewed_at: datetime | None


class NoteItemUpdate(BaseModel):
    summary: str | None = None
    owner_id: int | None = None
    due_date: date | None = None
    kind: NoteItemKind | None = None


class NoteItemAccept(BaseModel):
    title: str | None = None
    priority: TaskPriority = TaskPriority.NORMAL
    assignee_id: int | None = None
    due_date: date | None = None


# --- one-on-ones ---


class OneOnOneCreate(BaseModel):
    person_id: int
    scheduled_for: date


class AgendaItemCreate(BaseModel):
    topic: str


class AgendaItemUpdate(BaseModel):
    status: AgendaItemStatus


class AgendaItemOut(ORMModel):
    id: int
    topic: str
    status: AgendaItemStatus


class OneOnOneOut(ORMModel):
    id: int
    person_id: int
    scheduled_for: date
    summary: str | None
    agenda_items: list[AgendaItemOut]


class AgendaMarkdownOut(BaseModel):
    markdown: str


# --- jobs / reports / whatnext ---


class JobRunOut(ORMModel):
    id: int
    job_name: str
    status: JobRunStatus
    started_at: datetime
    finished_at: datetime | None
    result: str | None
    error: str | None
    tokens_used: int


class JobListOut(BaseModel):
    jobs: list[str]


class ReportOut(ORMModel):
    id: int
    report_type: str
    repo_path: str | None
    created_at: datetime


class ReportDetailOut(ReportOut):
    body: str


class WhatNextOut(BaseModel):
    markdown: str


class MessageOut(BaseModel):
    detail: str
