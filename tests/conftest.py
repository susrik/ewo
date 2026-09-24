"""Shared fixtures: in-memory SQLite DB, FakeLLM, FastAPI test client.

No network access anywhere in tests: the LLM is FakeLLM, external HTTP is
respx-mocked, git is faked at the GitRepo seam.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

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
def notes_root(tmp_path: Path) -> Path:
    """A small notes tree mirroring the real layout (people folders, AGENTS.md chain)."""
    root = tmp_path / "notes"
    (root / "swd" / "people" / "james_l").mkdir(parents=True)
    (root / "swd" / "people" / "james_s").mkdir(parents=True)
    (root / "swd" / "people" / "former" / "bjarke").mkdir(parents=True)
    (root / "eurohpc").mkdir()
    (root / ".obsidian").mkdir()
    (root / "AGENTS.md").write_text("# Root context\nErik is the manager.\n")
    (root / "swd" / "people" / "AGENTS.md").write_text("James L and James S differ.\n")
    (root / "swd" / "people" / "james_l" / "AGENTS.md").write_text("James L: dashboards.\n")
    (root / "swd" / "people" / "james_l" / "2026-09-14-james-121.md").write_text(
        "# James 1:1\n\n- fix the IX SLA report\n- Erik: send the Jira ticket\n"
    )
    (root / "swd" / "people" / "james_l" / "old.md").write_text(
        "# James\n\n## 2026-09-01\n\n- recent item\n\n## 2025-01-01\n\n- ancient item\n"
    )
    (root / "swd" / "people" / "former" / "bjarke" / "old.md").write_text("## 2026-09-01\n- x\n")
    (root / "eurohpc" / "2026-09-03-oam.md").write_text(
        "---\ndate: 2026-09-03\n---\n# OAM\n\n- ACSA acceptance testing not confirmed\n"
    )
    (root / "eurohpc" / "ignored.md").write_text("- do not read\n")
    (root / ".obsidian" / "hidden.md").write_text("- hidden\n")
    (root / "TODO.md").write_text("- glitchtip db\n")
    return root


@pytest.fixture()
def client(
    config: Config, session_factory: sessionmaker[Session], fake_llm: FakeLLM
) -> Iterator[TestClient]:
    app = create_app(config=config, llm=fake_llm)
    app.state.session_factory = session_factory  # share the in-memory DB
    with TestClient(app) as test_client:
        yield test_client
