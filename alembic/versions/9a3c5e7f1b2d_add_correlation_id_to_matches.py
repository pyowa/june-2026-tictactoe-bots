"""add correlation_id to matches

Revision ID: 9a3c5e7f1b2d
Revises: f3af65520232
Create Date: 2026-06-07 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '9a3c5e7f1b2d'
down_revision: Union[str, Sequence[str], None] = 'f3af65520232'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('matches', sa.Column('correlation_id', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('matches', 'correlation_id')
