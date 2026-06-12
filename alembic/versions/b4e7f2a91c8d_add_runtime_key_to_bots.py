"""add runtime_key to bots

Revision ID: b4e7f2a91c8d
Revises: 9a3c5e7f1b2d
Create Date: 2026-06-11 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b4e7f2a91c8d"
down_revision: Union[str, Sequence[str], None] = "9a3c5e7f1b2d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add with server_default so existing rows are populated immediately.
    op.add_column(
        "bots",
        sa.Column(
            "runtime_key",
            sa.String(),
            nullable=False,
            server_default="'python-3.14'",
        ),
    )
    # Backfill: bots with a dotted python_version (e.g. "3.12") get the
    # correct key; bots with bare "3" keep the "python-3.14" default.
    op.execute(
        "UPDATE bots SET runtime_key = 'python-' || python_version "
        "WHERE python_version LIKE '%.%'"
    )
    op.alter_column("bots", "runtime_key", server_default=None)


def downgrade() -> None:
    op.drop_column("bots", "runtime_key")
