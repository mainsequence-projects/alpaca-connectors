"""Public Market Data capability for Alpaca OHLCV storage and updates."""

from __future__ import annotations

from importlib import import_module

_LAZY_IMPORTS = {
    "ASSET_SOURCES": ("alpaca_connectors.market_data.configurations", "ASSET_SOURCES"),
    "AlpacaBarsConfiguration": (
        "alpaca_connectors.market_data.configurations",
        "AlpacaBarsConfiguration",
    ),
    "AlpacaBarsConfigurationAssetTable": (
        "alpaca_connectors.market_data.configurations",
        "AlpacaBarsConfigurationAssetTable",
    ),
    "AlpacaBarsConfigurationTable": (
        "alpaca_connectors.market_data.configurations",
        "AlpacaBarsConfigurationTable",
    ),
    "ALPACA_STOCK_BARS_STORAGE_BY_TRIPLE": (
        "alpaca_connectors.market_data.storage",
        "ALPACA_STOCK_BARS_STORAGE_BY_TRIPLE",
    ),
    "AlpacaStockBars1dSipAllStorage": (
        "alpaca_connectors.market_data.storage",
        "AlpacaStockBars1dSipAllStorage",
    ),
    "AlpacaStockBarsConfig": ("alpaca_connectors.market_data.alpaca_bars", "AlpacaStockBarsConfig"),
    "AlpacaStockBarsNode": ("alpaca_connectors.market_data.alpaca_bars", "AlpacaStockBarsNode"),
    "normalize_frequency_id": (
        "alpaca_connectors.market_data.alpaca_bars_support",
        "normalize_frequency_id",
    ),
    "project_storage_models": (
        "alpaca_connectors.market_data.storage",
        "project_storage_models",
    ),
    "storage_for": ("alpaca_connectors.market_data.storage", "storage_for"),
    "storage_metadata": ("alpaca_connectors.market_data.storage", "storage_metadata"),
    "MarketDataDataset": ("alpaca_connectors.market_data.services", "MarketDataDataset"),
    "build_market_data_update": (
        "alpaca_connectors.market_data.services",
        "build_market_data_update",
    ),
    "execute_market_data_update": (
        "alpaca_connectors.market_data.services",
        "execute_market_data_update",
    ),
    "get_market_data_dataset": (
        "alpaca_connectors.market_data.services",
        "get_market_data_dataset",
    ),
    "list_market_data_datasets": (
        "alpaca_connectors.market_data.services",
        "list_market_data_datasets",
    ),
    "create_bar_configuration": (
        "alpaca_connectors.market_data.configurations",
        "create_bar_configuration",
    ),
    "delete_bar_configuration": (
        "alpaca_connectors.market_data.configurations",
        "delete_bar_configuration",
    ),
    "get_bar_configuration": (
        "alpaca_connectors.market_data.configurations",
        "get_bar_configuration",
    ),
    "list_bar_configurations": (
        "alpaca_connectors.market_data.configurations",
        "list_bar_configurations",
    ),
    "project_configuration_models": (
        "alpaca_connectors.market_data.configurations",
        "project_configuration_models",
    ),
    "query_price_observations": (
        "alpaca_connectors.market_data.services",
        "query_price_observations",
    ),
    "resolve_market_data_update": (
        "alpaca_connectors.market_data.services",
        "resolve_market_data_update",
    ),
    "update_bar_configuration": (
        "alpaca_connectors.market_data.configurations",
        "update_bar_configuration",
    ),
    "validate_configuration_scope": (
        "alpaca_connectors.market_data.configurations",
        "validate_configuration_scope",
    ),
}

__all__ = list(_LAZY_IMPORTS)


def __getattr__(name: str):
    if name in _LAZY_IMPORTS:
        module_name, attr_name = _LAZY_IMPORTS[name]
        value = getattr(import_module(module_name), attr_name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)
