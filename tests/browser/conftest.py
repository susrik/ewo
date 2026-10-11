"""Browser (Playwright) test fixtures.

These tests drive the real htmx GUI in a headless Chromium against a live
server, exercising behaviour the ``TestClient`` suite can't observe: htmx
fragment swaps, the focus-scroll and SSE job-log scripts, and checkbox/bulk
selection interactions.

They are marked ``browser`` and excluded from the default ``pytest`` run (see
``addopts`` in ``pyproject.toml``); run them explicitly with
``pytest -m browser --no-cov``. They need Playwright + Chromium installed
(``pip install -e ".[test-browser]"`` then ``playwright install chromium``)
and, unlike the unit suite, are allowed network access so htmx/Tailwind/daisyUI
load from their CDNs.

Playwright and uvicorn are imported lazily so collection of this directory
during a normal ``pytest`` run never requires them to be installed.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from ewo.config import Config
from ewo.core.llm import FakeLLM
from ewo.db.models import Base
from ewo.server.app import create_app

if TYPE_CHECKING:
    from playwright.sync_api import Browser, Page


@dataclass
class LiveServer:
    """The running server plus the SQLAlchemy handles tests seed/query through."""

    base_url: str
    engine: object
    session_factory: sessionmaker[Session]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def live_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[LiveServer]:
    """A real uvicorn server over a throwaway SQLite file, shared by all browser tests.

    The database is a file under pytest's temp dir (``tmp_path_factory``), created
    fresh here and deleted when the run ends. It never touches any real/production
    database; ``_reset_db`` drops and recreates the schema before every test so
    seeding can't leak between tests.

    A file (rather than ``sqlite://:memory:`` with a single ``StaticPool``
    connection) is deliberate: uvicorn runs in this process's thread while
    FastAPI's sync endpoints run in a threadpool and jobs run in daemon threads,
    all touching the DB concurrently with the tests' own seeding sessions. A
    single shared in-memory connection is not thread-safe. ``NullPool`` gives
    every session its own connection over the one file, with a busy timeout for
    the rare write clash — the same scheme ``tests/conftest.py`` uses.

    Config mirrors ``tests/conftest.py`` (notes disabled, FakeLLM), plus an LLM
    API key so the AI-affordances render without needing real network access.
    """
    tmp = tmp_path_factory.mktemp("browser-server")
    db_path = tmp / "browser.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    config = Config.model_validate(
        {
            "database": {"url": f"sqlite:///{db_path}"},
            "storage": {"data_dir": str(tmp)},
            "llm": {"api_key": "sk-test"},
        }
    )
    app = create_app(config=config, llm=FakeLLM(responses=["fake response"]))
    app.state.session_factory = factory

    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover - only on a mis-set-up dev env
        pytest.skip(f"uvicorn not installed ({exc}); run 'pip install uvicorn' in the dev venv")

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 20
    while True:
        try:
            httpx.get(f"{base_url}/", timeout=1)
            break
        except httpx.HTTPError:
            if time.time() >= deadline:
                raise RuntimeError("live server did not start within 20s") from None
            time.sleep(0.1)

    yield LiveServer(base_url=base_url, engine=engine, session_factory=factory)

    server.should_exit = True
    thread.join(timeout=10)
    engine.dispose()


@pytest.fixture(autouse=True)
def _reset_db(live_server: LiveServer) -> Iterator[None]:
    """Give every test a clean slate (the server is session-scoped)."""
    Base.metadata.drop_all(live_server.engine)
    Base.metadata.create_all(live_server.engine)
    yield


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - only on a mis-set-up dev env
        pytest.skip(
            f"playwright not installed ({exc}); run 'pip install -e \".[test-browser]\"' "
            "and 'playwright install chromium'"
        )
    pw = sync_playwright().start()
    chromium = pw.chromium.launch(headless=True)
    yield chromium
    chromium.close()
    pw.stop()


@pytest.fixture()
def page(browser: Browser) -> Iterator[Page]:
    context = browser.new_context()
    pg = context.new_page()
    pg.set_default_timeout(10_000)
    yield pg
    context.close()
