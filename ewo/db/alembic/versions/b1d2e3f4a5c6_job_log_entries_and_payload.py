"""job_log_entries table + job_runs.payload for live job logging.

Revision ID: b1d2e3f4a5c6
Revises: 1d2e3f4a5b6c
Create Date: 2026-10-10 12:00:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'b1d2e3f4a5c6'
down_revision = '1d2e3f4a5b6c'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('job_runs', sa.Column('payload', sa.JSON(), nullable=True))
    op.create_table(
        'job_log_entries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('run_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=10), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('meta', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['run_id'], ['job_runs.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_job_log_entries_run_id', 'job_log_entries', ['run_id'])


def downgrade() -> None:
    op.drop_index('ix_job_log_entries_run_id', table_name='job_log_entries')
    op.drop_table('job_log_entries')
    op.drop_column('job_runs', 'payload')