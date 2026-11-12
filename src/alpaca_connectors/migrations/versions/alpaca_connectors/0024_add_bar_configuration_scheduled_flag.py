"""add bar configuration scheduled flag

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-12 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0024"
down_revision: Union[str, Sequence[str], None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_NAME = "alpaca_connectors__bars_configuration"


def upgrade() -> None:
    """Include every existing bar configuration in the daily bars update.

    ADR 0021. An unscheduled configuration still runs on demand while enabled. The server default
    stays: the release still serving during the rollout inserts rows without this column.
    """
    op.add_column(
        TABLE_NAME,
        sa.Column("scheduled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    """Remove the scheduled flag."""
    op.drop_column(TABLE_NAME, "scheduled")
