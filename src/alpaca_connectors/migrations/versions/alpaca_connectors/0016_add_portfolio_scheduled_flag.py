"""add portfolio scheduled flag

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-05 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0016"
down_revision: Union[str, Sequence[str], None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_NAME = "alpaca_connectors__etf_portfolio_configuration"


def upgrade() -> None:
    """Include every existing portfolio configuration in the portfolio Job's scheduled run.

    ADR 0013. The server default stays: the release still serving during the rollout inserts rows
    without this column. Signal configurations need no column; they reuse ``enabled``.
    """
    op.add_column(
        TABLE_NAME,
        sa.Column("scheduled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    """Remove the scheduled flag."""
    op.drop_column(TABLE_NAME, "scheduled")
