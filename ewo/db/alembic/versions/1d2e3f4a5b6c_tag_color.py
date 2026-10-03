"""tag color: labels may carry a user-assigned color (hex #RRGGBB) for pillboxes.

Revision ID: 1d2e3f4a5b6c
Revises: 9c4b6a2e8d1f
Create Date: 2026-10-03 09:00:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '1d2e3f4a5b6c'
down_revision = '9c4b6a2e8d1f'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('tags') as batch:
        batch.add_column(sa.Column('color', sa.String(length=7), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('tags') as batch:
        batch.drop_column('color')