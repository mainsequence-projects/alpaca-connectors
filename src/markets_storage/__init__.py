"""Project-owned ms-markets storage classes (storage-first DataNode architecture).

These SQLAlchemy MetaTable classes own the published table schema for the project's
asset-indexed DataNodes. They replace the old ``records: list[RecordDefinition]`` that used
to live on the ``DataNodeConfiguration``. Identifiers, columns, dtypes, indexes, and the
``asset_identifier`` foreign key all live here; the DataNode config carries only updater scope.

See ``docs/implementation_tasks/0001_ms_markets_storage_first_migration.md`` (Phase 2).
"""

from __future__ import annotations

from src.markets_storage.alpaca_bars import (
    ALPACA_STOCK_BARS_STORAGE_BY_TRIPLE,
    AlpacaStockBars1dSipAllStorage,
    project_storage_models,
    storage_for,
    storage_metadata,
)

__all__ = [
    "ALPACA_STOCK_BARS_STORAGE_BY_TRIPLE",
    "AlpacaStockBars1dSipAllStorage",
    "project_storage_models",
    "storage_for",
    "storage_metadata",
]
