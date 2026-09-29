"""tag description: labels are tags with an optional human-readable description.

Revision ID: e8b4d2f1a3c6
Revises: c7e3a9f2d1b8
Create Date: 2026-09-27 10:00:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'e8b4d2f1a3c6'
down_revision = 'c7e3a9f2d1b8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('tags') as batch:
        batch.add_column(sa.Column('description', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('tags') as batch:
        batch.drop_column('description')
