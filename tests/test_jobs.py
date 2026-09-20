"""Job registry, built-in jobs, scheduler wiring."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

import ewo.jobs.builtin  # noqa: F401 - registers builtins
from ewo.config import Config
from ewo.core import tasks
from ewo.core.llm import FakeLLM
from ewo.db.models import JobRunStatus, Report
from ewo.jobs.registry import JobContext, JobRegistry, UnknownJobError, registry
from ewo.jobs.scheduler import build_scheduler


def test_registry_run_success(
    session_factory: sessionmaker[Session], config: Config, fake_llm: FakeLLM
) -> None:
    reg = JobRegistry()

    @reg.register("hello")
    def hello(context: JobContext) -> str:
        context.add_tokens(5)
        return "hi"

    run = reg.run("hello", session_factory, config, fake_llm)
    assert run.status == JobRunStatus.SUCCESS
    assert run.finished_at is not None
    assert run.tokens_used == 5
    assert reg.names() == ["hello"]


def test_registry_run_failure_recorded(
    session_factory: sessionmaker[Session], config: Config, fake_llm: FakeLLM
) -> None:
    reg = JobRegistry()

    @reg.register("boom")
    def boom(context: JobContext) -> str:
        raise RuntimeError("kapow")

    run = reg.run("boom", session_factory, config, fake_llm)
    assert run.status == JobRunStatus.FAILED
    assert run.error is not None and "kapow" in run.error


def test_registry_unknown_job(
    session_factory: sessionmaker[Session], config: Config, fake_llm: FakeLLM
) -> None:
    with pytest.raises(UnknownJobError):
        JobRegistry().run("nope", session_factory, config, fake_llm)


def test_builtin_jobs_registered() -> None:
    assert {"jira_sync", "daily_report", "what_next"} <= set(registry.names())


def test_what_next_job_uses_llm_when_key_configured(
    session_factory: sessionmaker[Session], config: Config, fake_llm: FakeLLM
) -> None:
    with session_factory() as session:
        tasks.create_task(session, "needs advice")
    config.llm.api_key = "sk-test"
    run = registry.run("what_next", session_factory, config, fake_llm)
    assert run.status == JobRunStatus.SUCCESS
    assert fake_llm.calls  # LLM narrative was requested


def test_what_next_job_skips_llm_without_key(
    session_factory: sessionmaker[Session], config: Config, fake_llm: FakeLLM
) -> None:
    with session_factory() as session:
        tasks.create_task(session, "no advice")
    run = registry.run("what_next", session_factory, config, fake_llm)
    assert run.status == JobRunStatus.SUCCESS
    assert fake_llm.calls == []


def test_jira_sync_job_disabled(
    session_factory: sessionmaker[Session], config: Config, fake_llm: FakeLLM
) -> None:
    run = registry.run("jira_sync", session_factory, config, fake_llm)
    assert run.status == JobRunStatus.SUCCESS  # disabled → no-op success


def test_jira_sync_job_enabled(
    session_factory: sessionmaker[Session],
    config: Config,
    fake_llm: FakeLLM,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "ewo.jobs.builtin.sync_jira",
        lambda session, client, jql: {"created": 2, "updated": 1},
    )
    config.jira.enabled = True
    run = registry.run("jira_sync", session_factory, config, fake_llm)
    assert run.status == JobRunStatus.SUCCESS


def test_daily_report_job_records_report(
    session_factory: sessionmaker[Session], config: Config, fake_llm: FakeLLM
) -> None:
    with session_factory() as session:
        tasks.create_task(session, "some work")

    run = registry.run("daily_report", session_factory, config, fake_llm)
    assert run.status == JobRunStatus.SUCCESS

    with session_factory() as session:
        [report] = session.scalars(select(Report)).all()
        assert report.report_type == "daily"
        assert report.repo_path is None  # reports repo disabled
        assert "some work" in report.body
        assert report.job_run_id == run.id


def test_what_next_job(
    session_factory: sessionmaker[Session], config: Config, fake_llm: FakeLLM
) -> None:
    run = registry.run("what_next", session_factory, config, fake_llm)
    assert run.status == JobRunStatus.SUCCESS
    with session_factory() as session:
        [report] = session.scalars(select(Report)).all()
        assert report.report_type == "what-next"


def test_daily_report_publishes_when_enabled(
    session_factory: sessionmaker[Session],
    config: Config,
    fake_llm: FakeLLM,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    published: list[str] = []

    def fake_publish(cfg: Config, report_type: str, body: str, when: object) -> str:
        published.append(report_type)
        return "ewo/2026-08/x.md"

    monkeypatch.setattr("ewo.jobs.builtin.publish_report", fake_publish)
    config.reports_repo.enabled = True

    run = registry.run("daily_report", session_factory, config, fake_llm)
    assert run.status == JobRunStatus.SUCCESS
    assert published == ["daily"]
    with session_factory() as session:
        [report] = session.scalars(select(Report)).all()
        assert report.repo_path == "ewo/2026-08/x.md"


def test_daily_report_sends_email_when_enabled(
    session_factory: sessionmaker[Session],
    config: Config,
    fake_llm: FakeLLM,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[tuple[str, str]] = []

    class FakeGoogle:
        def __init__(self, session: object, cfg: object) -> None:
            pass

        def send_email(self, to: str, subject: str, body: str) -> None:
            sent.append((to, subject))

    monkeypatch.setattr("ewo.jobs.builtin.HttpGoogleClient", FakeGoogle)
    config.google.enabled = True
    config.google.email_to = "me@x.com"

    run = registry.run("daily_report", session_factory, config, fake_llm)
    assert run.status == JobRunStatus.SUCCESS
    assert sent and sent[0][0] == "me@x.com"


def test_build_scheduler(
    session_factory: sessionmaker[Session], config: Config, fake_llm: FakeLLM
) -> None:
    config.jobs.schedules = {
        "daily_report": "0 7 * * 1-5",
        "jira_sync": "",  # disabled by empty cron
        "not_a_job": "* * * * *",  # unknown, skipped
    }
    scheduler = build_scheduler(config, registry, session_factory, fake_llm)
    job_ids = [job.id for job in scheduler.get_jobs()]
    assert job_ids == ["daily_report"]
