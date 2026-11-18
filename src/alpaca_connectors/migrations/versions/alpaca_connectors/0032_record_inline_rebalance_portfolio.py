"""record the portfolio an inline rebalance configuration was created with

Revision ID: 0032
Revises: 0031
Create Date: 2026-11-18 20:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0032"
down_revision: Union[str, Sequence[str], None] = "0031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_NAME = "alpaca_connectors__portfolio_rebalance_configuration"
COLUMN = "portfolio_configuration_uid"
# Named from the column by the naming convention.
INDEX = "ix__alpaca_connectors__portfolio_rebalance_configura_cadfb2042b"


def upgrade() -> None:
    """Add the nullable column and its index (issue #42).

    Existing rules are shared rules, which name no portfolio: they stay null. The release before
    this one maps no such column, so it reads and writes the table as before.
    """
    op.add_column(TABLE_NAME, sa.Column(COLUMN, sa.Uuid(), nullable=True))
    op.create_index(op.f(INDEX), TABLE_NAME, [COLUMN], unique=False)


def downgrade() -> None:
    op.drop_index(op.f(INDEX), table_name=TABLE_NAME)
    op.drop_column(TABLE_NAME, COLUMN)
