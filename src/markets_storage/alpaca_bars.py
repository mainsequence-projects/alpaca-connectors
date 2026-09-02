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

from mainsequence.meta_tables import schema_table_name
from mainsequence.meta_tables.migrations import metadata_for_models

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

# Shared, intention-rich column metadata for every OHLCV value column. Each storage class gets
# its own freshly built ``mapped_column`` objects (SQLAlchemy columns cannot be shared).
_VALUE_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("open", "Open", "Bar open price."),
    ("high", "High", "Bar high price."),
    ("low", "Low", "Bar low price."),
    ("close", "Close", "Bar close price."),
    ("volume", "Volume", "Reported traded share volume."),
    ("trade_count", "Trade Count", "Number of trades in the bar."),
    ("vwap", "VWAP", "Volume weighted average price in the bar."),
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
        "Alpaca US equity OHLCV bars at 1d frequency from the sip feed with all adjustment, "
        "keyed by (time_index, asset_identifier). Backs holdings-universe price updates."
    )
    __time_index_name__: ClassVar[str] = "time_index"
    __cadence__: ClassVar[str] = "1d"
    __index_names__: ClassVar[list[str]] = ["time_index", ASSET_IDENTIFIER_DIMENSION]

    time_index: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        info={"label": "Time Index", "description": "UTC bar timestamp."},
    )
    asset_identifier: Mapped[str] = mapped_column(
        String(255),
        ForeignKey(_ASSET_IDENTIFIER_FK, ondelete="RESTRICT"),
        nullable=False,
        info={
            "label": "Asset Identifier",
            "description": "Asset unique identifier from the Asset MetaTable.",
        },
    )
    open: Mapped[float | None] = mapped_column(
        Float, nullable=True, info={"label": "Open", "description": "Bar open price."}
    )
    high: Mapped[float | None] = mapped_column(
        Float, nullable=True, info={"label": "High", "description": "Bar high price."}
    )
    low: Mapped[float | None] = mapped_column(
        Float, nullable=True, info={"label": "Low", "description": "Bar low price."}
    )
    close: Mapped[float | None] = mapped_column(
        Float, nullable=True, info={"label": "Close", "description": "Bar close price."}
    )
    volume: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        info={"label": "Volume", "description": "Reported traded share volume."},
    )
    trade_count: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        info={"label": "Trade Count", "description": "Number of trades in the bar."},
    )
    vwap: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        info={"label": "VWAP", "description": "Volume weighted average price in the bar."},
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
        "Alpaca US equity OHLCV bars at 1d frequency from the iex feed with raw adjustment, "
        "keyed by (time_index, asset_identifier). Preserves the generic bars CLI default table "
        "without mixing it with SIP/all adjusted bars."
    )
    __time_index_name__: ClassVar[str] = "time_index"
    __cadence__: ClassVar[str] = "1d"
    __index_names__: ClassVar[list[str]] = ["time_index", ASSET_IDENTIFIER_DIMENSION]

    time_index: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        info={"label": "Time Index", "description": "UTC bar timestamp."},
    )
    asset_identifier: Mapped[str] = mapped_column(
        String(255),
        ForeignKey(_ASSET_IDENTIFIER_FK, ondelete="RESTRICT"),
        nullable=False,
        info={
            "label": "Asset Identifier",
            "description": "Asset unique identifier from the Asset MetaTable.",
        },
    )
    open: Mapped[float | None] = mapped_column(
        Float, nullable=True, info={"label": "Open", "description": "Bar open price."}
    )
    high: Mapped[float | None] = mapped_column(
        Float, nullable=True, info={"label": "High", "description": "Bar high price."}
    )
    low: Mapped[float | None] = mapped_column(
        Float, nullable=True, info={"label": "Low", "description": "Bar low price."}
    )
    close: Mapped[float | None] = mapped_column(
        Float, nullable=True, info={"label": "Close", "description": "Bar close price."}
    )
    volume: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        info={"label": "Volume", "description": "Reported traded share volume."},
    )
    trade_count: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        info={"label": "Trade Count", "description": "Number of trades in the bar."},
    )
    vwap: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        info={"label": "VWAP", "description": "Volume weighted average price in the bar."},
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
# migration provider as ``src.markets_storage.alpaca_bars:METADATA``.
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
