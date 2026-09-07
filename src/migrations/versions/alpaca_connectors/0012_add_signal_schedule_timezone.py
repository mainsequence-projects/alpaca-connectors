"""add signal schedule timezone

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-06 01:21:19.572070

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: Union[str, Sequence[str], None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    table_name = "alpaca_connectors__etf_signal_job_configuration"
    schedule_constraint = (
        "ck__alpaca_connectors__etf_signal_job_configuration_871f2fa48b"
    )
    op.drop_constraint(schedule_constraint, table_name, type_="check")
    op.add_column(
        table_name,
        sa.Column("schedule_timezone", sa.String(length=64), nullable=True),
    )
    op.execute(
        sa.text(
            f"UPDATE {table_name} SET schedule_timezone = 'UTC' "
            "WHERE schedule_type = 'crontab'"
        )
    )
    op.create_check_constraint(
        op.f(schedule_constraint),
        table_name,
        "(schedule_type = 'interval' AND schedule_every IS NOT NULL "
        "AND schedule_every > 0 AND schedule_period IS NOT NULL "
        "AND schedule_expression IS NULL AND schedule_timezone IS NULL) OR "
        "(schedule_type = 'crontab' AND schedule_every IS NULL "
        "AND schedule_period IS NULL AND schedule_expression IS NOT NULL "
        "AND schedule_timezone IS NOT NULL)",
    )


def downgrade() -> None:
    """Downgrade schema."""
    table_name = "alpaca_connectors__etf_signal_job_configuration"
    schedule_constraint = (
        "ck__alpaca_connectors__etf_signal_job_configuration_871f2fa48b"
    )
    op.drop_constraint(op.f(schedule_constraint), table_name, type_="check")
    op.drop_column(table_name, "schedule_timezone")
    op.create_check_constraint(
        schedule_constraint,
        table_name,
        "(schedule_type = 'interval' AND schedule_every IS NOT NULL "
        "AND schedule_every > 0 AND schedule_period IS NOT NULL "
        "AND schedule_expression IS NULL) OR "
        "(schedule_type = 'crontab' AND schedule_every IS NULL "
        "AND schedule_period IS NULL AND schedule_expression IS NOT NULL)",
    )
