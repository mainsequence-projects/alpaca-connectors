"""add portfolio configurations

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-17 22:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0027"
down_revision: Union[str, Sequence[str], None] = "0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_NAME = "alpaca_connectors__portfolio_configuration"
PREVIOUS_TABLE = "alpaca_connectors__etf_portfolio_configuration"
BARS_TABLE = "alpaca_connectors__bars_configuration"
REBALANCE_TABLE = "alpaca_connectors__portfolio_rebalance_configuration"
PORTFOLIO_TABLE = "ms_markets__portfolio"
CHECKS = (
    ("ck__alpaca_connectors__portfolio_configuration__ck_p_68d0fc8309", "commission_fee >= 0"),
    (
        "ck__alpaca_connectors__portfolio_configuration__ck_p_aade2dd354",
        "length(trim(valuation_column)) > 0",
    ),
    (
        "ck__alpaca_connectors__portfolio_configuration__ck_p_5799a1046a",
        "length(trim(upsample_frequency_id)) > 0",
    ),
    (
        "ck__alpaca_connectors__portfolio_configuration__ck_p_9cdf42c3ea",
        "length(trim(intraday_bar_interpolation_rule)) > 0",
    ),
    (
        "ck__alpaca_connectors__portfolio_configuration__ck_p_5d43f1dc85",
        "upsample_frequency_id = '1d'",
    ),
    (
        "ck__alpaca_connectors__portfolio_configuration__ck_p_92e6ee43b7",
        "intraday_bar_interpolation_rule = 'ffill'",
    ),
    (
        "ck__alpaca_connectors__portfolio_configuration__ck_p_a2cd950045",
        "valuation_maximum_staleness_seconds > 0",
    ),
)
INDEXES = (
    ("ix__alpaca_connectors__portfolio_configuration__name", ["name"], False),
    (
        "ix__alpaca_connectors__portfolio_configuration__bars_3c39184ecf",
        ["bars_configuration_uid"],
        False,
    ),
    (
        "ix__alpaca_connectors__portfolio_configuration__reba_8e2bd41f2b",
        ["rebalance_configuration_uid"],
        False,
    ),
    ("uix__alpaca_connectors__portfolio_configuration__portfolio_uid", ["portfolio_uid"], True),
)
# The columns both tables share: the previous table's provided-weights rows, without the weights
# source, the Signal Configuration and the unused `job_uid`.
COPIED_COLUMNS = (
    "uid",
    "name",
    "description",
    "bars_configuration_uid",
    "rebalance_configuration_uid",
    "portfolio_uid",
    "upsample_frequency_id",
    "intraday_bar_interpolation_rule",
    "valuation_column",
    "valuation_maximum_staleness_seconds",
    "fail_on_missing_prices",
    "commission_fee",
    "created_at",
    "updated_at",
    "scheduled",
    "owner_gateway_uid",
    "owner_user_uid",
)
# What must be gone before this release: the objects only the previous release can drain, which
# this release no longer reads (ADR 0027).
REMAINING_OBJECTS = (
    (
        "ETF portfolio configurations",
        f"SELECT COUNT(*) FROM {PREVIOUS_TABLE} WHERE weights_source <> 'provided'",
    ),
    (
        "signal configurations",
        "SELECT COUNT(*) FROM alpaca_connectors__etf_signal_job_configuration",
    ),
    (
        "bar configurations whose asset source is universe",
        f"SELECT COUNT(*) FROM {BARS_TABLE} WHERE asset_source = 'universe'",
    ),
    ("Asset Universes", "SELECT COUNT(*) FROM alpaca_connectors__asset_universe"),
    ("Universe Sources", "SELECT COUNT(*) FROM alpaca_connectors__universe_source"),
)


def refuse_while_objects_remain() -> None:
    """Refuse to run until the previous release's CLI has drained every object it alone reads."""
    bind = op.get_bind()
    remaining = [
        f"{count} {label}"
        for label, query in REMAINING_OBJECTS
        if (count := int(bind.execute(sa.text(query)).scalar() or 0))
    ]
    if remaining:
        raise RuntimeError(
            "Revision 0027 refuses to run while these remain: "
            + ", ".join(remaining)
            + ". Drain the Environment with the previous release's CLI first (portfolio delete, "
            "signal delete, bar-configuration update or delete, universe delete, universe-source "
            "delete; ADR 0027), then deploy this release again."
        )


def copy_rows(source: str) -> str:
    """Rows of ``source`` the new table lacks, each under its own ``uid``."""
    columns = ", ".join(COPIED_COLUMNS)
    return (
        f"INSERT INTO {TABLE_NAME} ({columns}) SELECT {columns} FROM {source} "
        f"WHERE weights_source = 'provided' AND uid NOT IN (SELECT uid FROM {TABLE_NAME})"
    )


def upgrade() -> None:
    """One table of portfolios built from provided weights, with the existing rows (ADR 0027).

    Adds only: the previous table stays, so the release still serving during the rollout keeps
    writing it, and revision 0028 copies again any row it wrote after this copy before dropping
    it. Each row keeps its ``uid``, which the application that owns the portfolio holds.
    """
    refuse_while_objects_remain()
    op.create_table(
        TABLE_NAME,
        sa.Column("uid", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("bars_configuration_uid", sa.Uuid(), nullable=False),
        sa.Column("rebalance_configuration_uid", sa.Uuid(), nullable=False),
        sa.Column("portfolio_uid", sa.Uuid(), nullable=True),
        sa.Column("upsample_frequency_id", sa.String(length=16), nullable=False),
        sa.Column("intraday_bar_interpolation_rule", sa.String(length=16), nullable=False),
        sa.Column("valuation_column", sa.String(length=64), nullable=False),
        sa.Column("valuation_maximum_staleness_seconds", sa.Integer(), nullable=False),
        sa.Column("fail_on_missing_prices", sa.Boolean(), nullable=False),
        sa.Column("commission_fee", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scheduled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("owner_gateway_uid", sa.String(length=64), nullable=False),
        sa.Column("owner_user_uid", sa.String(length=255), nullable=False),
        *(sa.CheckConstraint(condition, name=op.f(name)) for name, condition in CHECKS),
        sa.ForeignKeyConstraint(
            ["bars_configuration_uid"],
            [f"{BARS_TABLE}.uid"],
            name=op.f("fk__alpaca_connectors__portfolio_configuration__bars_2903b46163"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["rebalance_configuration_uid"],
            [f"{REBALANCE_TABLE}.uid"],
            name=op.f("fk__alpaca_connectors__portfolio_configuration__reba_19271ffaa5"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_uid"],
            [f"{PORTFOLIO_TABLE}.uid"],
            name=op.f("fk__alpaca_connectors__portfolio_configuration__port_fd64afe62b"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("uid", name=op.f(f"pk__{TABLE_NAME}")),
        info={
            "namespace": "alpaca-connectors",
            "identifier": "alpaca-connectors.AlpacaPortfolioConfiguration",
            "markets_storage_app": "alpaca_connectors",
        },
    )
    for name, columns, unique in INDEXES:
        op.create_index(op.f(name), TABLE_NAME, columns, unique=unique)
    op.execute(sa.text(copy_rows(PREVIOUS_TABLE)))


def downgrade() -> None:
    """Remove the table; the previous table still holds every row this revision copied."""
    for name, _columns, _unique in reversed(INDEXES):
        op.drop_index(op.f(name), table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)
