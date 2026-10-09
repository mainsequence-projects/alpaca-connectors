"""Storage-first MetaTable classes for Alpaca OHLCV stock bars.

Table identity (one physical table per ``(frequency_id, feed, adjustment)`` triple) mirrors the
old behavior, where the node minted a distinct table per triple via
``DataNodeMetaData(identifier="alpaca_stock_bars_{freq}_{feed}_{adj}")``. Here that identity is
encoded by a dedicated storage class whose ``__metatable_identifier__`` is the *same* legacy
string, so the registered DataNode identifier (``alpaca_stock_bars_1d_sip_all``) is preserved.

To add another supported triple:
  1. add a storage class mirroring ``AlpacaStockBars1dSipAllStorage`` with its own
     ``__metatable_identifier__ = "alpaca_stock_bars_<freq>_<feed>_<adj>"``;
  2. register it in ``ALPACA_STOCK_BARS_STORAGE_BY_TRIPLE``;
  3. generate + run a migration revision so the table exists before any write.
"""

from __future__ import annotations

import datetime
from typing import ClassVar

from msm.base import MarketsBase, MarketsTimeIndexMetaTableMixin
from msm.models.assets.core import AssetTable
from msm.settings import ASSET_IDENTIFIER_DIMENSION
from sqlalchemy import DateTime, Float, ForeignKey, MetaData, String
from sqlalchemy.orm import Mapped, mapped_column

from metatables import schema_table_name
from metatables.migrations import metadata_for_models

# Project-owned SQLAlchemy table-name segment so physical tables are namespaced to this project
# instead of the library default ``ms_markets``. This only affects physical table names; the
# logical ``__metatable_identifier__`` remains the globally unique runtime identity.
ALPACA_CONNECTORS_STORAGE_APP = "alpaca_connectors"

_ASSET_IDENTIFIER_FK = f"{AssetTable.__table__.fullname}.unique_identifier"
_BARS_1D_SIP_ALL_TABLE_NAME = schema_table_name(
    ALPACA_CONNECTORS_STORAGE_APP,
    "bars_1d_sip_all",
)
_BARS_1D_IEX_RAW_TABLE_NAME = schema_table_name(
    ALPACA_CONNECTORS_STORAGE_APP,
    "bars_1d_iex_raw",
)

_BAR_LABELS = ["equities", "us", "prices", "alpaca"]


def _daily_bar_column_info(*, price_basis: str, volume_basis: str) -> dict[str, dict[str, str]]:
    """Column ``info`` of one daily bars table, worded for its corporate-action adjustment.

    Each call returns new dicts because every table's columns need their own ``info``.
    """

    def price(meaning: str) -> str:
        return f"{meaning}, in USD per share, {price_basis}; null when Alpaca reports none."

    return {
        "time_index": {
            "label": "Bar close time",
            "description": (
                "Scheduled 16:00 America/New_York close of the trading day the bar covers, "
                "stored in UTC, also on early-close days."
            ),
        },
        "open_time": {
            "label": "Bar open time",
            "description": (
                "Scheduled 09:30 America/New_York open of the trading day the bar covers, "
                "stored in UTC."
            ),
        },
        "asset_identifier": {
            "label": "Asset identifier",
            "description": (
                "Main Sequence unique identifier of the stock or ETF, ALPACA:: followed by its "
                "Alpaca asset UUID."
            ),
        },
        "open": {"label": "Open", "description": price("Opening price of the day")},
        "high": {"label": "High", "description": price("Highest price of the day")},
        "low": {"label": "Low", "description": price("Lowest price of the day")},
        "close": {"label": "Close", "description": price("Closing price of the day")},
        "volume": {
            "label": "Volume",
            "description": (
                f"Number of shares the feed reported traded during the day, {volume_basis}; "
                "null when Alpaca reports none."
            ),
        },
        "trade_count": {
            "label": "Trade count",
            "description": (
                "Number of trades the feed reported during the day; null when Alpaca reports none."
            ),
        },
        "vwap": {"label": "VWAP", "description": price("Volume-weighted average price of the day")},
    }


_SIP_ALL_COLUMN_INFO = _daily_bar_column_info(
    price_basis="adjusted for splits, dividends and other corporate actions",
    volume_basis="adjusted for splits",
)
_IEX_RAW_COLUMN_INFO = _daily_bar_column_info(
    price_basis="not adjusted for corporate actions",
    volume_basis="not adjusted for splits",
)


class AlpacaStockBars1dSipAllStorage(MarketsTimeIndexMetaTableMixin, MarketsBase):
    """Daily (1d) SIP-feed, all-adjustment Alpaca US equity OHLCV bars."""

    __markets_storage_app__ = ALPACA_CONNECTORS_STORAGE_APP
    __tablename__ = _BARS_1D_SIP_ALL_TABLE_NAME
    __metatable_identifier__ = "alpaca_stock_bars_1d_sip_all"
    __metatable_extra_hash_components__ = {
        "feed": "sip",
        "adjustment": "all",
    }
    __metatable_description__ = (
        "One row per asset per US trading day. Daily stock prices (OHLCV bars) of US stocks "
        "and ETFs, adjusted for splits, dividends and other corporate actions, from Alpaca's "
        "consolidated SIP market-data feed for the assets the stored price-update "
        "configurations select. A day's bar is written by the first update that runs after the "
        "following 00:00 UTC."
    )
    __metatable_labels__ = _BAR_LABELS
    __time_index_name__: ClassVar[str] = "time_index"
    __cadence__: ClassVar[str] = "1d"
    __index_names__: ClassVar[list[str]] = ["time_index", ASSET_IDENTIFIER_DIMENSION]

    time_index: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, info=_SIP_ALL_COLUMN_INFO["time_index"]
    )
    open_time: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, info=_SIP_ALL_COLUMN_INFO["open_time"]
    )
    asset_identifier: Mapped[str] = mapped_column(
        String(255),
        ForeignKey(_ASSET_IDENTIFIER_FK, ondelete="RESTRICT"),
        nullable=False,
        info=_SIP_ALL_COLUMN_INFO["asset_identifier"],
    )
    open: Mapped[float | None] = mapped_column(
        Float, nullable=True, info=_SIP_ALL_COLUMN_INFO["open"]
    )
    high: Mapped[float | None] = mapped_column(
        Float, nullable=True, info=_SIP_ALL_COLUMN_INFO["high"]
    )
    low: Mapped[float | None] = mapped_column(
        Float, nullable=True, info=_SIP_ALL_COLUMN_INFO["low"]
    )
    close: Mapped[float | None] = mapped_column(
        Float, nullable=True, info=_SIP_ALL_COLUMN_INFO["close"]
    )
    volume: Mapped[float | None] = mapped_column(
        Float, nullable=True, info=_SIP_ALL_COLUMN_INFO["volume"]
    )
    trade_count: Mapped[float | None] = mapped_column(
        Float, nullable=True, info=_SIP_ALL_COLUMN_INFO["trade_count"]
    )
    vwap: Mapped[float | None] = mapped_column(
        Float, nullable=True, info=_SIP_ALL_COLUMN_INFO["vwap"]
    )


class AlpacaStockBars1dIexRawStorage(MarketsTimeIndexMetaTableMixin, MarketsBase):
    """Daily (1d) IEX-feed, raw-adjustment Alpaca US equity OHLCV bars."""

    __markets_storage_app__ = ALPACA_CONNECTORS_STORAGE_APP
    __tablename__ = _BARS_1D_IEX_RAW_TABLE_NAME
    __metatable_identifier__ = "alpaca_stock_bars_1d_iex_raw"
    __metatable_extra_hash_components__ = {
        "feed": "iex",
        "adjustment": "raw",
    }
    __metatable_description__ = (
        "One row per asset per US trading day. Daily stock prices (OHLCV bars) of US stocks "
        "and ETFs, not adjusted for corporate actions, from Alpaca's IEX market-data feed, "
        "which covers trades on the IEX exchange only, for the assets the stored price-update "
        "configurations select. A day's bar is written by the first update that runs after the "
        "following 00:00 UTC."
    )
    __metatable_labels__ = _BAR_LABELS
    __time_index_name__: ClassVar[str] = "time_index"
    __cadence__: ClassVar[str] = "1d"
    __index_names__: ClassVar[list[str]] = ["time_index", ASSET_IDENTIFIER_DIMENSION]

    time_index: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, info=_IEX_RAW_COLUMN_INFO["time_index"]
    )
    open_time: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, info=_IEX_RAW_COLUMN_INFO["open_time"]
    )
    asset_identifier: Mapped[str] = mapped_column(
        String(255),
        ForeignKey(_ASSET_IDENTIFIER_FK, ondelete="RESTRICT"),
        nullable=False,
        info=_IEX_RAW_COLUMN_INFO["asset_identifier"],
    )
    open: Mapped[float | None] = mapped_column(
        Float, nullable=True, info=_IEX_RAW_COLUMN_INFO["open"]
    )
    high: Mapped[float | None] = mapped_column(
        Float, nullable=True, info=_IEX_RAW_COLUMN_INFO["high"]
    )
    low: Mapped[float | None] = mapped_column(
        Float, nullable=True, info=_IEX_RAW_COLUMN_INFO["low"]
    )
    close: Mapped[float | None] = mapped_column(
        Float, nullable=True, info=_IEX_RAW_COLUMN_INFO["close"]
    )
    volume: Mapped[float | None] = mapped_column(
        Float, nullable=True, info=_IEX_RAW_COLUMN_INFO["volume"]
    )
    trade_count: Mapped[float | None] = mapped_column(
        Float, nullable=True, info=_IEX_RAW_COLUMN_INFO["trade_count"]
    )
    vwap: Mapped[float | None] = mapped_column(
        Float, nullable=True, info=_IEX_RAW_COLUMN_INFO["vwap"]
    )


# Registered (frequency_id, feed, adjustment) -> storage class. Add more triples deliberately
# (see module docstring).
ALPACA_STOCK_BARS_STORAGE_BY_TRIPLE: dict[
    tuple[str, str, str], type[MarketsTimeIndexMetaTableMixin]
] = {
    ("1d", "sip", "all"): AlpacaStockBars1dSipAllStorage,
    ("1d", "iex", "raw"): AlpacaStockBars1dIexRawStorage,
}


def storage_for(
    frequency_id: str, feed: str, adjustment: str
) -> type[MarketsTimeIndexMetaTableMixin]:
    """Return the storage class for a normalized ``(frequency_id, feed, adjustment)`` triple.

    Raises a clear ``ValueError`` for an unregistered triple so callers fail loudly instead of
    silently writing into the wrong table. Register a new storage class + migration first.
    """
    key = (frequency_id, feed, adjustment)
    storage = ALPACA_STOCK_BARS_STORAGE_BY_TRIPLE.get(key)
    if storage is None:
        supported = ", ".join(
            "/".join(triple) for triple in sorted(ALPACA_STOCK_BARS_STORAGE_BY_TRIPLE)
        )
        raise ValueError(
            f"No Alpaca stock bars storage registered for frequency/feed/adjustment "
            f"{frequency_id!r}/{feed!r}/{adjustment!r}. Supported: {supported}. Add a storage "
            "class + migration before publishing this triple."
        )
    return storage


def project_storage_models() -> list[type[MarketsTimeIndexMetaTableMixin]]:
    """All project-owned storage classes (for the migration provider + runtime attach)."""
    return list(dict.fromkeys(ALPACA_STOCK_BARS_STORAGE_BY_TRIPLE.values()))


def storage_metadata() -> MetaData:
    """SQLAlchemy metadata containing only the project storage tables (migration target)."""
    return metadata_for_models(project_storage_models())


# Stable migration target metadata (only the project-owned storage tables). Referenced by the
# migration provider as ``alpaca_connectors.market_data.storage:METADATA``.
METADATA: MetaData = storage_metadata()


__all__ = [
    "ALPACA_CONNECTORS_STORAGE_APP",
    "ALPACA_STOCK_BARS_STORAGE_BY_TRIPLE",
    "AlpacaStockBars1dIexRawStorage",
    "AlpacaStockBars1dSipAllStorage",
    "project_storage_models",
    "storage_for",
    "storage_metadata",
]
