"""Every project table lives in the `alpaca-connectors` namespace (ms-markets ADR 0047).

The namespace move keeps physical names and uids; only the catalog namespace and the identifier
prefix change. These tests freeze the physical names and the resolved catalog identities so a
change to the mixins cannot silently move a table again.
"""

from __future__ import annotations

from alpaca_connectors.migrations import all_project_metatable_models

PHYSICAL_TABLES = {
    "AlpacaAssetDetailsTable": ("alpaca_connectors__asset_alpaca", "AlpacaAssetDetails"),
    "AlpacaStockBars1dSipAllStorage": (
        "alpaca_connectors__bars_1d_sip_all",
        "alpaca_stock_bars_1d_sip_all",
    ),
    "AlpacaStockBars1dIexRawStorage": (
        "alpaca_connectors__bars_1d_iex_raw",
        "alpaca_stock_bars_1d_iex_raw",
    ),
    "AlpacaAccountDetails": ("alpaca_connectors__acct_alpaca", "AlpacaAccountDetails"),
    "AccountKeyRequestTable": ("alpaca_connectors__account_key_request", "AccountKeyRequest"),
    "UniverseSourceTable": ("alpaca_connectors__universe_source", "UniverseSource"),
    "AssetUniverseTable": ("alpaca_connectors__asset_universe", "AssetUniverse"),
    "AlpacaBarsConfigurationTable": (
        "alpaca_connectors__bars_configuration",
        "AlpacaBarsConfiguration",
    ),
    "AlpacaBarsConfigurationAssetTable": (
        "alpaca_connectors__bars_configuration_asset",
        "AlpacaBarsConfigurationAsset",
    ),
    "AssetRegistrationOperationTable": (
        "alpaca_connectors__asset_registration_operation",
        "AssetRegistrationOperation",
    ),
    "AlpacaETFSignalJobConfigurationTable": (
        "alpaca_connectors__etf_signal_job_configuration",
        "AlpacaETFSignalJobConfiguration",
    ),
    "PortfolioRebalanceConfigurationTable": (
        "alpaca_connectors__portfolio_rebalance_configuration",
        "PortfolioRebalanceConfiguration",
    ),
    "AlpacaETFPortfolioConfigurationTable": (
        "alpaca_connectors__etf_portfolio_configuration",
        "AlpacaETFPortfolioConfiguration",
    ),
}


def test_every_project_table_is_registered_in_the_project_namespace() -> None:
    models = {model.__name__: model for model in all_project_metatable_models()}

    assert set(PHYSICAL_TABLES) <= set(models)
    for name, model in models.items():
        assert model.__table__.info["namespace"] == "alpaca-connectors", name
        assert model.__metatable_identifier__.startswith("alpaca-connectors."), name


def test_the_namespace_move_keeps_physical_names_and_authored_identifiers() -> None:
    models = {model.__name__: model for model in all_project_metatable_models()}

    for name, (physical_name, authored) in PHYSICAL_TABLES.items():
        model = models[name]
        assert model.__table__.name == physical_name
        assert model.__metatable_identifier__ == f"alpaca-connectors.{authored}"


def test_project_row_classes_stay_on_the_local_runtime() -> None:
    """Under the remote backend a Markets-owned row class runs on the Markets API."""
    from msm.api.remote import is_markets_owned

    from alpaca_connectors.account.key_requests import AccountKeyRequest
    from alpaca_connectors.assets.alpaca_asset_details import AlpacaAssetDetails
    from alpaca_connectors.market_data.configurations import AlpacaBarsConfiguration
    from alpaca_connectors.operations.asset_registration import AssetRegistrationOperation
    from alpaca_connectors.operations.signal_job_configurations import (
        AlpacaETFSignalJobConfiguration,
    )
    from alpaca_connectors.portfolios.configurations import (
        AlpacaETFPortfolioConfiguration,
        PortfolioRebalanceConfiguration,
    )
    from alpaca_connectors.universes.registry import AssetUniverse
    from alpaca_connectors.universes.sources import UniverseSource

    for row in (
        AccountKeyRequest,
        AlpacaAssetDetails,
        AlpacaBarsConfiguration,
        AssetRegistrationOperation,
        AlpacaETFSignalJobConfiguration,
        AlpacaETFPortfolioConfiguration,
        PortfolioRebalanceConfiguration,
        AssetUniverse,
        UniverseSource,
    ):
        assert not is_markets_owned(row), row.__name__
