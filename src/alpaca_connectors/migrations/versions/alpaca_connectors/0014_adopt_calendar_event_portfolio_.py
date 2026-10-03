"""adopt calendar event portfolio rebalancing

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-08 11:01:37.482319

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: Union[str, Sequence[str], None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    portfolio_table = "alpaca_connectors__etf_portfolio_configuration"
    rebalance_table = "alpaca_connectors__portfolio_rebalance_configuration"

    op.add_column(
        portfolio_table,
        sa.Column(
            "valuation_maximum_staleness_seconds",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("86400"),
        ),
    )
    op.create_check_constraint(
        op.f("ck__alpaca_connectors__etf_portfolio_configuration_3c4a73aa27"),
        portfolio_table,
        "valuation_maximum_staleness_seconds > 0",
    )
    op.drop_constraint(
        op.f("ck__alpaca_connectors__etf_portfolio_configuration_0251d16f17"),
        portfolio_table,
        type_="check",
    )
    op.drop_column(portfolio_table, "forward_fill_to_now")
    op.drop_column(portfolio_table, "portfolio_prices_frequency")
    op.alter_column(
        portfolio_table,
        "valuation_maximum_staleness_seconds",
        server_default=None,
    )

    op.drop_constraint(
        op.f("ck__alpaca_connectors__portfolio_rebalance_configura_d2697beab2"),
        rebalance_table,
        type_="check",
    )
    for column in (
        sa.Column(
            "calendar_identifier",
            sa.String(length=255),
            nullable=False,
            server_default=sa.text("'NYSE'"),
        ),
        sa.Column(
            "session_label",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'regular'"),
        ),
        sa.Column(
            "rebalance_event",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'market_close'"),
        ),
        sa.Column(
            "event_offset_seconds",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "rebalance_cadence",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'every_session'"),
        ),
        sa.Column(
            "rebalance_weekday",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    ):
        op.add_column(rebalance_table, column)
    op.execute(
        sa.text(
            "UPDATE alpaca_connectors__portfolio_rebalance_configuration "
            "SET strategy = 'calendar_event_signal'"
        )
    )
    op.create_check_constraint(
        op.f("ck__alpaca_connectors__portfolio_rebalance_configura_d2697beab2"),
        rebalance_table,
        "strategy = 'calendar_event_signal'",
    )
    op.create_check_constraint(
        op.f("ck__alpaca_connectors__portfolio_rebalance_configura_46d859cb83"),
        rebalance_table,
        "length(trim(calendar_identifier)) > 0",
    )
    op.create_check_constraint(
        op.f("ck__alpaca_connectors__portfolio_rebalance_configura_60836b1685"),
        rebalance_table,
        "length(trim(session_label)) > 0",
    )
    op.create_check_constraint(
        op.f("ck__alpaca_connectors__portfolio_rebalance_configura_fe7c42cf0a"),
        rebalance_table,
        "rebalance_event IN ('market_open', 'market_close')",
    )
    op.create_check_constraint(
        op.f("ck__alpaca_connectors__portfolio_rebalance_configura_6461f045c0"),
        rebalance_table,
        "rebalance_cadence IN ('every_session', 'weekly')",
    )
    op.create_check_constraint(
        op.f("ck__alpaca_connectors__portfolio_rebalance_configura_94c50b6360"),
        rebalance_table,
        "rebalance_weekday >= 0 AND rebalance_weekday <= 6",
    )
    op.create_index(
        op.f("ix__alpaca_connectors__portfolio_rebalance_configura_812d9fd482"),
        rebalance_table,
        ["calendar_identifier"],
        unique=False,
    )
    for column_name in (
        "calendar_identifier",
        "session_label",
        "rebalance_event",
        "event_offset_seconds",
        "rebalance_cadence",
        "rebalance_weekday",
    ):
        op.alter_column(rebalance_table, column_name, server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    portfolio_table = "alpaca_connectors__etf_portfolio_configuration"
    rebalance_table = "alpaca_connectors__portfolio_rebalance_configuration"

    op.drop_index(
        op.f("ix__alpaca_connectors__portfolio_rebalance_configura_812d9fd482"),
        table_name=rebalance_table,
    )
    for constraint_name in (
        "ck__alpaca_connectors__portfolio_rebalance_configura_94c50b6360",
        "ck__alpaca_connectors__portfolio_rebalance_configura_6461f045c0",
        "ck__alpaca_connectors__portfolio_rebalance_configura_fe7c42cf0a",
        "ck__alpaca_connectors__portfolio_rebalance_configura_60836b1685",
        "ck__alpaca_connectors__portfolio_rebalance_configura_46d859cb83",
        "ck__alpaca_connectors__portfolio_rebalance_configura_d2697beab2",
    ):
        op.drop_constraint(op.f(constraint_name), rebalance_table, type_="check")
    op.execute(
        sa.text(
            "UPDATE alpaca_connectors__portfolio_rebalance_configuration "
            "SET strategy = 'immediate_signal'"
        )
    )
    for column_name in (
        "rebalance_weekday",
        "rebalance_cadence",
        "event_offset_seconds",
        "rebalance_event",
        "session_label",
        "calendar_identifier",
    ):
        op.drop_column(rebalance_table, column_name)
    op.create_check_constraint(
        op.f("ck__alpaca_connectors__portfolio_rebalance_configura_d2697beab2"),
        rebalance_table,
        "strategy = 'immediate_signal'",
    )

    op.add_column(
        portfolio_table,
        sa.Column(
            "portfolio_prices_frequency",
            sa.VARCHAR(length=16),
            nullable=True,
            server_default=sa.text("'1d'"),
        ),
    )
    op.add_column(
        portfolio_table,
        sa.Column(
            "forward_fill_to_now",
            sa.BOOLEAN(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_check_constraint(
        op.f("ck__alpaca_connectors__etf_portfolio_configuration_0251d16f17"),
        portfolio_table,
        "portfolio_prices_frequency IS NULL OR portfolio_prices_frequency = '1d'",
    )
    op.drop_constraint(
        op.f("ck__alpaca_connectors__etf_portfolio_configuration_3c4a73aa27"),
        portfolio_table,
        type_="check",
    )
    op.drop_column(portfolio_table, "valuation_maximum_staleness_seconds")
    op.alter_column(portfolio_table, "portfolio_prices_frequency", server_default=None)
    op.alter_column(portfolio_table, "forward_fill_to_now", server_default=None)
