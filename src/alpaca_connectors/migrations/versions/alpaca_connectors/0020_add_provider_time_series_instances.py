"""add provider time-series instances

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-26 12:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0020"
down_revision: Union[str, Sequence[str], None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create this application's instances of the Markets time-series contracts (ADR 0047).

    Signal weights, portfolio values, portfolio weights, rebalance state, calendar events, orders
    and trades move from the shared ``ms_markets__*`` tables to contract-identical
    ``alpaca_connectors__<contract>__main`` tables in the ``alpaca-connectors`` namespace
    (``alpaca_connectors.instances``). Their foreign keys reference the Markets registries by
    physical name. The shared tables keep their rows; ``alpaca-connectors instances backfill``
    moves this application's rows (docs/operations/markets-api-boundary.md).
    """
    op.create_table(
        "alpaca_connectors__signalweightsts__main",
        sa.Column("time_index", sa.DateTime(timezone=True), nullable=False),
        sa.Column("signal_uid", sa.String(length=255), nullable=False),
        sa.Column("asset_identifier", sa.String(length=255), nullable=False),
        sa.Column("signal_weight", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(
            ["asset_identifier"],
            ["ms_markets__asset.unique_identifier"],
            name=op.f("fk__alpaca_connectors__signalweightsts__main__asset_5348d6649d"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["signal_uid"],
            ["ms_markets__signalmetadata.signal_uid"],
            name=op.f("fk__alpaca_connectors__signalweightsts__main__signal_59dc0c4aa1"),
            ondelete="RESTRICT",
        ),
        info={
            "namespace": "alpaca-connectors",
            "identifier": "alpaca-connectors.SignalWeightsTS.main",
            "markets_storage_app": "alpaca_connectors",
        },
    )
    op.create_index(
        "uix__alpaca_connectors__signalweightsts__main__time_2174cdf9f7",
        "alpaca_connectors__signalweightsts__main",
        ["time_index", "signal_uid", "asset_identifier"],
        unique=True,
    )
    op.create_table(
        "alpaca_connectors__portfoliosts__main",
        sa.Column("time_index", sa.DateTime(timezone=True), nullable=False),
        sa.Column("portfolio_identifier", sa.String(length=255), nullable=False),
        sa.Column("close", sa.Float(), nullable=True),
        sa.Column("return", sa.Float(), nullable=True),
        sa.Column("calculated_close", sa.Float(), nullable=True),
        sa.Column("close_time", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["portfolio_identifier"],
            ["ms_markets__portfolio.unique_identifier"],
            name=op.f("fk__alpaca_connectors__portfoliosts__main__portfolio_8e2c225d66"),
            ondelete="RESTRICT",
        ),
        info={
            "namespace": "alpaca-connectors",
            "identifier": "alpaca-connectors.PortfoliosTS.main",
            "markets_storage_app": "alpaca_connectors",
        },
    )
    op.create_index(
        "uix__alpaca_connectors__portfoliosts__main__time_ind_9ea4da217a",
        "alpaca_connectors__portfoliosts__main",
        ["time_index", "portfolio_identifier"],
        unique=True,
    )
    op.create_table(
        "alpaca_connectors__portfolioweightsts__main",
        sa.Column("time_index", sa.DateTime(timezone=True), nullable=False),
        sa.Column("portfolio_identifier", sa.String(length=255), nullable=False),
        sa.Column("asset_identifier", sa.String(length=255), nullable=False),
        sa.Column("weight", sa.Float(), nullable=True),
        sa.Column("weight_before", sa.Float(), nullable=True),
        sa.Column("price_current", sa.Float(), nullable=True),
        sa.Column("price_before", sa.Float(), nullable=True),
        sa.Column("volume_current", sa.Float(), nullable=True),
        sa.Column("volume_before", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(
            ["asset_identifier"],
            ["ms_markets__asset.unique_identifier"],
            name=op.f("fk__alpaca_connectors__portfolioweightsts__main__ass_323237e07c"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_identifier"],
            ["ms_markets__portfolio.unique_identifier"],
            name=op.f("fk__alpaca_connectors__portfolioweightsts__main__por_79faf4e534"),
            ondelete="RESTRICT",
        ),
        info={
            "namespace": "alpaca-connectors",
            "identifier": "alpaca-connectors.PortfolioWeightsTS.main",
            "markets_storage_app": "alpaca_connectors",
        },
    )
    op.create_index(
        "uix__alpaca_connectors__portfolioweightsts__main__ti_4114ea45c3",
        "alpaca_connectors__portfolioweightsts__main",
        ["time_index", "portfolio_identifier", "asset_identifier"],
        unique=True,
    )
    op.create_table(
        "alpaca_connectors__portfoliorebalancestatets__main",
        sa.Column("time_index", sa.DateTime(timezone=True), nullable=False),
        sa.Column("portfolio_identifier", sa.String(length=255), nullable=False),
        sa.Column("rebalance_intent_id", sa.String(length=64), nullable=False),
        sa.Column("asset_identifier", sa.String(length=255), nullable=False),
        sa.Column("target_signal_time_index", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_source", sa.String(length=255), nullable=False),
        sa.Column("execution_status", sa.String(length=32), nullable=False),
        sa.Column("target_weight", sa.Float(), nullable=False),
        sa.Column("weight_before", sa.Float(), nullable=False),
        sa.Column("weight_after", sa.Float(), nullable=False),
        sa.Column("executed_weight_delta", sa.Float(), nullable=False),
        sa.Column("remaining_weight_delta", sa.Float(), nullable=False),
        sa.Column("execution_price", sa.Float(), nullable=True),
        sa.Column("executed_quantity", sa.Float(), nullable=True),
        sa.Column("executed_notional", sa.Float(), nullable=True),
        sa.Column("observed_volume", sa.Float(), nullable=True),
        sa.Column("observed_available_liquidity", sa.Float(), nullable=True),
        sa.Column("strategy_state", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["asset_identifier"],
            ["ms_markets__asset.unique_identifier"],
            name=op.f("fk__alpaca_connectors__portfoliorebalancestatets__ma_f857de8247"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_identifier"],
            ["ms_markets__portfolio.unique_identifier"],
            name=op.f("fk__alpaca_connectors__portfoliorebalancestatets__ma_ee91a45b8c"),
            ondelete="RESTRICT",
        ),
        info={
            "namespace": "alpaca-connectors",
            "identifier": "alpaca-connectors.PortfolioRebalanceStateTS.main",
            "markets_storage_app": "alpaca_connectors",
        },
    )
    op.create_index(
        "uix__alpaca_connectors__portfoliorebalancestatets__m_44c63e0246",
        "alpaca_connectors__portfoliorebalancestatets__main",
        ["time_index", "portfolio_identifier", "rebalance_intent_id", "asset_identifier"],
        unique=True,
    )
    op.create_table(
        "alpaca_connectors__portfoliocalendareventsts__main",
        sa.Column("time_index", sa.DateTime(timezone=True), nullable=False),
        sa.Column("calendar_identifier", sa.String(length=255), nullable=False),
        sa.Column("session_label", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("local_date", sa.String(length=10), nullable=False),
        sa.ForeignKeyConstraint(
            ["calendar_identifier"],
            ["ms_markets__calendar.unique_identifier"],
            name=op.f("fk__alpaca_connectors__portfoliocalendareventsts__ma_3f7773c3f5"),
            ondelete="RESTRICT",
        ),
        info={
            "namespace": "alpaca-connectors",
            "identifier": "alpaca-connectors.PortfolioCalendarEventsTS.main",
            "markets_storage_app": "alpaca_connectors",
        },
    )
    op.create_index(
        "uix__alpaca_connectors__portfoliocalendareventsts__m_c7d75fd579",
        "alpaca_connectors__portfoliocalendareventsts__main",
        ["time_index", "calendar_identifier", "session_label", "event_type"],
        unique=True,
    )
    op.create_table(
        "alpaca_connectors__ordersts__main",
        sa.Column("order_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("order_identifier", sa.String(), nullable=False),
        sa.Column("account_identifier", sa.String(length=255), nullable=False),
        sa.Column("order_manager_identifier", sa.String(length=255), nullable=True),
        sa.Column("asset_identifier", sa.String(length=255), nullable=False),
        sa.Column("order_remote_id", sa.String(), nullable=True),
        sa.Column("client_order_id", sa.String(), nullable=True),
        sa.Column("order_type", sa.String(), nullable=True),
        sa.Column("order_side", sa.BigInteger(), nullable=True),
        sa.Column("quantity", sa.Float(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("filled_quantity", sa.Float(), nullable=True),
        sa.Column("filled_price", sa.Float(), nullable=True),
        sa.Column("expires_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("limit_price", sa.Float(), nullable=True),
        sa.Column("time_in_force", sa.String(), nullable=True),
        sa.Column("comments", sa.String(), nullable=True),
        sa.Column("venue_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(
            ["account_identifier"],
            ["ms_markets__account.unique_identifier"],
            name=op.f("fk__alpaca_connectors__ordersts__main__account_ident_65b5d6638c"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["asset_identifier"],
            ["ms_markets__asset.unique_identifier"],
            name=op.f("fk__alpaca_connectors__ordersts__main__asset_identif_d501258745"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["order_manager_identifier"],
            ["ms_markets__ordermanager.unique_identifier"],
            name=op.f("fk__alpaca_connectors__ordersts__main__order_manager_def90dbef0"),
            ondelete="RESTRICT",
        ),
        info={
            "namespace": "alpaca-connectors",
            "identifier": "alpaca-connectors.OrdersTS.main",
            "markets_storage_app": "alpaca_connectors",
        },
    )
    op.create_index(
        "uix__alpaca_connectors__ordersts__main__order_time_458db1c85f",
        "alpaca_connectors__ordersts__main",
        ["order_time", "order_identifier", "account_identifier", "asset_identifier"],
        unique=True,
    )
    op.create_table(
        "alpaca_connectors__tradests__main",
        sa.Column("trade_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trade_identifier", sa.String(), nullable=False),
        sa.Column("account_identifier", sa.String(length=255), nullable=False),
        sa.Column("order_identifier", sa.String(), nullable=True),
        sa.Column("asset_identifier", sa.String(length=255), nullable=False),
        sa.Column("trade_side", sa.BigInteger(), nullable=True),
        sa.Column("quantity", sa.Float(), nullable=True),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("commission", sa.Float(), nullable=True),
        sa.Column("commission_asset_identifier", sa.String(length=255), nullable=True),
        sa.Column("settlement_cost", sa.Float(), nullable=True),
        sa.Column("settlement_asset_identifier", sa.String(length=255), nullable=True),
        sa.Column("comments", sa.String(), nullable=True),
        sa.Column("venue_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(
            ["account_identifier"],
            ["ms_markets__account.unique_identifier"],
            name=op.f("fk__alpaca_connectors__tradests__main__account_ident_fde1b7d0c8"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["asset_identifier"],
            ["ms_markets__asset.unique_identifier"],
            name=op.f("fk__alpaca_connectors__tradests__main__asset_identif_734bce5f8c"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["commission_asset_identifier"],
            ["ms_markets__asset.unique_identifier"],
            name=op.f("fk__alpaca_connectors__tradests__main__commission_as_05f0882502"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["settlement_asset_identifier"],
            ["ms_markets__asset.unique_identifier"],
            name=op.f("fk__alpaca_connectors__tradests__main__settlement_as_95b2b7aa28"),
            ondelete="RESTRICT",
        ),
        info={
            "namespace": "alpaca-connectors",
            "identifier": "alpaca-connectors.TradesTS.main",
            "markets_storage_app": "alpaca_connectors",
        },
    )
    op.create_index(
        "uix__alpaca_connectors__tradests__main__trade_time_de2a678e3f",
        "alpaca_connectors__tradests__main",
        ["trade_time", "trade_identifier", "account_identifier", "asset_identifier"],
        unique=True,
    )


def downgrade() -> None:
    """Drop the instance tables; the shared Markets tables keep their own rows."""
    op.drop_index(
        "uix__alpaca_connectors__tradests__main__trade_time_de2a678e3f",
        table_name="alpaca_connectors__tradests__main",
    )
    op.drop_table("alpaca_connectors__tradests__main")
    op.drop_index(
        "uix__alpaca_connectors__ordersts__main__order_time_458db1c85f",
        table_name="alpaca_connectors__ordersts__main",
    )
    op.drop_table("alpaca_connectors__ordersts__main")
    op.drop_index(
        "uix__alpaca_connectors__portfoliocalendareventsts__m_c7d75fd579",
        table_name="alpaca_connectors__portfoliocalendareventsts__main",
    )
    op.drop_table("alpaca_connectors__portfoliocalendareventsts__main")
    op.drop_index(
        "uix__alpaca_connectors__portfoliorebalancestatets__m_44c63e0246",
        table_name="alpaca_connectors__portfoliorebalancestatets__main",
    )
    op.drop_table("alpaca_connectors__portfoliorebalancestatets__main")
    op.drop_index(
        "uix__alpaca_connectors__portfolioweightsts__main__ti_4114ea45c3",
        table_name="alpaca_connectors__portfolioweightsts__main",
    )
    op.drop_table("alpaca_connectors__portfolioweightsts__main")
    op.drop_index(
        "uix__alpaca_connectors__portfoliosts__main__time_ind_9ea4da217a",
        table_name="alpaca_connectors__portfoliosts__main",
    )
    op.drop_table("alpaca_connectors__portfoliosts__main")
    op.drop_index(
        "uix__alpaca_connectors__signalweightsts__main__time_2174cdf9f7",
        table_name="alpaca_connectors__signalweightsts__main",
    )
    op.drop_table("alpaca_connectors__signalweightsts__main")
