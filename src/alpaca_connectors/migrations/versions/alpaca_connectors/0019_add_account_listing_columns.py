"""add account listing columns

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-26 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0019"
down_revision: Union[str, Sequence[str], None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_NAME = "alpaca_connectors__acct_alpaca"


def upgrade() -> None:
    """Copy the Account's name and active flag onto the account details (ms-markets ADR 0047).

    The registration listing then pages, filters and orders on this table alone instead of
    joining the Markets AccountTable. Both columns are nullable: the release still serving during
    the rollout inserts rows without them, and this revision cannot read the Markets Account
    table to fill them. `alpaca-connectors account backfill-listing-fields` copies them for
    existing rows through the Markets API.
    """
    op.add_column(TABLE_NAME, sa.Column("account_name", sa.String(length=255), nullable=True))
    op.add_column(TABLE_NAME, sa.Column("account_is_active", sa.Boolean(), nullable=True))


def downgrade() -> None:
    """Remove the copied listing columns."""
    op.drop_column(TABLE_NAME, "account_is_active")
    op.drop_column(TABLE_NAME, "account_name")
