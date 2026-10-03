"""SQLAlchemy 2.0 declarative models.

Keep everything dialect-portable: tests run on in-memory SQLite, production
runs on Postgres. Schema changes require an Alembic migration.
"""

from __future__ import annotations

import enum
from datetime import UTC, date, datetime

from sqlalchemy import (
    JSON,
    Column,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class TaskStatus(enum.StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"
    DROPPED = "dropped"


class TaskPriority(enum.StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class TaskSource(enum.StrEnum):
    MANUAL = "manual"
    DISCORD = "discord"
    JIRA = "jira"
    MCP = "mcp"
    NOTES = "notes"


task_tags = Table(
    "task_tags",
    Base.metadata,
    Column("task_id", ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Person(Base):
    """A team member (or the owner, ``is_self``) — not a login account.

    ``notes_dir`` is the person's folder in the notes tree (e.g.
    ``swd/people/robert``); ``aliases`` are other names the notes use for them.
    Both drive attribution when scanning notes for outstanding items.
    """

    __tablename__ = "people"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    jira_account_id: Mapped[str | None] = mapped_column(String(128))
    discord_user_id: Mapped[str | None] = mapped_column(String(64))
    is_self: Mapped[bool] = mapped_column(default=False)
    notes_dir: Mapped[str | None] = mapped_column(String(500))
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    tasks: Mapped[list[Task]] = relationship(back_populates="assignee")


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    description: Mapped[str | None] = mapped_column(Text)

    tasks: Mapped[list[Task]] = relationship(secondary=task_tags, back_populates="tags")


class Task(Base):
    """A coherent, user-facing piece of work (a topic).

    Tasks form an arbitrary-depth tree (single parent, many children) and
    collect ``nuggets`` — work items extracted from the notes tree.
    """

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, native_enum=False, length=20), default=TaskStatus.OPEN
    )
    priority: Mapped[TaskPriority] = mapped_column(
        Enum(TaskPriority, native_enum=False, length=20), default=TaskPriority.NORMAL
    )
    source: Mapped[TaskSource] = mapped_column(
        Enum(TaskSource, native_enum=False, length=20), default=TaskSource.MANUAL
    )
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("people.id", ondelete="SET NULL"))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    start_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    extra: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    assignee: Mapped[Person | None] = relationship(back_populates="tasks")
    parent: Mapped[Task | None] = relationship(remote_side="Task.id", back_populates="children")
    children: Mapped[list[Task]] = relationship(back_populates="parent")
    tags: Mapped[list[Tag]] = relationship(secondary=task_tags, back_populates="tasks")
    external_links: Mapped[list[ExternalLink]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )
    notes: Mapped[list[Note]] = relationship(back_populates="task", cascade="all, delete-orphan")
    nuggets: Mapped[list[Nugget]] = relationship(
        back_populates="task", foreign_keys="Nugget.task_id"
    )


class ExternalLink(Base):
    """Task ↔ external system. One external key can sit on many tasks (e.g.
    several tasks relate to one Jira issue), but only once per task."""

    __tablename__ = "external_links"
    __table_args__ = (
        UniqueConstraint("system", "external_key", "task_id", name="uq_external_links_key_task"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    system: Mapped[str] = mapped_column(String(50))  # e.g. "jira"
    external_key: Mapped[str] = mapped_column(String(200))  # e.g. "PROJ-123"
    url: Mapped[str | None] = mapped_column(String(1000))
    external_status: Mapped[str | None] = mapped_column(String(100))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime)

    task: Mapped[Task] = relationship(back_populates="external_links")


class Note(Base):
    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    person_id: Mapped[int | None] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    task: Mapped[Task | None] = relationship(back_populates="notes")
    person: Mapped[Person | None] = relationship()


class NuggetKind(enum.StrEnum):
    ACTION = "action"
    QUESTION = "question"
    DEADLINE = "deadline"
    RISK = "risk"


class NuggetStatus(enum.StrEnum):
    NEW = "new"
    ATTACHED = "attached"
    DISMISSED = "dismissed"
    ALREADY_DONE = "already_done"


class Nugget(Base):
    """A work item extracted from the notes tree — the review inbox.

    A nugget is a potential piece of work with a citation (``path``:``line``).
    Reviewing attaches it to a task (new or existing), dismisses it, or marks
    it already done. Attached nuggets stay visible on their task and can be
    moved between tasks or detached back to the inbox.

    Nuggets are deduplicated by ``fingerprint``; re-scans only bump
    ``last_seen_at``. Once reviewed (attached/dismissed/already_done) a nugget
    is never re-surfaced. ewo never modifies the source note.

    ``jira_keys`` are Jira issue keys (e.g. ``DBOARD3-1111``) captured from the
    note at extraction time; attaching adds them as task links.
    ``suggested_task_id`` holds the candidate task computed by ``nuggets_match``.
    """

    __tablename__ = "nuggets"

    id: Mapped[int] = mapped_column(primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    path: Mapped[str] = mapped_column(String(1000))
    line: Mapped[int] = mapped_column(Integer)
    summary: Mapped[str] = mapped_column(String(500))
    excerpt: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[NuggetKind] = mapped_column(
        Enum(NuggetKind, native_enum=False, length=20), default=NuggetKind.ACTION
    )
    status: Mapped[NuggetStatus] = mapped_column(
        Enum(NuggetStatus, native_enum=False, length=20), default=NuggetStatus.NEW
    )
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("people.id", ondelete="SET NULL"))
    owner_name: Mapped[str | None] = mapped_column(String(200))
    due_date: Mapped[date | None] = mapped_column(Date)
    jira_keys: Mapped[list[str]] = mapped_column(JSON, default=list)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    suggested_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL")
    )
    job_run_id: Mapped[int | None] = mapped_column(ForeignKey("job_runs.id", ondelete="SET NULL"))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)

    owner: Mapped[Person | None] = relationship()
    task: Mapped[Task | None] = relationship(foreign_keys=[task_id], back_populates="nuggets")
    suggested_task: Mapped[Task | None] = relationship(foreign_keys=[suggested_task_id])


class AgendaItemStatus(enum.StrEnum):
    OPEN = "open"
    DISCUSSED = "discussed"
    DROPPED = "dropped"


class OneOnOne(Base):
    __tablename__ = "one_on_ones"

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"))
    scheduled_for: Mapped[date] = mapped_column(Date)
    summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    person: Mapped[Person] = relationship()
    agenda_items: Mapped[list[AgendaItem]] = relationship(
        back_populates="one_on_one", cascade="all, delete-orphan"
    )


class AgendaItem(Base):
    __tablename__ = "agenda_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    one_on_one_id: Mapped[int] = mapped_column(ForeignKey("one_on_ones.id", ondelete="CASCADE"))
    topic: Mapped[str] = mapped_column(Text)
    status: Mapped[AgendaItemStatus] = mapped_column(
        Enum(AgendaItemStatus, native_enum=False, length=20), default=AgendaItemStatus.OPEN
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    one_on_one: Mapped[OneOnOne] = relationship(back_populates="agenda_items")


class JobRunStatus(enum.StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class JobRun(Base):
    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_name: Mapped[str] = mapped_column(String(100))
    status: Mapped[JobRunStatus] = mapped_column(
        Enum(JobRunStatus, native_enum=False, length=20), default=JobRunStatus.RUNNING
    )
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    result: Mapped[str | None] = mapped_column(String(500))
    error: Mapped[str | None] = mapped_column(Text)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    report_type: Mapped[str] = mapped_column(String(100))
    repo_path: Mapped[str | None] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text)
    job_run_id: Mapped[int | None] = mapped_column(ForeignKey("job_runs.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class OAuthToken(Base):
    __tablename__ = "oauth_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(50), unique=True)  # e.g. "google"
    token_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class UIPreference(Base):
    """A single UI preference, e.g. the saved tasks-page filter state."""

    __tablename__ = "ui_preferences"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
