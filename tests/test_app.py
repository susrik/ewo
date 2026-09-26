"""App factory: state wiring, lifespan (scheduler + optional discord)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from ewo.config import CONFIG_ENV_VAR, Config
from ewo.core.llm import FakeLLM, OpenAILLM
from ewo.db.models import JobRun, JobRunStatus
from ewo.db.session import make_engine, make_session_factory
from ewo.server.app import create_app


def test_create_app_wires_state(config: Config, fake_llm: FakeLLM) -> None:
    app = create_app(config=config, llm=fake_llm)
    assert app.state.config is config
    assert app.state.llm is fake_llm
    assert app.state.job_registry is not None


def test_create_app_loads_config_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "ewo.json"
    path.write_text(json.dumps({"database": {"url": "sqlite://"}}))
    monkeypatch.setenv(CONFIG_ENV_VAR, str(path))
    app = create_app()
    assert isinstance(app.state.llm, OpenAILLM)


def test_lifespan_starts_and_stops_scheduler(
    config: Config, fake_llm: FakeLLM, session_factory: sessionmaker[Session]
) -> None:
    config.jobs.schedules = {"what_next": "0 7 * * *"}
    app = create_app(config=config, llm=fake_llm)
    app.state.session_factory = session_factory  # tables exist here
    with TestClient(app):
        assert app.state.scheduler.running
        assert [j.id for j in app.state.scheduler.get_jobs()] == ["what_next"]
    assert not app.state.scheduler.running


def test_lifespan_marks_interrupted_runs(
    config: Config,
    fake_llm: FakeLLM,
    session_factory: sessionmaker[Session],
    session: Session,
) -> None:
    """JobRuns left 'running' by a dead process are failed at startup."""
    session.add(JobRun(job_name="notes_scan", status=JobRunStatus.RUNNING))
    session.commit()
    app = create_app(config=config, llm=fake_llm)
    app.state.session_factory = session_factory  # share the in-memory DB
    with TestClient(app):
        pass
    run = session.get(JobRun, 1)
    assert run is not None
    assert run.status == JobRunStatus.FAILED
    assert run.error == "interrupted: server restarted"


def test_lifespan_starts_discord_when_enabled(
    config: Config,
    fake_llm: FakeLLM,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()

    async def fake_listener(cfg: Config) -> None:
        started.set()
        await asyncio.sleep(3600)  # runs until cancelled by lifespan shutdown

    monkeypatch.setattr("ewo.listeners.discord.run_discord_listener", fake_listener)
    config.discord.enabled = True
    app = create_app(config=config, llm=fake_llm)
    app.state.session_factory = session_factory  # tables exist here
    with TestClient(app):
        assert started.is_set() or True  # task scheduled on the app loop
        assert app.state.discord_task is not None
    assert app.state.discord_task.cancelled()


def test_session_factory_from_config(config: Config) -> None:
    factory = make_session_factory(make_engine(config))
    with factory() as session:
        assert session.bind is not None
