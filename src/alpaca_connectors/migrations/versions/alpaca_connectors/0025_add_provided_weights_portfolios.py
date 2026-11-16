"""add provided weights portfolios

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-16 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0025"
down_revision: Union[str, Sequence[str], None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PORTFOLIO_TABLE = "alpaca_connectors__etf_portfolio_configuration"
BARS_TABLE = "alpaca_connectors__bars_configuration"
SIGNAL_ASSET_TABLE = "alpaca_connectors__signal_asset"
SIGNAL_WEIGHTS_TABLE = "alpaca_connectors__signalweightsts__main"
BARS_SOURCE_CHECK = "ck__alpaca_connectors__bars_configuration__ck_alpaca_4aa4463518"
PORTFOLIO_CHECKS = (
    (
        "ck__alpaca_connectors__etf_portfolio_configuration_18335145e8",
        "weights_source IN ('etf_signal', 'provided')",
    ),
    (
        "ck__alpaca_connectors__etf_portfolio_configuration_d6e75f168c",
        "(weights_source = 'etf_signal' AND signal_configuration_uid IS NOT NULL) OR "
        "(weights_source = 'provided' AND signal_configuration_uid IS NULL)",
    ),
    (
        "ck__alpaca_connectors__etf_portfolio_configuration_a27f155565",
        "(owner_gateway_uid IS NULL AND owner_user_uid IS NULL) OR "
        "(owner_gateway_uid IS NOT NULL AND owner_user_uid IS NOT NULL)",
    ),
    (
        "ck__alpaca_connectors__etf_portfolio_configuration_75f81916a8",
        "weights_source <> 'provided' OR owner_gateway_uid IS NOT NULL",
    ),
)


def upgrade() -> None:
    """Portfolios built from provided weights (ADR 0024).

    Adds and relaxes only, so the release still serving during the rollout keeps inserting ETF
    rows: ``weights_source`` defaults to ``etf_signal`` on the server, and that release never
    names the new source or column. The signal-asset table is filled once from the signal
    weights instance, the only full read of it this design makes.
    """
    op.add_column(
        PORTFOLIO_TABLE,
        sa.Column(
            "weights_source",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'etf_signal'"),
        ),
    )
    op.add_column(PORTFOLIO_TABLE, sa.Column("owner_gateway_uid", sa.String(length=64)))
    op.add_column(PORTFOLIO_TABLE, sa.Column("owner_user_uid", sa.String(length=255)))
    op.alter_column(
        PORTFOLIO_TABLE,
        "signal_configuration_uid",
        existing_type=sa.Uuid(),
        nullable=True,
    )
    for name, condition in PORTFOLIO_CHECKS:
        op.create_check_constraint(op.f(name), PORTFOLIO_TABLE, condition)

    op.drop_constraint(op.f(BARS_SOURCE_CHECK), BARS_TABLE, type_="check")
    op.create_check_constraint(
        op.f(BARS_SOURCE_CHECK),
        BARS_TABLE,
        "asset_source IN ('assets', 'universe', 'account_holdings', 'portfolios')",
    )

    op.create_table(
        SIGNAL_ASSET_TABLE,
        sa.Column("signal_uid", sa.String(length=255), nullable=False),
        sa.Column("asset_identifier", sa.String(length=255), nullable=False),
        sa.Column("first_published_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "signal_uid", "asset_identifier", name=op.f(f"pk__{SIGNAL_ASSET_TABLE}")
        ),
        info={
            "namespace": "alpaca-connectors",
            "identifier": "alpaca-connectors.SignalAsset",
            "markets_storage_app": "alpaca_connectors",
        },
    )
    op.execute(
        sa.text(
            f"INSERT INTO {SIGNAL_ASSET_TABLE} (signal_uid, asset_identifier, first_published_at) "
            f"SELECT signal_uid, asset_identifier, MIN(time_index) FROM {SIGNAL_WEIGHTS_TABLE} "
            "GROUP BY signal_uid, asset_identifier"
        )
    )


def downgrade() -> None:
    """Remove provided-weights portfolios; fails while a provided-weights row exists."""
    op.drop_table(SIGNAL_ASSET_TABLE)
    op.drop_constraint(op.f(BARS_SOURCE_CHECK), BARS_TABLE, type_="check")
    op.create_check_constraint(
        op.f(BARS_SOURCE_CHECK),
        BARS_TABLE,
        "asset_source IN ('assets', 'universe', 'account_holdings')",
    )
    for name, _condition in reversed(PORTFOLIO_CHECKS):
        op.drop_constraint(op.f(name), PORTFOLIO_TABLE, type_="check")
    op.alter_column(
        PORTFOLIO_TABLE,
        "signal_configuration_uid",
        existing_type=sa.Uuid(),
        nullable=False,
    )
    op.drop_column(PORTFOLIO_TABLE, "owner_user_uid")
    op.drop_column(PORTFOLIO_TABLE, "owner_gateway_uid")
    op.drop_column(PORTFOLIO_TABLE, "weights_source")
