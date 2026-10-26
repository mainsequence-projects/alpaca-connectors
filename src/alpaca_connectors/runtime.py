"""ms-markets runtime bootstrap for the Alpaca connector project (ms-markets ADR 0047).

Markets registries (assets, asset types, OpenFIGI details, accounts, holdings, asset categories
and memberships, calendars, portfolios, signal metadata, order managers) and the Markets tables
this application publishes into (account holdings, asset snapshots) are reached through the
Markets API: every entrypoint selects ms-markets' remote row backend (``use_markets_api``), so
``Asset.upsert_many(...)``, ``Account.update(...)``, ``AccountHoldingsSnapshots.publish(...)``,
``Portfolio.upsert(...)`` and the other ``msm.api`` row calls run on the Markets API. This
application holds no grant on ``mainsequence.markets``.

This application's own tables (namespace ``alpaca-connectors``), including its instances of the
Markets time-series contracts (``alpaca_connectors.instances``), stay on the local MetaTables
runtime: ``start_markets_engine()`` attaches them, and their foreign keys to Markets registries
are references by physical name, not attachments. It attaches the already-migrated tables; it
never creates schema (that is the migration provider's job). ``msm.start_engine`` is idempotent
and cached per configuration, so calling it more than once in a process is safe.

Call this once at every process entrypoint (CLI ``main()``, each job ``main()``, FastAPI
startup). Never call it from the SDK-free ETF extractor import graph.
"""

from __future__ import annotations

from typing import Any


def use_markets_api() -> None:
    """Send this process's Markets row calls to the Markets API (once per process).

    The Markets API is found in the caller's Environment on the first row call
    (``LazyRemoteBackend``), so a process that never touches a Markets registry never needs to
    see it. The workflow grants every resource that does ``reader`` on the
    ``MainSequenceMarkets`` branch; ``MARKETS_API_URL`` selects a local Markets API.
    """
    from msm.api.remote import active_remote_backend, use_remote_backend
    from msm.client import LazyRemoteBackend

    if active_remote_backend() is None:
        use_remote_backend(LazyRemoteBackend())


def project_runtime_models() -> list[type[Any]]:
    """This project's asset, bars, universe, configuration and operation tables."""
    from alpaca_connectors.assets.alpaca_asset_details import project_asset_models
    from alpaca_connectors.market_data import project_configuration_models, project_storage_models
    from alpaca_connectors.operations import project_operation_models
    from alpaca_connectors.portfolios import project_portfolio_configuration_models
    from alpaca_connectors.universes import project_universe_models

    return [
        *project_asset_models(),
        *project_storage_models(),
        *project_universe_models(),
        *project_configuration_models(),
        *project_operation_models(),
        *project_portfolio_configuration_models(),
    ]


def account_runtime_models() -> list[type[Any]]:
    """The project tables plus the account details, key requests and execution ledger instances.

    Use this only in the ``account`` entrypoint; ``start_engine`` rejects a second *differing*
    model set in one process, but each CLI command is its own process.
    """
    from alpaca_connectors.account.alpaca_account_details import project_account_models
    from alpaca_connectors.execution import execution_runtime_models

    return [
        *project_runtime_models(),
        *project_account_models(),
        # Order execution (ADR 0016) writes this application's orders and trades instances.
        *execution_runtime_models(),
    ]


def application_runtime_models() -> list[type[Any]]:
    """Complete model set used by long-lived API processes and shared services."""
    from alpaca_connectors.instances import portfolio_instance_models

    # The API reads signal and portfolio history from this application's instances.
    return [*account_runtime_models(), *portfolio_instance_models()]


def portfolio_runtime_models(extra_models: list[type[Any]] | None = None) -> list[type[Any]]:
    """Runtime models for ETF-holdings portfolio construction.

    The application tables plus the signal and portfolio contract instances. Configured
    interpolated-price storage classes are dynamic and must be migrated before normal
    portfolio execution; pass them as ``extra_models``. Calendars, portfolios and signal metadata
    are Markets registries read and written through the Markets API, so they are not attached.
    """
    return [*application_runtime_models(), *(extra_models or [])]


def start_portfolio_markets_engine(
    *,
    extra_models: list[type[Any]] | None = None,
    timeout: int | float | tuple[float, float] | None = None,
) -> Any:
    """Attach the portfolio-capable runtime once for this process."""
    import msm_portfolios
    from msm.bootstrap import resolve_runtime

    use_markets_api()
    models = portfolio_runtime_models(extra_models=extra_models)
    try:
        return resolve_runtime(models=models, row_model_name="alpaca_connectors.runtime")
    except RuntimeError:
        return msm_portfolios.start_engine(models=models, timeout=timeout)


def portfolio_job_runtime_models() -> list[type[Any]]:
    """Resolve every phase-one persistent interpolation table before process bootstrap.

    The interpolation table identity includes the registered source TimeIndexMetaTable UID.
    Jobs therefore discover each deliberately migrated Alpaca bars profile before the one allowed
    ``msm.start_engine`` call and attach all phase-one interpolation outputs in the same runtime.
    """
    from alpaca_connectors.portfolios.interpolated_prices_schema import (
        assert_interpolated_prices_registered,
        configured_alpaca_interpolated_prices_storage,
        resolve_interpolated_prices_storage_specs,
    )

    specs = resolve_interpolated_prices_storage_specs()
    assert_interpolated_prices_registered(specs)
    dynamic_models = [
        configured_alpaca_interpolated_prices_storage(
            source_time_index_meta_table_uid=spec.source_time_index_meta_table_uid,
            source_cadence=spec.source_cadence,
        )
        for spec in specs
    ]
    return portfolio_runtime_models(extra_models=dynamic_models)


def start_portfolio_job_engine(
    *,
    timeout: int | float | tuple[float, float] | None = None,
) -> Any:
    """Attach the complete phase-one portfolio runtime in one process bootstrap."""
    import msm_portfolios

    use_markets_api()
    return msm_portfolios.start_engine(models=portfolio_job_runtime_models(), timeout=timeout)


def start_markets_engine(
    *,
    models: list[type[Any]] | None = None,
    timeout: int | float | tuple[float, float] | None = None,
) -> Any:
    """Select the Markets API for registries and attach this project's own tables once.

    Returns the ``MarketsRuntime`` of the own tables. Pass ``models`` to attach a specific set
    (e.g. ``account_runtime_models()``); defaults to ``application_runtime_models()``. Raises if
    the platform backend is unreachable or a required MetaTable has not been migrated yet.
    """
    import msm
    from msm.bootstrap import resolve_runtime

    use_markets_api()
    resolved_models = models if models is not None else application_runtime_models()
    try:
        return resolve_runtime(models=resolved_models, row_model_name="alpaca_connectors.runtime")
    except RuntimeError as exc:
        if "requires an initialized markets runtime" not in str(exc):
            raise
    return msm.start_engine(models=resolved_models, timeout=timeout)


__all__ = [
    "account_runtime_models",
    "application_runtime_models",
    "portfolio_runtime_models",
    "portfolio_job_runtime_models",
    "project_runtime_models",
    "start_markets_engine",
    "start_portfolio_markets_engine",
    "start_portfolio_job_engine",
    "use_markets_api",
]
