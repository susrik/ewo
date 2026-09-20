"""Engine/session factory built from the ewo Config."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from ewo.config import Config


def make_engine(config: Config) -> Engine:
    return create_engine(config.database.url)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
