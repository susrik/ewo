"""FastAPI app factory. Lifespan starts the scheduler and enabled listeners."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

from fastapi import FastAPI

from ewo.config import Config, load_config
from ewo.core.llm import LLMClient, OpenAILLM
from ewo.db.session import make_engine, make_session_factory
from ewo.jobs import builtin  # noqa: F401  (registers built-in jobs)
from ewo.jobs.registry import mark_interrupted_runs, registry
from ewo.jobs.scheduler import build_scheduler
from ewo.server.api import router as api_router
from ewo.server.gui import router as gui_router


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    config: Config = app.state.config
    with app.state.session_factory() as session:
        mark_interrupted_runs(session)
    scheduler = build_scheduler(config, registry, app.state.session_factory, app.state.llm)
    scheduler.start()
    app.state.scheduler = scheduler

    discord_task: asyncio.Task[None] | None = None
    if config.discord.enabled:
        from ewo.listeners.discord import run_discord_listener

        discord_task = asyncio.create_task(run_discord_listener(config))
        app.state.discord_task = discord_task

    yield

    scheduler.shutdown(wait=False)
    if discord_task is not None:
        discord_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await discord_task


def create_app(config: Config | None = None, llm: LLMClient | None = None) -> FastAPI:
    resolved = config or load_config()
    app = FastAPI(title="ewo", lifespan=_lifespan)
    app.state.config = resolved
    app.state.session_factory = make_session_factory(make_engine(resolved))
    app.state.llm = llm or OpenAILLM(resolved.llm)
    app.state.job_registry = registry
    app.include_router(api_router)
    app.include_router(gui_router)
    return app
