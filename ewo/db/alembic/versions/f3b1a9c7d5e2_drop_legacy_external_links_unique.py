"""drop legacy UNIQUE(system, external_key) from external_links

c7e3a9f2d1b8 swapped the old unnamed UNIQUE(system, external_key) for the
named per-task UNIQUE(system, external_key, task_id). On SQLite an inline
UNIQUE reflects with no name, so the drop was silently skipped and both
constraints stayed — re-introducing "one external key total" and breaking
nugget attach whenever the Jira key already sits on another task.

SQLite cannot drop an unnamed constraint, so external_links is rebuilt there;
elsewhere the legacy constraint (if it survived) is dropped by its discovered
name. Rows are preserved — the per-task constraint is strictly weaker, so
existing data always fits.

Revision ID: f3b1a9c7d5e2
Revises: e8b4d2f1a3c6
Create Date: 2026-09-28 12:00:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'f3b1a9c7d5e2'
down_revision = 'e8b4d2f1a3c6'
branch_labels = None
depends_on = None

_TABLE = 'external_links'
_LEGACY_COLUMNS = {'system', 'external_key'}


def _legacy_unique_name(inspector: sa.engine.reflection.Inspector) -> tuple[bool, str | None]:
    """Whether a UNIQUE covering exactly (system, external_key) exists, and
    its name if it has one (unnamed inline UNIQUE reflects as None on
    SQLite)."""
    for uc in inspector.get_unique_constraints(_TABLE):
        if set(uc['column_names']) == _LEGACY_COLUMNS:
            return True, uc['name']
    for index in inspector.get_indexes(_TABLE):
        if index.get('unique') and set(index['column_names']) == _LEGACY_COLUMNS:
            return True, index['name']
    return False, None


def upgrade() -> None:
    bind = op.get_bind()
    present, name = _legacy_unique_name(sa.inspect(bind))
    if not present:
        return
    if bind.dialect.name == 'sqlite':
        # SQLite cannot drop an unnamed inline UNIQUE; rebuild the table.
        op.execute(
            """
            CREATE TABLE external_links_rebuilt (
                id INTEGER NOT NULL,
                task_id INTEGER NOT NULL,
                system VARCHAR(50) NOT NULL,
                external_key VARCHAR(200) NOT NULL,
                url VARCHAR(1000),
                external_status VARCHAR(100),
                last_synced_at DATETIME,
                PRIMARY KEY (id),
                CONSTRAINT uq_external_links_key_task UNIQUE (system, external_key, task_id),
                FOREIGN KEY(task_id) REFERENCES tasks (id) ON DELETE CASCADE
            )
            """
        )
        op.execute(
            """
            INSERT INTO external_links_rebuilt
                (id, task_id, system, external_key, url, external_status, last_synced_at)
            SELECT id, task_id, system, external_key, url, external_status, last_synced_at
            FROM external_links
            """
        )
        op.execute('DROP TABLE external_links')
        op.execute('ALTER TABLE external_links_rebuilt RENAME TO external_links')
    else:
        with op.batch_alter_table(_TABLE) as batch:
            if name is not None:
                batch.drop_constraint(name, type_='unique')


def downgrade() -> None:
    with op.batch_alter_table(_TABLE) as batch:
        batch.create_unique_constraint(
            'uq_external_links_system_key', ['system', 'external_key']
        )
