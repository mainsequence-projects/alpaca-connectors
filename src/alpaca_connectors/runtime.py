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
startup, which first starts the Markets API: ``alpaca_connectors.dependencies``).
"""

from __future__ import annotations

from typing import Any


def use_markets_api() -> Any:
    """Send this process's Markets row calls to the Markets API (once per process).

    The Markets API is found in the caller's Environment on the first row call, or from the API's
    startup (``MarketsApiBackend``, ADR 0020), so a process that never touches a Markets registry
    never needs to see it. The workflow grants every resource that does ``reader`` on the
    ``MainSequenceMarkets`` branch; ``MARKETS_API_URL`` selects a local Markets API. Returns the
    process's remote backend.
    """
    from msm.api.remote import active_remote_backend, use_remote_backend

    from alpaca_connectors.dependencies import MarketsApiBackend

    if active_remote_backend() is None:
        use_remote_backend(MarketsApiBackend())
    return active_remote_backend()


def project_runtime_models() -> list[type[Any]]:
    """This project's asset, bars, configuration and operation tables."""
    from alpaca_connectors.assets.alpaca_asset_details import project_asset_models
    from alpaca_connectors.market_data import project_configuration_models, project_storage_models
    from alpaca_connectors.operations import project_operation_models
    from alpaca_connectors.portfolios import project_portfolio_configuration_models

    return [
        *project_asset_models(),
        *project_storage_models(),
        *project_configuration_models(),
        *project_operation_models(),
        *project_portfolio_configuration_models(),
    ]


def account_runtime_models() -> list[type[Any]]:
    """Every table this project reads or writes: the project tables, account details, key
    requests, and the private execution and portfolio contract instances.

    It equals ``application_runtime_models()``: ``start_engine`` rejects a second *differing*
    model set in one process, and services started with either set call each other.
    """
    from alpaca_connectors.account.provider_account_details import project_account_models
    from alpaca_connectors.execution import execution_runtime_models
    from alpaca_connectors.instances import private_portfolio_models

    return [
        *project_runtime_models(),
        *project_account_models(),
        # Order execution (ADR 0016) writes this application's orders and trades instances.
        *execution_runtime_models(),
        # A portfolio run writes its rebalance state and calendar events in this application's
        # private instances; its signal and portfolio series are Markets' (ADR 0031).
        *private_portfolio_models(),
    ]


def application_runtime_models() -> list[type[Any]]:
    """Complete model set used by long-lived API processes and shared services."""
    return account_runtime_models()


def portfolio_runtime_models() -> list[type[Any]]:
    """Runtime models for portfolio construction.

    The application tables with the private portfolio instances, the interpolated prices among
    them: every one is migrated by this application's provider (ADR 0029). Calendars, portfolios
    and signal metadata are Markets registries read and written through the Markets API, so they
    are not attached.
    """
    return application_runtime_models()


def start_portfolio_markets_engine(
    *,
    timeout: int | float | tuple[float, float] | None = None,
) -> Any:
    """Attach the portfolio-capable runtime once for this process."""
    import msm_portfolios
    from msm.bootstrap import resolve_runtime

    use_markets_api()
    models = portfolio_runtime_models()
    try:
        return resolve_runtime(models=models, row_model_name="alpaca_connectors.runtime")
    except RuntimeError:
        return msm_portfolios.start_engine(models=models, timeout=timeout)


def start_portfolio_job_engine(
    *,
    timeout: int | float | tuple[float, float] | None = None,
) -> Any:
    """Attach the complete portfolio runtime in one process bootstrap."""
    import msm_portfolios

    use_markets_api()
    return msm_portfolios.start_engine(models=portfolio_runtime_models(), timeout=timeout)


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
    "project_runtime_models",
    "start_markets_engine",
    "start_portfolio_markets_engine",
    "start_portfolio_job_engine",
    "use_markets_api",
]
