"""nuggets rename, task hierarchy + dates, per-task external links

- note_items → nuggets (table rename); status value accepted → attached
- nuggets: jira_keys (JSON), suggested_task_id (the nuggets_match suggestion)
- tasks: parent_id (arbitrary-depth tree, single parent), start_date,
  completed_at
- external_links: the old unnamed UNIQUE(system, external_key) becomes the
  named UNIQUE(system, external_key, task_id) so one external issue (e.g. a
  Jira ticket) can be linked from many tasks, but only once per task. The old
  constraint is discovered by its columns (its auto-generated name differs
  between Postgres and SQLite).

Revision ID: c7e3a9f2d1b8
Revises: a1c3e5f7b9d2
Create Date: 2026-09-24 16:00:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'c7e3a9f2d1b8'
down_revision = 'a1c3e5f7b9d2'
branch_labels = None
depends_on = None

_LINKS_UNIQUE = 'uq_external_links_key_task'


def _unique_constraint_name(table: str, columns: set[str]) -> str | None:
    """Name of the unique constraint/index covering exactly *columns*."""
    inspector = sa.inspect(op.get_bind())
    for uc in inspector.get_unique_constraints(table):
        if set(uc['column_names']) == columns:
            return uc['name']
    return None


def upgrade() -> None:
    op.rename_table('note_items', 'nuggets')
    with op.batch_alter_table('nuggets') as batch:
        batch.add_column(sa.Column('jira_keys', sa.JSON(), nullable=False, server_default='[]'))
        batch.add_column(sa.Column('suggested_task_id', sa.Integer(), nullable=True))
        batch.create_foreign_key(
            'fk_nuggets_suggested_task_id', 'tasks', ['suggested_task_id'], ['id'],
            ondelete='SET NULL',
        )
    # enum values are persisted as member names; cover both casings defensively
    op.execute("UPDATE nuggets SET status='ATTACHED' WHERE status IN ('ACCEPTED', 'accepted')")

    with op.batch_alter_table('tasks') as batch:
        batch.add_column(sa.Column('parent_id', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('start_date', sa.Date(), nullable=True))
        batch.add_column(sa.Column('completed_at', sa.DateTime(), nullable=True))
        batch.create_foreign_key(
            'fk_tasks_parent_id', 'tasks', ['parent_id'], ['id'], ondelete='SET NULL'
        )

    old_name = _unique_constraint_name('external_links', {'system', 'external_key'})
    with op.batch_alter_table('external_links') as batch:
        if old_name is not None:
            batch.drop_constraint(old_name, type_='unique')
        batch.create_unique_constraint(
            _LINKS_UNIQUE, ['system', 'external_key', 'task_id']
        )


def downgrade() -> None:
    with op.batch_alter_table('external_links') as batch:
        batch.drop_constraint(_LINKS_UNIQUE, type_='unique')
        batch.create_unique_constraint('uq_external_links_system_key', ['system', 'external_key'])

    with op.batch_alter_table('tasks') as batch:
        batch.drop_constraint('fk_tasks_parent_id', type_='foreignkey')
        batch.drop_column('completed_at')
        batch.drop_column('start_date')
        batch.drop_column('parent_id')

    op.execute("UPDATE nuggets SET status='ACCEPTED' WHERE status IN ('ATTACHED', 'attached')")
    with op.batch_alter_table('nuggets') as batch:
        batch.drop_constraint('fk_nuggets_suggested_task_id', type_='foreignkey')
        batch.drop_column('suggested_task_id')
        batch.drop_column('jira_keys')
    op.rename_table('nuggets', 'note_items')
