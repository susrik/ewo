"""Job registry, built-in jobs, scheduler wiring."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

import ewo.jobs.builtin  # noqa: F401 - registers builtins
from ewo.config import Config
from ewo.core import tasks
from ewo.core.llm import FakeLLM
from ewo.db.models import JobRun, JobRunStatus, Report
from ewo.jobs.registry import (
    JobContext,
    JobRegistry,
    UnknownJobError,
    mark_interrupted_runs,
    registry,
)
from ewo.jobs.scheduler import build_scheduler


def _wait_for_run(session_factory: sessionmaker[Session], run_id: int) -> JobRun:
    deadline = time.time() + 5
    while time.time() < deadline:
        with session_factory() as session:
            run = session.get(JobRun, run_id)
            assert run is not None
            if run.status != JobRunStatus.RUNNING:
                return run
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} still running after 5s")


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


def test_registry_run_async(
    session_factory: sessionmaker[Session], config: Config, fake_llm: FakeLLM
) -> None:
    reg = JobRegistry()

    @reg.register("hello")
    def hello(context: JobContext) -> str:
        context.add_tokens(3)
        return "hi async"

    run_id = reg.run_async("hello", session_factory, config, fake_llm)
    run = _wait_for_run(session_factory, run_id)
    assert run.status == JobRunStatus.SUCCESS
    assert run.result == "hi async" and run.tokens_used == 3


def test_registry_run_async_unknown(
    session_factory: sessionmaker[Session], config: Config, fake_llm: FakeLLM
) -> None:
    with pytest.raises(UnknownJobError):
        JobRegistry().run_async("nope", session_factory, config, fake_llm)


def test_mark_interrupted_runs(session: Session) -> None:
    session.add(JobRun(job_name="notes_scan", status=JobRunStatus.RUNNING))
    session.add(JobRun(job_name="what_next", status=JobRunStatus.SUCCESS))
    session.commit()

    assert mark_interrupted_runs(session) == 1
    stale = session.scalars(select(JobRun).where(JobRun.job_name == "notes_scan")).one()
    assert stale.status == JobRunStatus.FAILED
    assert stale.error == "interrupted: server restarted"
    assert stale.finished_at is not None
    done = session.scalars(select(JobRun).where(JobRun.job_name == "what_next")).one()
    assert done.status == JobRunStatus.SUCCESS


def test_builtin_jobs_registered() -> None:
    assert {
        "jira_sync",
        "daily_report",
        "what_next",
        "notes_scan",
        "nuggets_match",
        "housekeeping",
    } <= set(registry.names())


def test_housekeeping_job(
    session_factory: sessionmaker[Session], config: Config, fake_llm: FakeLLM
) -> None:
    with session_factory() as session:
        root = tasks.create_task(session, "root", tags=["root-tag"])
        child = tasks.create_task(session, "child", parent_id=root.id)
        tasks.update_task(session, root.id, tags=["root-tag", "late"])  # drift after create
        child_id = child.id

    run = registry.run("housekeeping", session_factory, config, fake_llm)

    assert run.status == JobRunStatus.SUCCESS
    assert run.result == "checked=2 fixed=1"
    with session_factory() as session:
        assert sorted(t.name for t in tasks.get_task(session, child_id).tags) == [
            "late",
            "root-tag",
        ]


def test_housekeeping_job_nothing_to_fix(
    session_factory: sessionmaker[Session], config: Config, fake_llm: FakeLLM
) -> None:
    with session_factory() as session:
        root = tasks.create_task(session, "root", tags=["root-tag"])
        tasks.create_task(session, "child", parent_id=root.id)  # inherits at create

    run = registry.run("housekeeping", session_factory, config, fake_llm)

    assert run.status == JobRunStatus.SUCCESS
    assert run.result == "checked=2 fixed=0"


def test_nuggets_match_job(
    session_factory: sessionmaker[Session], config: Config, fake_llm: FakeLLM
) -> None:
    run = registry.run("nuggets_match", session_factory, config, fake_llm)
    assert run.status == JobRunStatus.FAILED and "llm.api_key" in str(run.error)

    config.llm.api_key = "sk"
    run = registry.run("nuggets_match", session_factory, config, fake_llm)
    assert run.status == JobRunStatus.SUCCESS
    assert run.result is not None and run.result.startswith("reviewed=0")


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
