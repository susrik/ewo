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


task_tags = Table(
    "task_tags",
    Base.metadata,
    Column("task_id", ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Person(Base):
    __tablename__ = "people"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    email: Mapped[str | None] = mapped_column(String(320))
    jira_account_id: Mapped[str | None] = mapped_column(String(128))
    discord_user_id: Mapped[str | None] = mapped_column(String(64))
    is_self: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    tasks: Mapped[list[Task]] = relationship(back_populates="assignee")


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)

    tasks: Mapped[list[Task]] = relationship(secondary=task_tags, back_populates="tags")


class Task(Base):
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
    due_date: Mapped[date | None] = mapped_column(Date)
    extra: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    assignee: Mapped[Person | None] = relationship(back_populates="tasks")
    tags: Mapped[list[Tag]] = relationship(secondary=task_tags, back_populates="tasks")
    external_links: Mapped[list[ExternalLink]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )
    notes: Mapped[list[Note]] = relationship(back_populates="task", cascade="all, delete-orphan")


class ExternalLink(Base):
    __tablename__ = "external_links"
    __table_args__ = (UniqueConstraint("system", "external_key"),)

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
