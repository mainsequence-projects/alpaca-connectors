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


def start_markets_engine(*, timeout: int | float | tuple[float, float] | None = None) -> Any:
    """Attach the markets runtime once for this process. Returns the ``MarketsRuntime``.

    Idempotent: ``msm.start_engine`` caches per configuration. Raises if the platform backend is
    unreachable or a required MetaTable has not been migrated/registered yet.
    """
    import msm

    return msm.start_engine(models=project_runtime_models(), timeout=timeout)


__all__ = ["project_runtime_models", "start_markets_engine"]
