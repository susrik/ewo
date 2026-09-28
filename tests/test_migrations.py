"""Alembic migration regression tests.

c7e3a9f2d1b8 failed to drop the legacy unnamed UNIQUE(system, external_key)
on external_links under SQLite (unnamed inline UNIQUE constraints reflect
with name=None, so the drop was silently skipped). Databases migrated on
SQLite kept the global-unique constraint, and attaching a nugget whose Jira
key already sat on another task died with an IntegrityError. f3b1a9c7d5e2
repairs that; these tests pin the behaviour.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config as AlembicConfig

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PRE_REPAIR_STAMP = "e8b4d2f1a3c6"
_LINKS_DDL = """
CREATE TABLE external_links (
    id INTEGER NOT NULL,
    task_id INTEGER NOT NULL,
    system VARCHAR(50) NOT NULL,
    external_key VARCHAR(200) NOT NULL,
    url VARCHAR(1000),
    external_status VARCHAR(100),
    last_synced_at DATETIME,
    PRIMARY KEY (id),
    CONSTRAINT uq_external_links_key_task UNIQUE (system, external_key, task_id),
    FOREIGN KEY(task_id) REFERENCES tasks (id) ON DELETE CASCADE,
    UNIQUE (system, external_key)
)
"""


def _unique_column_sets(url: str) -> set[tuple[str, ...]]:
    inspector = sa.inspect(sa.create_engine(url))
    return {tuple(uc["column_names"]) for uc in inspector.get_unique_constraints("external_links")}


def _upgrade_head(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    config_file = tmp_path / "ewo.json"
    config_file.write_text(
        json.dumps({"database": {"url": url}, "storage": {"data_dir": str(tmp_path)}})
    )
    monkeypatch.setenv("EWO_CONFIG_FILENAME", str(config_file))
    cfg = AlembicConfig(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "ewo" / "db" / "alembic"))
    command.upgrade(cfg, "head")


def test_full_upgrade_leaves_only_per_task_unique(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh database migrated to head must not carry the legacy constraint."""
    url = f"sqlite:///{tmp_path / 'fresh.db'}"
    _upgrade_head(tmp_path, monkeypatch, url)
    assert _unique_column_sets(url) == {("system", "external_key", "task_id")}


def test_repair_drops_legacy_unique_and_keeps_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A database stuck in the broken post-c7e3a9f2d1b8 state (both
    constraints present) is repaired without losing rows."""
    url = f"sqlite:///{tmp_path / 'broken.db'}"
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE tasks (id INTEGER NOT NULL, PRIMARY KEY (id))"))
        conn.execute(sa.text(_LINKS_DDL))
        conn.execute(sa.text("INSERT INTO tasks (id) VALUES (1), (2)"))
        conn.execute(
            sa.text(
                "INSERT INTO external_links (id, task_id, system, external_key)"
                " VALUES (1, 1, 'jira', 'PROJ-1')"
            )
        )
        conn.execute(sa.text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        conn.execute(sa.text(f"INSERT INTO alembic_version VALUES ('{_PRE_REPAIR_STAMP}')"))
    engine.dispose()

    _upgrade_head(tmp_path, monkeypatch, url)

    assert _unique_column_sets(url) == {("system", "external_key", "task_id")}
    engine = sa.create_engine(url)
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text("SELECT task_id, system, external_key FROM external_links")
        ).all()
    assert rows == [(1, "jira", "PROJ-1")]
    # The scenario that used to fail: the same Jira key on a second task.
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO external_links (id, task_id, system, external_key)"
                " VALUES (2, 2, 'jira', 'PROJ-1')"
            )
        )
    engine.dispose()
