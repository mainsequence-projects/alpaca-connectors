"""allow the immediate_signal rebalance strategy

Revision ID: 0031
Revises: 0030
Create Date: 2026-11-18 18:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0031"
down_revision: Union[str, Sequence[str], None] = "0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_NAME = "alpaca_connectors__portfolio_rebalance_configuration"
# Named from `ck_portfolio_rebalance_configuration_strategy` by the naming convention (revision
# 0014 created it); the name does not depend on the expression.
STRATEGY_CHECK = "ck__alpaca_connectors__portfolio_rebalance_configura_d2697beab2"


def upgrade() -> None:
    """Accept immediate_signal beside calendar_event_signal (issue #41).

    It only widens what a row may hold: the release before this one writes calendar_event_signal
    only, which still passes.
    """
    op.drop_constraint(op.f(STRATEGY_CHECK), TABLE_NAME, type_="check")
    op.create_check_constraint(
        op.f(STRATEGY_CHECK),
        TABLE_NAME,
        "strategy IN ('calendar_event_signal', 'immediate_signal')",
    )


def downgrade() -> None:
    """Accept calendar_event_signal only; PostgreSQL refuses while an immediate_signal row exists."""
    op.drop_constraint(op.f(STRATEGY_CHECK), TABLE_NAME, type_="check")
    op.create_check_constraint(
        op.f(STRATEGY_CHECK),
        TABLE_NAME,
        "strategy = 'calendar_event_signal'",
    )
