"""ms-markets runtime bootstrap for the Alpaca connector project.

Storage-first ms-markets requires a one-time ``msm.start_engine(...)`` per process **before**
any MetaTable-backed row, repository, service, or DataNode operation. It attaches the runtime to
the already-migrated/registered MetaTables; it does not create schema (that is the migration
provider's job). ``msm.start_engine`` is idempotent and cached per configuration, so calling
``start_markets_engine()`` more than once in a process is safe.

Call this once at every process entrypoint (CLI ``main()``, each job ``main()``, FastAPI
startup). Never call it from the SDK-free ETF extractor import graph.
"""

from __future__ import annotations

from typing import Any


def project_runtime_models() -> list[type[Any]]:
    """MetaTable models this project attaches at runtime, in rough dependency order.

    Includes the ms-markets asset graph the business logic touches (assets, asset types,
    OpenFIGI provider details, asset snapshots, categories + memberships) plus the project-owned
    Alpaca bars storage classes. ``msm.start_engine`` re-orders by foreign-key dependencies and
    auto-includes referenced built-in tables (e.g. ``AssetTable``).
    """
    from msm.data_nodes.assets.storage import AssetSnapshotsStorage
    from msm.models import (
        AssetCategoryMembershipTable,
        AssetCategoryTable,
        AssetTable,
        AssetTypeTable,
        OpenFigiAssetDetailsTable,
    )

    from src.markets_storage.alpaca_bars import project_storage_models

    return [
        AssetTypeTable,
        AssetTable,
        OpenFigiAssetDetailsTable,
        AssetSnapshotsStorage,
        AssetCategoryTable,
        AssetCategoryMembershipTable,
        *project_storage_models(),
    ]


def account_runtime_models() -> list[type[Any]]:
    """Runtime models for the account-registration flow.

    The base asset/bars graph (registration resolves/registers held assets) plus the ms-markets
    account graph (`AccountGroupTable`, `AccountTable`, `AccountHoldingsSetTable`,
    `AccountHoldingsStorage`) and the project-owned Alpaca account tables. ``msm.start_engine``
    re-orders by FK dependency, so the leaves are enough but the full set is listed for clarity.

    Use this only in the ``account`` entrypoint; ``start_engine`` rejects a second *differing*
    model set in one process, but each CLI command is its own process.
    """
    from msm.data_nodes.accounts.storage import AccountHoldingsStorage
    from msm.models.accounts.core import AccountHoldingsSetTable, AccountTable
    from msm.models.accounts.groups import AccountGroupTable

    from src.account.alpaca_account_details import project_account_models

    return [
        *project_runtime_models(),
        AccountGroupTable,
        AccountTable,
        AccountHoldingsSetTable,
        AccountHoldingsStorage,
        *project_account_models(),
    ]


def portfolio_runtime_models(extra_models: list[type[Any]] | None = None) -> list[type[Any]]:
    """Runtime models for ETF-holdings portfolio construction.

    This attaches the msm_portfolios graph plus the project-owned Alpaca bars storage classes.
    Configured interpolated-price storage classes are dynamic and should be prepared/migrated
    before normal portfolio execution; keep them out of this static runtime list.
    """
    from msm.data_nodes.assets.storage import AssetSnapshotsStorage
    from msm.models import (
        AssetCategoryMembershipTable,
        AssetCategoryTable,
        CalendarDateTable,
        CalendarSessionTable,
        OpenFigiAssetDetailsTable,
    )
    from msm_portfolios.bootstrap import resolve_portfolio_models

    from src.markets_storage.alpaca_bars import project_storage_models

    return [
        *resolve_portfolio_models(None),
        OpenFigiAssetDetailsTable,
        AssetSnapshotsStorage,
        AssetCategoryTable,
        AssetCategoryMembershipTable,
        CalendarDateTable,
        CalendarSessionTable,
        *project_storage_models(),
        *(extra_models or []),
    ]


def start_portfolio_markets_engine(
    *,
    extra_models: list[type[Any]] | None = None,
    timeout: int | float | tuple[float, float] | None = None,
) -> Any:
    """Attach the portfolio-capable markets runtime once for this process."""
    import msm_portfolios
    from msm.bootstrap import resolve_runtime

    models = portfolio_runtime_models(extra_models=extra_models)
    try:
        return resolve_runtime(models=models, row_model_name="src.runtime")
    except RuntimeError:
        return msm_portfolios.start_engine(models=models, timeout=timeout)


def start_markets_engine(
    *,
    models: list[type[Any]] | None = None,
    timeout: int | float | tuple[float, float] | None = None,
) -> Any:
    """Attach the markets runtime once for this process. Returns the ``MarketsRuntime``.

    Pass ``models`` to attach a specific set (e.g. ``account_runtime_models()``); defaults to
    ``project_runtime_models()``. Idempotent: ``msm.start_engine`` caches per configuration. Raises
    if the platform backend is unreachable or a required MetaTable has not been migrated yet.
    """
    import msm

    return msm.start_engine(
        models=models if models is not None else project_runtime_models(),
        timeout=timeout,
    )


__all__ = [
    "account_runtime_models",
    "portfolio_runtime_models",
    "project_runtime_models",
    "start_markets_engine",
    "start_portfolio_markets_engine",
]
