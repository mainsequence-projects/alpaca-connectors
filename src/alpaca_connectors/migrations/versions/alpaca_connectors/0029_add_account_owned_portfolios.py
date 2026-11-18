"""add account-owned portfolios and signal definitions

Revision ID: 0029
Revises: 0028
Create Date: 2026-11-18 12:00:00.000000

"""

import datetime as dt
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0029"
down_revision: Union[str, Sequence[str], None] = "0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ACCOUNT_TABLE = "ms_markets__account"
DETAILS_TABLE = "alpaca_connectors__acct_alpaca"
DEFINITION_TABLE = "alpaca_connectors__signal_definition"
ENDED_TABLE = "alpaca_connectors__ended_portfolio"
PORTFOLIO_TABLE = "alpaca_connectors__portfolio_configuration"
BARS_TABLE = "alpaca_connectors__bars_configuration"

DEFINITION_CHECKS = (
    ("ck__alpaca_connectors__signal_definition__ck_signal_9fa3d0f669", "length(trim(key)) > 0"),
)
DEFINITION_ACCOUNT_KEY = "fk__alpaca_connectors__signal_definition__account_ui_f3dcc80805"
DEFINITION_UNIQUE = (
    ("uq__alpaca_connectors__signal_definition__account_uid__key", ["account_uid", "key"]),
    ("uq__alpaca_connectors__signal_definition__uid__account_uid", ["uid", "account_uid"]),
)
DEFINITION_INDEXES = (
    ("ix__alpaca_connectors__signal_definition__account_uid", ["account_uid"], False),
    ("uix__alpaca_connectors__signal_definition__signal_key", ["signal_key"], True),
)
ENDED_INDEXES = (
    ("ix__alpaca_connectors__ended_portfolio__account_uid", ["account_uid"], False),
    ("uix__alpaca_connectors__ended_portfolio__portfolio_uid", ["portfolio_uid"], True),
    ("ix__alpaca_connectors__ended_portfolio__signal_uid", ["signal_uid"], False),
)
PORTFOLIO_ACCOUNT_KEY = "fk__alpaca_connectors__portfolio_configuration__acco_3e91584880"
PORTFOLIO_DEFINITION_KEY = "fk__alpaca_connectors__portfolio_configuration__sign_10f7a179b0"
# Revision 0027's key from the portfolio to the bars configuration it named.
PORTFOLIO_BARS_KEY = "fk__alpaca_connectors__portfolio_configuration__bars_2903b46163"
PORTFOLIO_INDEXES = (
    ("ix__alpaca_connectors__portfolio_configuration__account_uid", ["account_uid"], False),
    (
        "ix__alpaca_connectors__portfolio_configuration__sign_3a17476c94",
        ["signal_definition_uid"],
        False,
    ),
)
BARS_ACCOUNT_KEY = "fk__alpaca_connectors__bars_configuration__account_u_ccd74cf5a7"
BARS_PORTFOLIO_KEY = "fk__alpaca_connectors__bars_configuration__portfolio_8b78eb7d8d"
BARS_ONE_PARENT = (
    "ck__alpaca_connectors__bars_configuration__ck_alpaca_30a9bbeb9b",
    "(account_uid IS NULL) <> (portfolio_configuration_uid IS NULL)",
)
BARS_PORTFOLIO_INDEX = (
    "uix__alpaca_connectors__bars_configuration__portfoli_c41eaa3c77",
    ["portfolio_configuration_uid"],
    True,
)
OWN_BARS_SOURCE = "portfolios"
OWN_BARS_SUFFIX = " prices"


def _tables():
    """The columns adoption reads and writes, as plain table constructs."""
    portfolios = sa.table(
        PORTFOLIO_TABLE,
        sa.column("uid", sa.Uuid()),
        sa.column("name", sa.String()),
        sa.column("account_uid", sa.Uuid()),
        sa.column("signal_definition_uid", sa.Uuid()),
        sa.column("bars_configuration_uid", sa.Uuid()),
        sa.column("owner_gateway_uid", sa.String()),
        sa.column("owner_user_uid", sa.String()),
    )
    bars = sa.table(
        BARS_TABLE,
        sa.column("uid", sa.Uuid()),
        sa.column("name", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("enabled", sa.Boolean()),
        sa.column("account_uid", sa.Uuid()),
        sa.column("portfolio_configuration_uid", sa.Uuid()),
        sa.column("asset_source", sa.String()),
        sa.column("frequency_id", sa.String()),
        sa.column("feed", sa.String()),
        sa.column("adjustment", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
        sa.column("scheduled", sa.Boolean()),
    )
    details = sa.table(
        DETAILS_TABLE,
        sa.column("account_uid", sa.Uuid()),
        sa.column("owner_gateway_uid", sa.String()),
        sa.column("owner_user_uid", sa.String()),
    )
    definitions = sa.table(
        DEFINITION_TABLE,
        sa.column("uid", sa.Uuid()),
        sa.column("account_uid", sa.Uuid()),
        sa.column("key", sa.String()),
        sa.column("signal_key", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    return portfolios, bars, details, definitions


def adoptable_portfolios(bind: sa.Connection) -> list[sa.RowMapping]:
    """Portfolios not yet adopted whose bars configuration's account their own owner owns.

    A portfolio whose bars run on an account without an owner, or on another owner's account,
    cannot be given one owning account; it stays unadopted until the ``portfolio adopt`` rollout
    command ends it (ADR 0028, decision 9).
    """
    portfolios, bars, details, _ = _tables()
    query = (
        sa.select(
            portfolios.c.uid,
            portfolios.c.name,
            bars.c.account_uid,
            bars.c.name.label("bars_name"),
            bars.c.description.label("bars_description"),
            bars.c.enabled,
            bars.c.scheduled,
            bars.c.frequency_id,
            bars.c.feed,
            bars.c.adjustment,
        )
        .select_from(
            portfolios.join(bars, bars.c.uid == portfolios.c.bars_configuration_uid).join(
                details, details.c.account_uid == bars.c.account_uid
            )
        )
        .where(
            portfolios.c.account_uid.is_(None),
            details.c.owner_gateway_uid == portfolios.c.owner_gateway_uid,
            details.c.owner_user_uid == portfolios.c.owner_user_uid,
        )
        .order_by(portfolios.c.uid)
    )
    return list(bind.execute(query).mappings())


def adopt_portfolios(bind: sa.Connection) -> int:
    """Give each adoptable portfolio its account, a signal definition and its own bars.

    The definition keeps the portfolio's signal: its key, and the ms-markets key of its signal,
    are the configuration's UID, as before (ADR 0028, decision 9). The portfolio's own bars
    configuration copies the profile of the one it named. A ``portfolios`` bars configuration
    that no portfolio names any more is deleted; every other one stays as its account's feed.
    """
    portfolios, bars, _, definitions = _tables()
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    rows = adoptable_portfolios(bind)
    definition_rows, bars_rows, adoptions = [], [], []
    for row in rows:
        definition_uid = uuid.uuid4()
        key = str(row["uid"])
        definition_rows.append(
            {
                "uid": definition_uid,
                "account_uid": row["account_uid"],
                "key": key,
                "signal_key": key,
                "name": row["name"],
                "description": None,
                "created_at": now,
                "updated_at": now,
            }
        )
        bars_rows.append(
            {
                "uid": uuid.uuid4(),
                "name": (row["name"] + OWN_BARS_SUFFIX)[:255],
                "description": row["bars_description"],
                "enabled": row["enabled"],
                "account_uid": None,
                "portfolio_configuration_uid": row["uid"],
                "asset_source": OWN_BARS_SOURCE,
                "frequency_id": row["frequency_id"],
                "feed": row["feed"],
                "adjustment": row["adjustment"],
                "created_at": now,
                "updated_at": now,
                "scheduled": row["scheduled"],
            }
        )
        adoptions.append(
            {
                "portfolio": row["uid"],
                "account": row["account_uid"],
                "definition": definition_uid,
            }
        )
    if adoptions:
        bind.execute(sa.insert(definitions), definition_rows)
        bind.execute(sa.insert(bars), bars_rows)
        bind.execute(
            sa.update(portfolios)
            .where(portfolios.c.uid == sa.bindparam("portfolio"))
            .values(
                account_uid=sa.bindparam("account"),
                signal_definition_uid=sa.bindparam("definition"),
                bars_configuration_uid=None,
            ),
            adoptions,
        )
    named = sa.select(portfolios.c.bars_configuration_uid).where(
        portfolios.c.bars_configuration_uid.is_not(None)
    )
    bind.execute(
        sa.delete(bars).where(
            bars.c.asset_source == OWN_BARS_SOURCE,
            bars.c.portfolio_configuration_uid.is_(None),
            bars.c.uid.not_in(named),
        )
    )
    return len(adoptions)


def _create_index(name: str, table: str, columns: list[str], unique: bool) -> None:
    op.create_index(op.f(name), table, columns, unique=unique)


def upgrade() -> None:
    """Accounts own portfolios, signals and bars (ADR 0028). Adds, and adopts the existing rows.

    Adds the signal definitions and ended portfolios, the portfolio's account and signal
    definition, and the bars configuration's portfolio parent; a bars configuration names its
    account or its portfolio, never both, and the account's key now cascades. The portfolio's
    former bars key is relaxed, not dropped: the release still serving during the rollout keeps
    writing it, and a later revision drops it. Every portfolio its own owner can own is adopted
    here; the ``portfolio adopt`` rollout command adopts any written meanwhile and ends the rest.
    """
    bind = op.get_bind()
    op.create_table(
        DEFINITION_TABLE,
        sa.Column("uid", sa.Uuid(), nullable=False),
        sa.Column("account_uid", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("signal_key", sa.String(length=512), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        *(sa.CheckConstraint(condition, name=op.f(name)) for name, condition in DEFINITION_CHECKS),
        sa.ForeignKeyConstraint(
            ["account_uid"],
            [f"{ACCOUNT_TABLE}.uid"],
            name=op.f(DEFINITION_ACCOUNT_KEY),
            ondelete="CASCADE",
        ),
        *(sa.UniqueConstraint(*columns, name=op.f(name)) for name, columns in DEFINITION_UNIQUE),
        sa.PrimaryKeyConstraint("uid", name=op.f(f"pk__{DEFINITION_TABLE}")),
        info={
            "namespace": "alpaca-connectors",
            "identifier": "alpaca-connectors.AlpacaSignalDefinition",
            "markets_storage_app": "alpaca_connectors",
        },
    )
    for name, columns, unique in DEFINITION_INDEXES:
        _create_index(name, DEFINITION_TABLE, columns, unique)
    op.create_table(
        ENDED_TABLE,
        sa.Column("uid", sa.Uuid(), nullable=False),
        sa.Column("account_uid", sa.Uuid(), nullable=False),
        sa.Column("portfolio_uid", sa.Uuid(), nullable=False),
        sa.Column("portfolio_configuration_uid", sa.Uuid(), nullable=False),
        sa.Column("signal_uid", sa.String(length=255), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("uid", name=op.f(f"pk__{ENDED_TABLE}")),
        info={
            "namespace": "alpaca-connectors",
            "identifier": "alpaca-connectors.AlpacaEndedPortfolio",
            "markets_storage_app": "alpaca_connectors",
        },
    )
    for name, columns, unique in ENDED_INDEXES:
        _create_index(name, ENDED_TABLE, columns, unique)

    op.add_column(PORTFOLIO_TABLE, sa.Column("account_uid", sa.Uuid(), nullable=True))
    op.add_column(PORTFOLIO_TABLE, sa.Column("signal_definition_uid", sa.Uuid(), nullable=True))
    portfolio_keys = {key["name"] for key in sa.inspect(bind).get_foreign_keys(PORTFOLIO_TABLE)}
    if PORTFOLIO_BARS_KEY in portfolio_keys:
        op.drop_constraint(op.f(PORTFOLIO_BARS_KEY), PORTFOLIO_TABLE, type_="foreignkey")
    op.alter_column(
        PORTFOLIO_TABLE, "bars_configuration_uid", existing_type=sa.Uuid(), nullable=True
    )
    op.create_foreign_key(
        op.f(PORTFOLIO_ACCOUNT_KEY),
        PORTFOLIO_TABLE,
        ACCOUNT_TABLE,
        ["account_uid"],
        ["uid"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f(PORTFOLIO_DEFINITION_KEY),
        PORTFOLIO_TABLE,
        DEFINITION_TABLE,
        ["signal_definition_uid", "account_uid"],
        ["uid", "account_uid"],
        ondelete="RESTRICT",
    )
    for name, columns, unique in PORTFOLIO_INDEXES:
        _create_index(name, PORTFOLIO_TABLE, columns, unique)

    op.add_column(BARS_TABLE, sa.Column("portfolio_configuration_uid", sa.Uuid(), nullable=True))
    bars_keys = {key["name"] for key in sa.inspect(bind).get_foreign_keys(BARS_TABLE)}
    if BARS_ACCOUNT_KEY in bars_keys:
        op.drop_constraint(op.f(BARS_ACCOUNT_KEY), BARS_TABLE, type_="foreignkey")
    op.alter_column(BARS_TABLE, "account_uid", existing_type=sa.Uuid(), nullable=True)
    op.create_foreign_key(
        op.f(BARS_ACCOUNT_KEY),
        BARS_TABLE,
        ACCOUNT_TABLE,
        ["account_uid"],
        ["uid"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        op.f(BARS_PORTFOLIO_KEY),
        BARS_TABLE,
        PORTFOLIO_TABLE,
        ["portfolio_configuration_uid"],
        ["uid"],
        ondelete="CASCADE",
    )
    op.create_check_constraint(op.f(BARS_ONE_PARENT[0]), BARS_TABLE, BARS_ONE_PARENT[1])
    name, columns, unique = BARS_PORTFOLIO_INDEX
    _create_index(name, BARS_TABLE, columns, unique)

    adopt_portfolios(bind)


def downgrade() -> None:
    """Put every portfolio back on a bars configuration of its account, then remove the additions.

    Each portfolio's own bars configuration becomes its account's ``portfolios`` feed again and
    the portfolio names it, so the previous release finds the shape it wrote. The signal
    definitions and ended-portfolio records are dropped: a portfolio that followed a definition
    created after this revision keeps it only in this release, and the previous release reads
    the signal keyed by the portfolio's configuration UID. A bars configuration whose portfolio
    has no account cannot be put back on one and is deleted.
    """
    bind = op.get_bind()
    portfolios, bars, _, _ = _tables()
    owner = (
        sa.select(portfolios.c.account_uid)
        .where(portfolios.c.uid == bars.c.portfolio_configuration_uid)
        .scalar_subquery()
    )
    # A configuration whose portfolio has no account has nowhere to go.
    bind.execute(
        sa.delete(bars).where(
            bars.c.portfolio_configuration_uid.is_not(None),
            owner.is_(None),
        )
    )
    owned = list(
        bind.execute(
            sa.select(bars.c.uid, bars.c.portfolio_configuration_uid).where(
                bars.c.portfolio_configuration_uid.is_not(None)
            )
        ).mappings()
    )
    if owned:
        bind.execute(
            sa.update(portfolios)
            .where(portfolios.c.uid == sa.bindparam("portfolio"))
            .values(bars_configuration_uid=sa.bindparam("bars")),
            [
                {"portfolio": row["portfolio_configuration_uid"], "bars": row["uid"]}
                for row in owned
            ],
        )
        bind.execute(
            sa.update(bars)
            .where(bars.c.portfolio_configuration_uid.is_not(None))
            .values(account_uid=owner, portfolio_configuration_uid=None)
        )

    op.drop_index(op.f(BARS_PORTFOLIO_INDEX[0]), table_name=BARS_TABLE)
    op.drop_constraint(op.f(BARS_ONE_PARENT[0]), BARS_TABLE, type_="check")
    op.drop_constraint(op.f(BARS_PORTFOLIO_KEY), BARS_TABLE, type_="foreignkey")
    op.drop_constraint(op.f(BARS_ACCOUNT_KEY), BARS_TABLE, type_="foreignkey")
    op.alter_column(BARS_TABLE, "account_uid", existing_type=sa.Uuid(), nullable=False)
    op.create_foreign_key(
        op.f(BARS_ACCOUNT_KEY),
        BARS_TABLE,
        ACCOUNT_TABLE,
        ["account_uid"],
        ["uid"],
        ondelete="RESTRICT",
    )
    op.drop_column(BARS_TABLE, "portfolio_configuration_uid")

    for name, _columns, _unique in reversed(PORTFOLIO_INDEXES):
        op.drop_index(op.f(name), table_name=PORTFOLIO_TABLE)
    op.drop_constraint(op.f(PORTFOLIO_DEFINITION_KEY), PORTFOLIO_TABLE, type_="foreignkey")
    op.drop_constraint(op.f(PORTFOLIO_ACCOUNT_KEY), PORTFOLIO_TABLE, type_="foreignkey")
    op.drop_column(PORTFOLIO_TABLE, "signal_definition_uid")
    op.drop_column(PORTFOLIO_TABLE, "account_uid")
    op.alter_column(
        PORTFOLIO_TABLE, "bars_configuration_uid", existing_type=sa.Uuid(), nullable=False
    )
    op.create_foreign_key(
        op.f(PORTFOLIO_BARS_KEY),
        PORTFOLIO_TABLE,
        BARS_TABLE,
        ["bars_configuration_uid"],
        ["uid"],
        ondelete="RESTRICT",
    )

    for name, _columns, _unique in reversed(ENDED_INDEXES):
        op.drop_index(op.f(name), table_name=ENDED_TABLE)
    op.drop_table(ENDED_TABLE)
    for name, _columns, _unique in reversed(DEFINITION_INDEXES):
        op.drop_index(op.f(name), table_name=DEFINITION_TABLE)
    op.drop_table(DEFINITION_TABLE)
