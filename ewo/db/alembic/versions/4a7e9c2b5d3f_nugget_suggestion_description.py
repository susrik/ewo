"""add nugget_suggestions.description.

Revision ID: 4a7e9c2b5d3f
Revises: 7f2c9d1e4a8b
Create Date: 2026-10-11 13:00:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '4a7e9c2b5d3f'
down_revision = '7f2c9d1e4a8b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('nugget_suggestions', sa.Column('description', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('nugget_suggestions', 'description')