"""Durable user-maintained Alpaca stock-bar update configurations.

The configuration MetaTable is the application control plane.  It records which registered
Alpaca account supplies credentials, which asset source should be resolved at execution time, and
which already-migrated bar profile receives the observations.  Output MetaTable UIDs are never
stored here: the ``(frequency_id, feed, adjustment)`` triple deterministically selects storage.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, ClassVar, Literal

from msm.api.base import MarketsMetaTableRow, operation_result_rows
from msm.base import MarketsBase, markets_table_args, new_markets_uid
from msm.models import AccountTable
from msm.models.assets.core import AssetTable
from pydantic import ConfigDict, Field
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    delete,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from alpaca_connectors.metatables import (
    PROJECT_TABLE_APP,
    AlpacaMarketsMetaTableMixin,
    ProjectStorageNameMixin,
)
from metatables import schema_table_name

AssetSource = Literal["assets", "account_holdings", "portfolios"]
ASSET_SOURCES: tuple[AssetSource, ...] = ("assets", "account_holdings", "portfolios")
# A bars feed's sources; a portfolio's own configuration prices its portfolio (`portfolios`).
FEED_ASSET_SOURCES: tuple[AssetSource, ...] = ("assets", "account_holdings")
UTC = dt.timezone.utc
_UNSET = object()
# Named by its physical name: the portfolio tables import this module (ADR 0028, decision 4).
PORTFOLIO_CONFIGURATION_TABLE = schema_table_name(PROJECT_TABLE_APP, "portfolio_configuration")


def utc_now() -> dt.datetime:
    return dt.datetime.now(UTC).replace(microsecond=0)


class AlpacaBarsConfigurationTable(
    ProjectStorageNameMixin,
    AlpacaMarketsMetaTableMixin,
    MarketsBase,
):
    """One stock-bar update configuration with exactly one parent (ADR 0028, decision 4).

    A bars feed names its account, whose keys fetch its explicit assets or the account's
    holdings. A portfolio's own configuration names its portfolio configuration, and fetches
    that portfolio's assets with the portfolio's account's keys. Either goes with its parent.
    Every configuration writes the one shared price table of its bar profile, whose rows name no
    account.
    """

    __project_storage_concept__ = "bars_configuration"
    __markets_base_identifier__ = "AlpacaBarsConfiguration"
    __metatable_description__ = (
        "One row per stored stock price-update configuration. Saved settings for downloading "
        "stock prices (OHLCV bars) from Alpaca: either a feed of one registered Alpaca account, "
        "whose keys fetch an explicit list of assets or the account's recent holdings, or one "
        "portfolio's own configuration, which fetches every asset that portfolio's signal has "
        "published with the portfolio's account's keys; and the bar frequency, data feed and "
        "corporate-action adjustment that choose the shared price table; created by the "
        "account's owner through the Alpaca Connectors application."
    )
    __metatable_labels__ = ["equities", "prices", "configuration", "alpaca"]
    __table_args__ = markets_table_args(
        "AlpacaBarsConfiguration",
        CheckConstraint(
            "asset_source IN ('assets', 'account_holdings', 'portfolios')",
            name="ck_alpaca_bars_configuration_asset_source",
        ),
        CheckConstraint(
            "(account_uid IS NULL) <> (portfolio_configuration_uid IS NULL)",
            name="ck_alpaca_bars_configuration_one_parent",
        ),
        Index(None, "name"),
        Index(None, "enabled"),
        Index(None, "account_uid"),
        Index(None, "asset_source"),
        Index(None, "portfolio_configuration_uid", unique=True),
    )

    uid: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=new_markets_uid,
        info={
            "label": "Configuration UID",
            "description": "Public UUID that identifies the configuration and selects it to run.",
        },
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        info={"label": "Name", "description": "Display name of the configuration."},
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        info={
            "label": "Description",
            "description": "Free-text note about the configuration; null when none was given.",
        },
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        info={
            "label": "Enabled",
            "description": (
                "Whether price updates may run the configuration; a disabled one can still be "
                "reviewed."
            ),
        },
    )
    account_uid: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{AccountTable.__table__.fullname}.uid", ondelete="CASCADE"),
        nullable=True,
        info={
            "label": "Account UID",
            "description": (
                "For a bars feed, the Main Sequence account UID of the registered Alpaca "
                "brokerage account it belongs to, whose API keys authorize the price requests "
                "and, for account_holdings, whose holdings choose the assets; the feed is deleted "
                "with the account. Null for a portfolio's own configuration."
            ),
        },
    )
    portfolio_configuration_uid: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{PORTFOLIO_CONFIGURATION_TABLE}.uid", ondelete="CASCADE"),
        nullable=True,
        info={
            "label": "Portfolio configuration UID",
            "description": (
                "For a portfolio's own configuration, the UID of the portfolio configuration it "
                "belongs to and is deleted with; its runs use that portfolio's account's keys. "
                "Null for a bars feed."
            ),
        },
    )
    asset_source: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        info={
            "label": "Asset source",
            "description": (
                "How the assets to price are chosen: assets for an explicit list, "
                "account_holdings for the account's latest holdings from the past 30 days, or "
                "portfolios for every asset ever published by the signal of the portfolio the "
                "configuration belongs to."
            ),
        },
    )
    frequency_id: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        info={
            "label": "Bar frequency",
            "description": (
                "Bar interval code, such as 1d for daily bars; with the feed and adjustment it "
                "must name an existing price table, currently 1d/sip/all or 1d/iex/raw."
            ),
        },
    )
    feed: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        info={
            "label": "Data feed",
            "description": (
                "Alpaca market-data feed code: sip for the consolidated feed of all US exchanges "
                "or iex for the IEX exchange only."
            ),
        },
    )
    adjustment: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        info={
            "label": "Adjustment",
            "description": (
                "Corporate-action adjustment code: all for prices adjusted for splits, dividends "
                "and other actions, or raw for unadjusted prices."
            ),
        },
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        info={"label": "Created at", "description": "Time, in UTC, the configuration was created."},
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        info={
            "label": "Updated at",
            "description": "Time, in UTC, the configuration last changed.",
        },
    )
    scheduled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        # Revision 0024 (ADR 0021). The server default stays: a release that predates the column
        # inserts rows without it.
        server_default=true(),
        info={
            "label": "Scheduled",
            "description": (
                "Whether the daily bars update includes this configuration; an unscheduled one "
                "still runs on demand while enabled."
            ),
        },
    )


class AlpacaBarsConfigurationAssetTable(
    ProjectStorageNameMixin,
    AlpacaMarketsMetaTableMixin,
    MarketsBase,
):
    """Explicit Asset membership for configurations whose source is ``assets``."""

    __project_storage_concept__ = "bars_configuration_asset"
    __markets_base_identifier__ = "AlpacaBarsConfigurationAsset"
    __metatable_description__ = (
        "One row per asset per stock price-update configuration. The explicit list of US stocks "
        "and exchange-traded funds priced by each stored Alpaca price-update configuration that "
        "chooses its assets by list, maintained with the configuration by operators of the "
        "Alpaca Connectors application."
    )
    __metatable_labels__ = ["equities", "prices", "configuration", "alpaca"]
    __table_args__ = markets_table_args(
        "AlpacaBarsConfigurationAsset",
        Index(None, "asset_uid"),
    )

    configuration_uid: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{AlpacaBarsConfigurationTable.__table__.fullname}.uid", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
        info={
            "label": "Configuration UID",
            "description": "UID of the price-update configuration that lists the asset.",
        },
    )
    asset_uid: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{AssetTable.__table__.fullname}.uid", ondelete="RESTRICT"),
        primary_key=True,
        nullable=False,
        info={
            "label": "Asset UID",
            "description": (
                "Main Sequence asset UID of a stock or exchange-traded fund the configuration "
                "prices."
            ),
        },
    )


class AlpacaBarsConfiguration(MarketsMetaTableRow):
    """Typed configuration row enriched with explicit Asset memberships."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    __table__: ClassVar[type[AlpacaBarsConfigurationTable]] = AlpacaBarsConfigurationTable
    __required_tables__: ClassVar[list[type[MarketsBase]]] = [
        AlpacaBarsConfigurationTable,
        AlpacaBarsConfigurationAssetTable,
    ]

    name: str
    description: str | None
    enabled: bool
    account_uid: uuid.UUID | None = None
    portfolio_configuration_uid: uuid.UUID | None = None
    asset_source: AssetSource
    frequency_id: str
    feed: str
    adjustment: str
    created_at: dt.datetime
    updated_at: dt.datetime
    scheduled: bool
    asset_uids: list[uuid.UUID] = Field(default_factory=list)


def _normalize_name(value: str) -> str:
    normalized = (value or "").strip()
    if not normalized:
        raise ValueError("Bar configuration name must not be empty.")
    if len(normalized) > 255:
        raise ValueError("Bar configuration name must be at most 255 characters.")
    return normalized


def _normalize_description(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def validate_configuration_scope(
    *,
    asset_source: str,
    asset_uids: list[uuid.UUID | str] | None,
    portfolio_owned: bool = False,
) -> tuple[AssetSource, list[uuid.UUID]]:
    """Normalize and validate the mutually exclusive source shapes.

    A bars feed of an account prices explicit assets or the account's holdings; a portfolio's own
    configuration prices that portfolio's assets, the ``portfolios`` source (ADR 0028).
    """
    normalized_source = asset_source.strip().lower()
    if normalized_source not in ASSET_SOURCES:
        raise ValueError(
            f"Unsupported asset_source {asset_source!r}. Expected one of {list(ASSET_SOURCES)!r}."
        )
    if portfolio_owned and normalized_source != "portfolios":
        raise ValueError("A portfolio's own bars configuration prices its portfolio's assets.")
    if not portfolio_owned and normalized_source not in FEED_ASSET_SOURCES:
        raise ValueError(
            "A bars feed prices explicit assets or its account's holdings; a portfolio's "
            "own bars configuration is created with the portfolio."
        )
    normalized_asset_uids = list(
        dict.fromkeys(uuid.UUID(str(asset_uid)) for asset_uid in (asset_uids or []))
    )
    if normalized_source == "assets":
        if not normalized_asset_uids:
            raise ValueError("assets source requires asset_uids.")
    elif normalized_asset_uids:
        raise ValueError(f"{normalized_source} source forbids asset_uids.")
    return normalized_source, normalized_asset_uids  # type: ignore[return-value]


def _validate_references(
    *,
    account_uid: uuid.UUID,
    asset_uids: list[uuid.UUID],
) -> None:
    from alpaca_connectors.account.services import get_account_registration
    from alpaca_connectors.assets.resolution import assets_by_uids

    if get_account_registration(account_uid) is None:
        raise LookupError(f"Alpaca account registration {account_uid!s} does not exist.")
    existing_assets = assets_by_uids(asset_uids)
    missing_assets = [
        str(asset_uid) for asset_uid in asset_uids if str(asset_uid) not in existing_assets
    ]
    if missing_assets:
        raise LookupError(f"These Asset UIDs do not exist: {sorted(missing_assets)!r}")


def bar_configuration_account_uid(configuration: AlpacaBarsConfiguration) -> uuid.UUID:
    """The account whose keys run the configuration: a feed's own, or its portfolio's account."""
    if configuration.account_uid is not None:
        return uuid.UUID(str(configuration.account_uid))
    from alpaca_connectors.portfolios.configurations import get_portfolio_configuration

    portfolio = get_portfolio_configuration(configuration.portfolio_configuration_uid)
    if portfolio is None or portfolio.account_uid is None:
        raise LookupError(
            f"Bar configuration {configuration.uid!s} belongs to a portfolio without an account."
        )
    return uuid.UUID(str(portfolio.account_uid))


def portfolio_bar_configuration(
    portfolio_configuration_uid: uuid.UUID | str,
) -> AlpacaBarsConfiguration | None:
    """The portfolio's own bars configuration (ADR 0028, decision 4)."""
    from alpaca_connectors.runtime import account_runtime_models, start_markets_engine

    start_markets_engine(models=account_runtime_models())
    rows = AlpacaBarsConfiguration.filter(
        portfolio_configuration_uid=uuid.UUID(str(portfolio_configuration_uid)), limit=1
    )
    return _with_memberships(rows[0]) if rows else None


def portfolio_bar_configurations(
    portfolio_configuration_uids: list[uuid.UUID | str],
) -> dict[str, AlpacaBarsConfiguration]:
    """The own bars configurations of several portfolios, by portfolio configuration UID."""
    from alpaca_connectors.markets_api import read_in_batches
    from alpaca_connectors.runtime import account_runtime_models, start_markets_engine

    uids = sorted({str(uuid.UUID(str(uid))) for uid in portfolio_configuration_uids})
    if not uids:
        return {}
    start_markets_engine(models=account_runtime_models())
    rows = read_in_batches(
        lambda batch: AlpacaBarsConfiguration.filter(
            portfolio_configuration_uid__in=batch, limit=len(batch)
        ),
        uids,
    )
    return {str(row.portfolio_configuration_uid): row for row in rows}


def bar_configurations_for_account(
    account_uid: uuid.UUID | str,
) -> list[AlpacaBarsConfiguration]:
    """Return the account's bars feeds, which run with its keys and go with it."""
    from alpaca_connectors.runtime import account_runtime_models, start_markets_engine

    start_markets_engine(models=account_runtime_models())
    return sorted(
        AlpacaBarsConfiguration.filter(account_uid=str(account_uid), limit=10_000),
        key=lambda configuration: (configuration.name, str(configuration.uid)),
    )


def disable_bar_configuration(configuration_uid: uuid.UUID | str) -> AlpacaBarsConfiguration:
    """Stop every run of the configuration, writing only ``enabled`` (ADR 0022).

    The removal of the account it runs with calls this, so nothing the configuration references
    is validated.
    """
    return AlpacaBarsConfiguration.update(
        configuration_uid,
        {"enabled": False, "updated_at": utc_now()},
    )


def _configuration_asset_uids(configuration_uid: uuid.UUID | str) -> list[uuid.UUID]:
    return _configuration_asset_uids_by_configuration([configuration_uid]).get(
        str(configuration_uid),
        [],
    )


def _configuration_asset_uids_by_configuration(
    configuration_uids: list[uuid.UUID | str],
) -> dict[str, list[uuid.UUID]]:
    from msm.bootstrap import resolve_runtime
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import select

    normalized_configuration_uids = list(
        dict.fromkeys(uuid.UUID(str(configuration_uid)) for configuration_uid in configuration_uids)
    )
    if not normalized_configuration_uids:
        return {}
    runtime = resolve_runtime(
        models=[AlpacaBarsConfigurationAssetTable],
        row_model_name="AlpacaBarsConfigurationAsset",
    )
    statement = (
        select(
            AlpacaBarsConfigurationAssetTable.configuration_uid,
            AlpacaBarsConfigurationAssetTable.asset_uid,
        )
        .where(
            AlpacaBarsConfigurationAssetTable.configuration_uid.in_(normalized_configuration_uids)
        )
        .order_by(
            AlpacaBarsConfigurationAssetTable.configuration_uid.asc(),
            AlpacaBarsConfigurationAssetTable.asset_uid.asc(),
        )
    )
    operation = compile_markets_statement(
        statement,
        context=runtime.context,
        operation="select",
    )
    rows = operation_result_rows(execute_markets_operation(operation, context=runtime.context))
    memberships = {
        str(configuration_uid): [] for configuration_uid in normalized_configuration_uids
    }
    for row in rows:
        memberships[str(row["configuration_uid"])].append(uuid.UUID(str(row["asset_uid"])))
    return memberships


def _with_memberships(
    row: AlpacaBarsConfiguration,
    *,
    asset_uids: list[uuid.UUID] | None = None,
) -> AlpacaBarsConfiguration:
    return row.model_copy(
        update={
            "asset_uids": (_configuration_asset_uids(row.uid) if asset_uids is None else asset_uids)
        }
    )


def _replace_memberships(
    configuration_uid: uuid.UUID | str,
    asset_uids: list[uuid.UUID],
) -> None:
    from msm.api.base import operation_result_rows
    from msm.bootstrap import resolve_runtime
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from msm.repositories.crud import bulk_upsert_model

    normalized_configuration_uid = uuid.UUID(str(configuration_uid))
    normalized_asset_uids = list(dict.fromkeys(asset_uids))
    runtime = resolve_runtime(
        models=[AlpacaBarsConfigurationAssetTable],
        row_model_name="AlpacaBarsConfigurationAsset",
    )
    if normalized_asset_uids:
        upsert_result = bulk_upsert_model(
            runtime.context,
            model=AlpacaBarsConfigurationAssetTable,
            values=[
                {
                    "configuration_uid": normalized_configuration_uid,
                    "asset_uid": asset_uid,
                }
                for asset_uid in normalized_asset_uids
            ],
            conflict_columns=("configuration_uid", "asset_uid"),
        )
        returned_asset_uids = {
            uuid.UUID(str(row["asset_uid"]))
            for row in operation_result_rows(upsert_result)
            if row.get("asset_uid") is not None
        }
        missing_asset_uids = set(normalized_asset_uids) - returned_asset_uids
        if missing_asset_uids:
            missing = ", ".join(str(asset_uid) for asset_uid in sorted(missing_asset_uids))
            raise RuntimeError(
                "Bulk bar-configuration membership upsert returned an incomplete result for: "
                f"{missing}. Existing memberships were not removed."
            )

    stale_memberships = delete(AlpacaBarsConfigurationAssetTable).where(
        AlpacaBarsConfigurationAssetTable.configuration_uid == normalized_configuration_uid
    )
    if normalized_asset_uids:
        stale_memberships = stale_memberships.where(
            AlpacaBarsConfigurationAssetTable.asset_uid.notin_(normalized_asset_uids)
        )
    delete_operation = compile_markets_statement(
        stale_memberships,
        context=runtime.context,
        operation="delete",
    )
    execute_markets_operation(delete_operation, context=runtime.context)


def create_bar_configuration(
    *,
    name: str,
    account_uid: uuid.UUID | str,
    asset_source: str,
    frequency_id: str,
    feed: str,
    adjustment: str,
    asset_uids: list[uuid.UUID | str] | None = None,
    description: str | None = None,
    enabled: bool = True,
    scheduled: bool = True,
    uid: uuid.UUID | str | None = None,
) -> AlpacaBarsConfiguration:
    from msm.bootstrap import resolve_runtime
    from msm.repositories.crud import create_model

    from alpaca_connectors.market_data.alpaca_bars_support import (
        normalize_adjustment,
        normalize_feed,
        normalize_frequency_id,
    )
    from alpaca_connectors.market_data.storage import storage_for
    from alpaca_connectors.runtime import account_runtime_models, start_markets_engine

    source, normalized_asset_uids = validate_configuration_scope(
        asset_source=asset_source,
        asset_uids=asset_uids,
    )
    normalized_account_uid = uuid.UUID(str(account_uid))
    normalized_frequency = normalize_frequency_id(frequency_id)
    normalized_feed = normalize_feed(feed)
    normalized_adjustment = normalize_adjustment(adjustment)
    storage_for(normalized_frequency, normalized_feed, normalized_adjustment)
    start_markets_engine(models=account_runtime_models())
    _validate_references(account_uid=normalized_account_uid, asset_uids=normalized_asset_uids)
    now = utc_now()
    values: dict[str, Any] = {
        "uid": uuid.UUID(str(uid)) if uid is not None else new_markets_uid(),
        "name": _normalize_name(name),
        "description": _normalize_description(description),
        "enabled": bool(enabled),
        "scheduled": bool(scheduled),
        "account_uid": normalized_account_uid,
        "asset_source": source,
        "frequency_id": normalized_frequency,
        "feed": normalized_feed,
        "adjustment": normalized_adjustment,
        "created_at": now,
        "updated_at": now,
    }
    runtime = resolve_runtime(
        models=[AlpacaBarsConfigurationTable, AlpacaBarsConfigurationAssetTable],
        row_model_name="AlpacaBarsConfiguration",
    )
    result = create_model(runtime.context, model=AlpacaBarsConfigurationTable, values=values)
    rows = operation_result_rows(result)
    if not rows:
        raise RuntimeError("Bar configuration create returned no row.")
    row = AlpacaBarsConfiguration.model_validate(rows[0])
    _replace_memberships(row.uid, normalized_asset_uids)
    return _with_memberships(row)


def create_portfolio_bar_configuration(
    *,
    portfolio_configuration_uid: uuid.UUID | str,
    name: str,
    frequency_id: str,
    feed: str,
    adjustment: str,
    description: str | None = None,
    enabled: bool = True,
    scheduled: bool = True,
) -> AlpacaBarsConfiguration:
    """Store a portfolio's own bars configuration (ADR 0028, decision 4).

    It names the portfolio configuration, not an account: its runs use the portfolio's account's
    keys, and it is deleted with the portfolio. It prices every asset the portfolio's signal has
    published, into the shared table of its bar profile.
    """
    from msm.bootstrap import resolve_runtime
    from msm.repositories.crud import create_model

    from alpaca_connectors.market_data.alpaca_bars_support import (
        normalize_adjustment,
        normalize_feed,
        normalize_frequency_id,
    )
    from alpaca_connectors.market_data.storage import storage_for
    from alpaca_connectors.runtime import account_runtime_models, start_markets_engine

    normalized_frequency = normalize_frequency_id(frequency_id)
    normalized_feed = normalize_feed(feed)
    normalized_adjustment = normalize_adjustment(adjustment)
    storage_for(normalized_frequency, normalized_feed, normalized_adjustment)
    start_markets_engine(models=account_runtime_models())
    now = utc_now()
    values: dict[str, Any] = {
        "uid": new_markets_uid(),
        "name": _normalize_name(name),
        "description": _normalize_description(description),
        "enabled": bool(enabled),
        "scheduled": bool(scheduled),
        "account_uid": None,
        "portfolio_configuration_uid": uuid.UUID(str(portfolio_configuration_uid)),
        "asset_source": "portfolios",
        "frequency_id": normalized_frequency,
        "feed": normalized_feed,
        "adjustment": normalized_adjustment,
        "created_at": now,
        "updated_at": now,
    }
    runtime = resolve_runtime(
        models=[AlpacaBarsConfigurationTable, AlpacaBarsConfigurationAssetTable],
        row_model_name="AlpacaBarsConfiguration",
    )
    result = create_model(runtime.context, model=AlpacaBarsConfigurationTable, values=values)
    rows = operation_result_rows(result)
    if not rows:
        raise RuntimeError("Bar configuration create returned no row.")
    return _with_memberships(AlpacaBarsConfiguration.model_validate(rows[0]), asset_uids=[])


def get_bar_configuration(
    configuration_uid: uuid.UUID | str,
) -> AlpacaBarsConfiguration | None:
    from alpaca_connectors.runtime import account_runtime_models, start_markets_engine

    start_markets_engine(models=account_runtime_models())
    row = AlpacaBarsConfiguration.get_by_uid(configuration_uid)
    return _with_memberships(row) if row is not None else None


def list_bar_configurations(
    *,
    limit: int = 25,
    offset: int = 0,
    search: str | None = None,
    enabled: bool | None = None,
    scheduled: bool | None = None,
    asset_source: str | None = None,
    account_uids: list[uuid.UUID | str] | None = None,
    portfolio_owned: bool | None = None,
    ordering: str = "name",
) -> tuple[list[AlpacaBarsConfiguration], int]:
    from msm.bootstrap import resolve_runtime
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import func, or_, select

    from alpaca_connectors.runtime import account_runtime_models, start_markets_engine

    start_markets_engine(models=account_runtime_models())
    runtime = resolve_runtime(
        models=[AlpacaBarsConfigurationTable, AlpacaBarsConfigurationAssetTable],
        row_model_name="AlpacaBarsConfiguration",
    )
    statement = select(AlpacaBarsConfigurationTable)
    if enabled is not None:
        statement = statement.where(AlpacaBarsConfigurationTable.enabled == enabled)
    if scheduled is not None:
        statement = statement.where(AlpacaBarsConfigurationTable.scheduled == scheduled)
    if asset_source is not None:
        normalized_source = asset_source.strip().lower()
        if normalized_source not in ASSET_SOURCES:
            raise ValueError(f"Unsupported asset_source filter {asset_source!r}.")
        statement = statement.where(AlpacaBarsConfigurationTable.asset_source == normalized_source)
    if account_uids is not None:
        accounts = [uuid.UUID(str(uid)) for uid in account_uids]
        if not accounts:
            return [], 0
        statement = statement.where(AlpacaBarsConfigurationTable.account_uid.in_(accounts))
    if portfolio_owned is not None:
        column = AlpacaBarsConfigurationTable.portfolio_configuration_uid
        statement = statement.where(column.is_not(None) if portfolio_owned else column.is_(None))
    if search:
        pattern = f"%{search.strip()}%"
        statement = statement.where(
            or_(
                AlpacaBarsConfigurationTable.name.ilike(pattern),
                AlpacaBarsConfigurationTable.description.ilike(pattern),
            )
        )
    count_statement = select(func.count().label("count")).select_from(
        statement.order_by(None).subquery()
    )
    descending = ordering.startswith("-")
    ordering_key = ordering.removeprefix("-")
    ordering_columns = {
        "name": AlpacaBarsConfigurationTable.name,
        "updated_at": AlpacaBarsConfigurationTable.updated_at,
        "frequency_id": AlpacaBarsConfigurationTable.frequency_id,
    }
    if ordering_key not in ordering_columns:
        raise ValueError(f"Unsupported bar-configuration ordering {ordering!r}.")
    ordering_column = ordering_columns[ordering_key]
    ordering_expression = ordering_column.desc() if descending else ordering_column.asc()
    statement = statement.order_by(ordering_expression, AlpacaBarsConfigurationTable.uid.asc())
    page_operation = compile_markets_statement(
        statement.limit(limit).offset(offset),
        context=runtime.context,
        operation="select",
    )
    count_operation = compile_markets_statement(
        count_statement,
        context=runtime.context,
        operation="select",
    )
    rows = [
        AlpacaBarsConfiguration.model_validate(row)
        for row in operation_result_rows(
            execute_markets_operation(page_operation, context=runtime.context)
        )
    ]
    memberships = _configuration_asset_uids_by_configuration([row.uid for row in rows])
    rows = [_with_memberships(row, asset_uids=memberships.get(str(row.uid), [])) for row in rows]
    count_rows = operation_result_rows(
        execute_markets_operation(count_operation, context=runtime.context)
    )
    total = int(count_rows[0].get("count", 0)) if count_rows else 0
    return rows, total


def scheduled_bar_configuration_uids() -> list[uuid.UUID]:
    """Return the configurations the daily bars dispatcher's scheduled run includes.

    A configuration is updated daily when it is both enabled and scheduled (ADR 0021): an
    unscheduled one still runs on demand, and a disabled one runs neither way. A ``portfolios``
    feed that only portfolios not yet adopted name prices nothing and is left out (ADR 0028).
    Ordered by name, then UID.
    """
    from msm.bootstrap import resolve_runtime
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import or_, select

    from alpaca_connectors.runtime import account_runtime_models, start_markets_engine

    table = AlpacaBarsConfigurationTable
    start_markets_engine(models=account_runtime_models())
    runtime = resolve_runtime(models=[table], row_model_name="AlpacaBarsConfiguration")
    operation = compile_markets_statement(
        select(table.uid)
        .where(
            table.enabled.is_(True),
            table.scheduled.is_(True),
            or_(table.asset_source != "portfolios", table.portfolio_configuration_uid.is_not(None)),
        )
        .order_by(table.name.asc(), table.uid.asc()),
        context=runtime.context,
        operation="select",
    )
    return [
        uuid.UUID(str(row["uid"]))
        for row in operation_result_rows(
            execute_markets_operation(operation, context=runtime.context)
        )
    ]


def update_bar_configuration(
    configuration_uid: uuid.UUID | str,
    *,
    name: str | None = None,
    description: str | None | object = _UNSET,
    enabled: bool | None = None,
    scheduled: bool | None = None,
    account_uid: uuid.UUID | str | None = None,
    asset_source: str | None = None,
    asset_uids: list[uuid.UUID | str] | None | object = _UNSET,
    frequency_id: str | None = None,
    feed: str | None = None,
    adjustment: str | None = None,
) -> AlpacaBarsConfiguration:
    """Update the configuration's fields, its flags, or both.

    Changing only ``scheduled`` skips the scope and reference validation, so a configuration
    whose account is gone can still be left out of the daily run (as for portfolios). A
    portfolio's own configuration keeps its portfolio and its ``portfolios`` source; only its
    name, description, flags and bar profile change.
    """
    from alpaca_connectors.market_data.alpaca_bars_support import (
        normalize_adjustment,
        normalize_feed,
        normalize_frequency_id,
    )
    from alpaca_connectors.market_data.storage import storage_for

    current = get_bar_configuration(configuration_uid)
    if current is None:
        raise LookupError(f"Bar configuration {configuration_uid!s} does not exist.")
    other_fields = (
        name,
        enabled,
        account_uid,
        asset_source,
        frequency_id,
        feed,
        adjustment,
    )
    if (
        scheduled is not None
        and all(value is None for value in other_fields)
        and description is _UNSET
        and asset_uids is _UNSET
    ):
        row = AlpacaBarsConfiguration.update(
            configuration_uid,
            {"scheduled": bool(scheduled), "updated_at": utc_now()},
        )
        return _with_memberships(row, asset_uids=list(current.asset_uids))
    portfolio_owned = current.portfolio_configuration_uid is not None
    if portfolio_owned and account_uid is not None:
        raise ValueError("A portfolio's own bars configuration uses its portfolio's account.")
    requested_source = asset_source if asset_source is not None else current.asset_source
    if asset_source is None and asset_uids is _UNSET:
        requested_asset_uids = list(current.asset_uids)
    else:
        requested_asset_uids = None if asset_uids is _UNSET else asset_uids
    source, normalized_asset_uids = validate_configuration_scope(
        asset_source=requested_source,
        asset_uids=requested_asset_uids,
        portfolio_owned=portfolio_owned,
    )
    normalized_frequency = normalize_frequency_id(frequency_id or current.frequency_id)
    normalized_feed = normalize_feed(feed or current.feed)
    normalized_adjustment = normalize_adjustment(adjustment or current.adjustment)
    storage_for(normalized_frequency, normalized_feed, normalized_adjustment)
    values: dict[str, Any] = {}
    if not portfolio_owned:
        normalized_account_uid = uuid.UUID(str(account_uid or current.account_uid))
        _validate_references(account_uid=normalized_account_uid, asset_uids=normalized_asset_uids)
        values["account_uid"] = normalized_account_uid
    values |= {
        "asset_source": source,
        "frequency_id": normalized_frequency,
        "feed": normalized_feed,
        "adjustment": normalized_adjustment,
        "updated_at": utc_now(),
    }
    if name is not None:
        values["name"] = _normalize_name(name)
    if description is not _UNSET:
        values["description"] = _normalize_description(
            description if isinstance(description, str) else None
        )
    if enabled is not None:
        values["enabled"] = bool(enabled)
    if scheduled is not None:
        values["scheduled"] = bool(scheduled)
    row = AlpacaBarsConfiguration.update(configuration_uid, values)
    _replace_memberships(configuration_uid, normalized_asset_uids)
    return _with_memberships(row)


def delete_bar_configuration(configuration_uid: uuid.UUID | str) -> dict[str, Any]:
    """Delete a bars feed; a portfolio's own configuration goes only with its portfolio."""
    current = get_bar_configuration(configuration_uid)
    if current is None:
        raise LookupError(f"Bar configuration {configuration_uid!s} does not exist.")
    if current.portfolio_configuration_uid is not None:
        raise ValueError(
            "This bars configuration belongs to a portfolio; it is deleted with the portfolio."
        )
    return AlpacaBarsConfiguration.delete(configuration_uid)


def project_configuration_models() -> list[type[MarketsBase]]:
    return [AlpacaBarsConfigurationTable, AlpacaBarsConfigurationAssetTable]


__all__ = [
    "ASSET_SOURCES",
    "AlpacaBarsConfiguration",
    "AlpacaBarsConfigurationAssetTable",
    "AlpacaBarsConfigurationTable",
    "AssetSource",
    "FEED_ASSET_SOURCES",
    "bar_configuration_account_uid",
    "create_bar_configuration",
    "create_portfolio_bar_configuration",
    "bar_configurations_for_account",
    "delete_bar_configuration",
    "disable_bar_configuration",
    "get_bar_configuration",
    "list_bar_configurations",
    "portfolio_bar_configuration",
    "portfolio_bar_configurations",
    "project_configuration_models",
    "scheduled_bar_configuration_uids",
    "update_bar_configuration",
    "validate_configuration_scope",
]
