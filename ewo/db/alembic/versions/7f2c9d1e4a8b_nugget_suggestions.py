"""nugget_suggestions: persist AI-proposed new tasks from the inbox_suggest job.

Revision ID: 7f2c9d1e4a8b
Revises: b1d2e3f4a5c6
Create Date: 2026-10-11 12:00:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '7f2c9d1e4a8b'
down_revision = 'b1d2e3f4a5c6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'nugget_suggestions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=500), nullable=False),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('nugget_ids', sa.JSON(), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('nugget_suggestions')