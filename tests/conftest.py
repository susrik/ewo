"""Shared fixtures: in-memory SQLite DB, FakeLLM, FastAPI test client.

No network access anywhere in tests: the LLM is FakeLLM, external HTTP is
respx-mocked, git is faked at the GitRepo seam.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from ewo.config import Config
from ewo.core.llm import FakeLLM
from ewo.db.models import Base
from ewo.server.app import create_app


@pytest.fixture()
def config(tmp_path: object) -> Config:
    return Config.model_validate(
        {
            "database": {"url": "sqlite://"},
            "storage": {"data_dir": str(tmp_path)},
        }
    )


@pytest.fixture()
def engine() -> Iterator[object]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def session_factory(engine: object) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)  # type: ignore[arg-type]


@pytest.fixture()
def session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with session_factory() as sess:
        yield sess


@pytest.fixture()
def fake_llm() -> FakeLLM:
    return FakeLLM(responses=["llm advice here"])


@pytest.fixture()
def client(
    config: Config, session_factory: sessionmaker[Session], fake_llm: FakeLLM
) -> Iterator[TestClient]:
    app = create_app(config=config, llm=fake_llm)
    app.state.session_factory = session_factory  # share the in-memory DB
    with TestClient(app) as test_client:
        yield test_client
