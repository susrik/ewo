"""FastAPI dependencies: config, DB session, LLM, job registry from app state."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session, sessionmaker

from ewo.config import Config
from ewo.core.llm import LLMClient
from ewo.jobs.registry import JobRegistry


def get_config(request: Request) -> Config:
    config: Config = request.app.state.config
    return config


def get_session_factory(request: Request) -> sessionmaker[Session]:
    factory: sessionmaker[Session] = request.app.state.session_factory
    return factory


def get_session(request: Request) -> Iterator[Session]:
    factory: sessionmaker[Session] = request.app.state.session_factory
    with factory() as session:
        yield session


def get_llm(request: Request) -> LLMClient:
    llm: LLMClient = request.app.state.llm
    return llm


def get_registry(request: Request) -> JobRegistry:
    registry: JobRegistry = request.app.state.job_registry
    return registry
