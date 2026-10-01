"""ui_preferences: key/value store for UI state (e.g. saved filter state).

Revision ID: 9c4b6a2e8d1f
Revises: f3b1a9c7d5e2
Create Date: 2026-10-01 12:00:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '9c4b6a2e8d1f'
down_revision = 'f3b1a9c7d5e2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'ui_preferences',
        sa.Column('key', sa.String(length=100), nullable=False),
        sa.Column('value', sa.JSON(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('key'),
    )


def downgrade() -> None:
    op.drop_table('ui_preferences')