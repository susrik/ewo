"""people are team members; note_items inbox

- people: drop email (team members are not platform users); add notes_dir and aliases
- tasks.source: new value "notes"
- job_runs.result: short result text returned by the job
- note_items: outstanding items extracted from the notes tree

Revision ID: a1c3e5f7b9d2
Revises: 6882b55f0fdb
Create Date: 2026-09-21 10:00:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'a1c3e5f7b9d2'
down_revision = '6882b55f0fdb'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('people') as batch:
        batch.drop_column('email')
        batch.add_column(sa.Column('notes_dir', sa.String(length=500), nullable=True))
        batch.add_column(sa.Column('aliases', sa.JSON(), nullable=False, server_default='[]'))

    op.add_column('job_runs', sa.Column('result', sa.String(length=500), nullable=True))

    op.create_table('note_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('fingerprint', sa.String(length=64), nullable=False),
    sa.Column('path', sa.String(length=1000), nullable=False),
    sa.Column('line', sa.Integer(), nullable=False),
    sa.Column('summary', sa.String(length=500), nullable=False),
    sa.Column('excerpt', sa.Text(), nullable=True),
    sa.Column('kind', sa.Enum('ACTION', 'QUESTION', 'DEADLINE', 'RISK', name='noteitemkind', native_enum=False, length=20), nullable=False),
    sa.Column('status', sa.Enum('NEW', 'ACCEPTED', 'DISMISSED', 'ALREADY_DONE', name='noteitemstatus', native_enum=False, length=20), nullable=False),
    sa.Column('owner_id', sa.Integer(), nullable=True),
    sa.Column('owner_name', sa.String(length=200), nullable=True),
    sa.Column('due_date', sa.Date(), nullable=True),
    sa.Column('task_id', sa.Integer(), nullable=True),
    sa.Column('job_run_id', sa.Integer(), nullable=True),
    sa.Column('first_seen_at', sa.DateTime(), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(), nullable=False),
    sa.Column('reviewed_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['job_run_id'], ['job_runs.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['owner_id'], ['people.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('fingerprint')
    )


def downgrade() -> None:
    op.drop_table('note_items')
    op.drop_column('job_runs', 'result')
    with op.batch_alter_table('people') as batch:
        batch.drop_column('aliases')
        batch.drop_column('notes_dir')
        batch.add_column(sa.Column('email', sa.String(length=320), nullable=True))
