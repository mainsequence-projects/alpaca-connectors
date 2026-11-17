"""drop retired configuration tables

Revision ID: 0028
Revises: 0027
Create Date: 2026-11-17 23:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0028"
down_revision: Union[str, Sequence[str], None] = "0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_NAME = "alpaca_connectors__portfolio_configuration"
PREVIOUS_TABLE = "alpaca_connectors__etf_portfolio_configuration"
SIGNAL_CONFIGURATION_TABLE = "alpaca_connectors__etf_signal_job_configuration"
UNIVERSE_TABLE = "alpaca_connectors__asset_universe"
SOURCE_TABLE = "alpaca_connectors__universe_source"
BARS_TABLE = "alpaca_connectors__bars_configuration"
SIGNAL_ASSET_TABLE = "alpaca_connectors__signal_asset"
BARS_UNIVERSE_KEY = "fk__alpaca_connectors__bars_configuration__universe_a0233ca8ac"
BARS_UNIVERSE_CHECK = "ck__alpaca_connectors__bars_configuration__ck_alpaca_a7f554576d"
BARS_SOURCE_CHECK = "ck__alpaca_connectors__bars_configuration__ck_alpaca_4aa4463518"
BARS_SOURCES = "asset_source IN ('assets', 'account_holdings', 'portfolios')"
PREVIOUS_BARS_SOURCES = "asset_source IN ('assets', 'universe', 'account_holdings', 'portfolios')"
BARS_UNIVERSE_SCOPE = (
    "(asset_source = 'universe' AND universe_uid IS NOT NULL) OR "
    "(asset_source <> 'universe' AND universe_uid IS NULL)"
)
# The columns both configuration tables share (revision 0027).
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
# Revision 0027 refused to run while any of these remained, and no release since creates them;
# finding one means it was written outside the application, so nothing is dropped.
REMAINING_OBJECTS = (
    (
        "ETF portfolio configurations",
        f"SELECT COUNT(*) FROM {PREVIOUS_TABLE} WHERE weights_source <> 'provided'",
    ),
    ("signal configurations", f"SELECT COUNT(*) FROM {SIGNAL_CONFIGURATION_TABLE}"),
    (
        "bar configurations whose asset source is universe",
        f"SELECT COUNT(*) FROM {BARS_TABLE} WHERE asset_source = 'universe'",
    ),
    ("Asset Universes", f"SELECT COUNT(*) FROM {UNIVERSE_TABLE}"),
    ("Universe Sources", f"SELECT COUNT(*) FROM {SOURCE_TABLE}"),
)


def refuse_while_objects_remain() -> None:
    """Refuse to drop a table that still holds an object no release can read any more."""
    bind = op.get_bind()
    remaining = [
        f"{count} {label}"
        for label, query in REMAINING_OBJECTS
        if (count := int(bind.execute(sa.text(query)).scalar() or 0))
    ]
    if remaining:
        raise RuntimeError(
            "Revision 0028 refuses to drop tables that still hold rows: "
            + ", ".join(remaining)
            + ". Revision 0027 refused to run while they remained; find who wrote them before "
            "dropping anything (ADR 0027)."
        )


def copy_rows(source: str) -> str:
    """Rows of ``source`` the new table lacks, each under its own ``uid``."""
    columns = ", ".join(COPIED_COLUMNS)
    return (
        f"INSERT INTO {TABLE_NAME} ({columns}) SELECT {columns} FROM {source} "
        f"WHERE weights_source = 'provided' AND uid NOT IN (SELECT uid FROM {TABLE_NAME})"
    )


def forget_unnamed_signal_assets() -> None:
    """Delete the signal-asset rows of every signal no portfolio configuration names.

    A configuration's signal UID is a hash of its ``ApplicationWeights`` configuration, computed
    by the application's own ``provided_signal_uid``; it is imported only when there are rows to
    check, so an empty table never needs it.
    """
    bind = op.get_bind()
    stored = {
        str(row[0])
        for row in bind.execute(sa.text(f"SELECT DISTINCT signal_uid FROM {SIGNAL_ASSET_TABLE}"))
    }
    if not stored:
        return
    from alpaca_connectors.portfolios.provided_weights import provided_signal_uid

    named = {
        provided_signal_uid(row[0])
        for row in bind.execute(sa.text(f"SELECT uid FROM {TABLE_NAME}"))
    }
    unnamed = sorted(stored - named)
    if unnamed:
        signal_assets = sa.table(SIGNAL_ASSET_TABLE, sa.column("signal_uid"))
        bind.execute(sa.delete(signal_assets).where(signal_assets.c.signal_uid.in_(unnamed)))


def upgrade() -> None:
    """Drop what only the releases before ADR 0027 used.

    The previous configuration table goes after the rows the new table lacks are copied again:
    the release that served during revision 0027's rollout could still insert them. Then the
    tables of the signal configurations, Asset Universes and Universe Sources, the bars
    configurations' ``universe_uid`` and ``universe`` source, and the signal-asset rows of signals
    no configuration names. Their observations and series stay in the instance tables.
    """
    refuse_while_objects_remain()
    op.execute(sa.text(copy_rows(PREVIOUS_TABLE)))
    op.drop_table(PREVIOUS_TABLE)
    op.drop_table(SIGNAL_CONFIGURATION_TABLE)
    # Present unless this revision's downgrade ran, which does not restore it.
    keys = {key["name"] for key in sa.inspect(op.get_bind()).get_foreign_keys(BARS_TABLE)}
    if BARS_UNIVERSE_KEY in keys:
        op.drop_constraint(op.f(BARS_UNIVERSE_KEY), BARS_TABLE, type_="foreignkey")
    op.drop_constraint(op.f(BARS_UNIVERSE_CHECK), BARS_TABLE, type_="check")
    op.drop_column(BARS_TABLE, "universe_uid")
    op.drop_constraint(op.f(BARS_SOURCE_CHECK), BARS_TABLE, type_="check")
    op.create_check_constraint(op.f(BARS_SOURCE_CHECK), BARS_TABLE, BARS_SOURCES)
    op.drop_table(UNIVERSE_TABLE)
    op.drop_table(SOURCE_TABLE)
    forget_unnamed_signal_assets()


def downgrade() -> None:
    """Restore the dropped tables and the bars universe source, empty, as revision 0027 left them.

    Every configuration goes back to the previous table as a provided-weights row, so revision
    0027's downgrade still finds each one there. The bars configurations get ``universe_uid`` and
    both checks back, but not the foreign key to the Asset Universes: that table is no longer one
    of this application's catalog tables, and the catalog reconciles a declared table only with
    keys to its own tables. The deleted signal-asset rows are not restored.
    """
    op.create_table(
        SOURCE_TABLE,
        sa.Column("uid", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("source_url", sa.String(length=2048), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("uid", name=op.f(f"pk__{SOURCE_TABLE}")),
        info={
            "namespace": "alpaca-connectors",
            "identifier": "alpaca-connectors.UniverseSource",
            "markets_storage_app": "alpaca_connectors",
        },
    )
    for name, columns, unique in (
        ("ix__alpaca_connectors__universe_source__enabled", ["enabled"], False),
        ("ix__alpaca_connectors__universe_source__symbol", ["symbol"], False),
    ):
        op.create_index(op.f(name), SOURCE_TABLE, columns, unique=unique)

    op.create_table(
        UNIVERSE_TABLE,
        sa.Column("uid", sa.Uuid(), nullable=False),
        sa.Column("source_uid", sa.Uuid(), nullable=False),
        sa.Column("asset_category_uid", sa.Uuid(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["asset_category_uid"],
            ["ms_markets__assetcategory.uid"],
            name=op.f("fk__alpaca_connectors__asset_universe__asset_categor_8bf394f434"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_uid"],
            [f"{SOURCE_TABLE}.uid"],
            name=op.f("fk__alpaca_connectors__asset_universe__source_uid__a_116b545a8a"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("uid", name=op.f(f"pk__{UNIVERSE_TABLE}")),
        info={
            "namespace": "alpaca-connectors",
            "identifier": "alpaca-connectors.AssetUniverse",
            "markets_storage_app": "alpaca_connectors",
        },
    )
    for name, columns, unique in (
        ("ix__alpaca_connectors__asset_universe__is_active", ["is_active"], False),
        (
            "uix__alpaca_connectors__asset_universe__asset_category_uid",
            ["asset_category_uid"],
            True,
        ),
        ("uix__alpaca_connectors__asset_universe__source_uid", ["source_uid"], True),
    ):
        op.create_index(op.f(name), UNIVERSE_TABLE, columns, unique=unique)

    op.drop_constraint(op.f(BARS_SOURCE_CHECK), BARS_TABLE, type_="check")
    op.create_check_constraint(op.f(BARS_SOURCE_CHECK), BARS_TABLE, PREVIOUS_BARS_SOURCES)
    op.add_column(BARS_TABLE, sa.Column("universe_uid", sa.Uuid(), nullable=True))
    op.create_check_constraint(op.f(BARS_UNIVERSE_CHECK), BARS_TABLE, BARS_UNIVERSE_SCOPE)

    op.create_table(
        SIGNAL_CONFIGURATION_TABLE,
        sa.Column("uid", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("universe_uid", sa.Uuid(), nullable=False),
        sa.Column("account_uid", sa.Uuid(), nullable=False),
        sa.Column("job_uid", sa.Uuid(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("schedule_type", sa.String(length=16), nullable=False),
        sa.Column("schedule_every", sa.Integer(), nullable=True),
        sa.Column("schedule_period", sa.String(length=16), nullable=True),
        sa.Column("schedule_expression", sa.String(length=128), nullable=True),
        sa.Column("schedule_start_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cpu_request", sa.String(length=16), nullable=False),
        sa.Column("memory_request", sa.String(length=16), nullable=False),
        sa.Column("max_runtime_seconds", sa.Integer(), nullable=False),
        sa.Column("spot", sa.Boolean(), nullable=False),
        sa.Column("lifecycle_state", sa.String(length=16), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("schedule_timezone", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "max_runtime_seconds > 0",
            name=op.f("ck__alpaca_connectors__etf_signal_job_configuration_3a0deb0058"),
        ),
        sa.CheckConstraint(
            "schedule_type IN ('interval', 'crontab')",
            name=op.f("ck__alpaca_connectors__etf_signal_job_configuration_d50e12d4e8"),
        ),
        sa.CheckConstraint(
            "schedule_period IS NULL OR schedule_period IN ('seconds', 'minutes', 'hours', 'days')",
            name=op.f("ck__alpaca_connectors__etf_signal_job_configuration_9f6b25d0b9"),
        ),
        sa.CheckConstraint(
            "lifecycle_state IN ('provisioning', 'ready', 'paused', 'error', 'deleting')",
            name=op.f("ck__alpaca_connectors__etf_signal_job_configuration_b924a12291"),
        ),
        sa.CheckConstraint(
            "(schedule_type = 'interval' AND schedule_every IS NOT NULL "
            "AND schedule_every > 0 AND schedule_period IS NOT NULL "
            "AND schedule_expression IS NULL AND schedule_timezone IS NULL) OR "
            "(schedule_type = 'crontab' AND schedule_every IS NULL "
            "AND schedule_period IS NULL AND schedule_expression IS NOT NULL "
            "AND schedule_timezone IS NOT NULL)",
            name=op.f("ck__alpaca_connectors__etf_signal_job_configuration_871f2fa48b"),
        ),
        sa.ForeignKeyConstraint(
            ["account_uid"],
            ["ms_markets__account.uid"],
            name=op.f("fk__alpaca_connectors__etf_signal_job_configuration_6bfe1585f8"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["universe_uid"],
            [f"{UNIVERSE_TABLE}.uid"],
            name=op.f("fk__alpaca_connectors__etf_signal_job_configuration_272f8dcd06"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("uid", name=op.f(f"pk__{SIGNAL_CONFIGURATION_TABLE}")),
        info={
            "namespace": "alpaca-connectors",
            "identifier": "alpaca-connectors.AlpacaETFSignalJobConfiguration",
            "markets_storage_app": "alpaca_connectors",
        },
    )
    for name, columns, unique in (
        ("ix__alpaca_connectors__etf_signal_job_configuration__name", ["name"], False),
        (
            "uix__alpaca_connectors__etf_signal_job_configuration_f4a84bcf45",
            ["universe_uid"],
            True,
        ),
        (
            "ix__alpaca_connectors__etf_signal_job_configuration_11eac71748",
            ["account_uid"],
            False,
        ),
        ("uix__alpaca_connectors__etf_signal_job_configuration__job_uid", ["job_uid"], True),
        ("ix__alpaca_connectors__etf_signal_job_configuration__enabled", ["enabled"], False),
        (
            "ix__alpaca_connectors__etf_signal_job_configuration_e46c395bf8",
            ["lifecycle_state"],
            False,
        ),
    ):
        op.create_index(op.f(name), SIGNAL_CONFIGURATION_TABLE, columns, unique=unique)

    op.create_table(
        PREVIOUS_TABLE,
        sa.Column("uid", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("signal_configuration_uid", sa.Uuid(), nullable=True),
        sa.Column("bars_configuration_uid", sa.Uuid(), nullable=False),
        sa.Column("rebalance_configuration_uid", sa.Uuid(), nullable=False),
        sa.Column("portfolio_uid", sa.Uuid(), nullable=True),
        sa.Column("job_uid", sa.Uuid(), nullable=True),
        sa.Column("upsample_frequency_id", sa.String(length=16), nullable=False),
        sa.Column("intraday_bar_interpolation_rule", sa.String(length=16), nullable=False),
        sa.Column("valuation_column", sa.String(length=64), nullable=False),
        sa.Column("fail_on_missing_prices", sa.Boolean(), nullable=False),
        sa.Column("commission_fee", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valuation_maximum_staleness_seconds", sa.Integer(), nullable=False),
        sa.Column("scheduled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "weights_source",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'etf_signal'"),
        ),
        sa.Column("owner_gateway_uid", sa.String(length=64), nullable=True),
        sa.Column("owner_user_uid", sa.String(length=255), nullable=True),
        *(
            sa.CheckConstraint(condition, name=op.f(name))
            for name, condition in (
                (
                    "ck__alpaca_connectors__etf_portfolio_configuration_1079d6402e",
                    "length(trim(valuation_column)) > 0",
                ),
                (
                    "ck__alpaca_connectors__etf_portfolio_configuration_a27f155565",
                    "(owner_gateway_uid IS NULL AND owner_user_uid IS NULL) OR "
                    "(owner_gateway_uid IS NOT NULL AND owner_user_uid IS NOT NULL)",
                ),
                (
                    "ck__alpaca_connectors__etf_portfolio_configuration_d6e75f168c",
                    "(weights_source = 'etf_signal' AND signal_configuration_uid IS NOT NULL) OR "
                    "(weights_source = 'provided' AND signal_configuration_uid IS NULL)",
                ),
                (
                    "ck__alpaca_connectors__etf_portfolio_configuration_3c4a73aa27",
                    "valuation_maximum_staleness_seconds > 0",
                ),
                (
                    "ck__alpaca_connectors__etf_portfolio_configuration_b1ac7aaa76",
                    "commission_fee >= 0",
                ),
                (
                    "ck__alpaca_connectors__etf_portfolio_configuration_e79d7c21d6",
                    "length(trim(upsample_frequency_id)) > 0",
                ),
                (
                    "ck__alpaca_connectors__etf_portfolio_configuration_081735bbf6",
                    "intraday_bar_interpolation_rule = 'ffill'",
                ),
                (
                    "ck__alpaca_connectors__etf_portfolio_configuration_a1c4d4a0f0",
                    "upsample_frequency_id = '1d'",
                ),
                (
                    "ck__alpaca_connectors__etf_portfolio_configuration_d641612457",
                    "length(trim(intraday_bar_interpolation_rule)) > 0",
                ),
                (
                    "ck__alpaca_connectors__etf_portfolio_configuration_18335145e8",
                    "weights_source IN ('etf_signal', 'provided')",
                ),
                (
                    "ck__alpaca_connectors__etf_portfolio_configuration_75f81916a8",
                    "weights_source <> 'provided' OR owner_gateway_uid IS NOT NULL",
                ),
            )
        ),
        sa.ForeignKeyConstraint(
            ["bars_configuration_uid"],
            [f"{BARS_TABLE}.uid"],
            name=op.f("fk__alpaca_connectors__etf_portfolio_configuration_d7050e3846"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_uid"],
            ["ms_markets__portfolio.uid"],
            name=op.f("fk__alpaca_connectors__etf_portfolio_configuration_d1e5d9a31d"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["rebalance_configuration_uid"],
            ["alpaca_connectors__portfolio_rebalance_configuration.uid"],
            name=op.f("fk__alpaca_connectors__etf_portfolio_configuration_0c3e4234aa"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["signal_configuration_uid"],
            [f"{SIGNAL_CONFIGURATION_TABLE}.uid"],
            name=op.f("fk__alpaca_connectors__etf_portfolio_configuration_8c86b51e46"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("uid", name=op.f(f"pk__{PREVIOUS_TABLE}")),
        info={
            "namespace": "alpaca-connectors",
            "identifier": "alpaca-connectors.AlpacaETFPortfolioConfiguration",
            "markets_storage_app": "alpaca_connectors",
        },
    )
    for name, columns, unique in (
        (
            "ix__alpaca_connectors__etf_portfolio_configuration_53174662de",
            ["signal_configuration_uid"],
            False,
        ),
        ("ix__alpaca_connectors__etf_portfolio_configuration__name", ["name"], False),
        (
            "ix__alpaca_connectors__etf_portfolio_configuration_d13119c5b7",
            ["bars_configuration_uid"],
            False,
        ),
        (
            "ix__alpaca_connectors__etf_portfolio_configuration_fb7a662c4d",
            ["rebalance_configuration_uid"],
            False,
        ),
        ("uix__alpaca_connectors__etf_portfolio_configuration__job_uid", ["job_uid"], True),
        (
            "uix__alpaca_connectors__etf_portfolio_configuration_df075f07b6",
            ["portfolio_uid"],
            True,
        ),
    ):
        op.create_index(op.f(name), PREVIOUS_TABLE, columns, unique=unique)

    columns = ", ".join(COPIED_COLUMNS)
    op.execute(
        sa.text(
            f"INSERT INTO {PREVIOUS_TABLE} ({columns}, weights_source) "
            f"SELECT {columns}, 'provided' FROM {TABLE_NAME}"
        )
    )
