from __future__ import annotations

import pytest
from msm.models.registration import resolve_markets_meta_table_models

from alpaca_connectors.runtime import (
    account_runtime_models,
    application_runtime_models,
    portfolio_runtime_models,
)


@pytest.fixture
def remote_backend():
    """Select the remote row backend as every entrypoint does, then restore the runtime."""
    from msm.api.remote import use_remote_backend, use_runtime_backend
    from msm.client import LazyRemoteBackend

    use_remote_backend(LazyRemoteBackend())
    yield
    use_runtime_backend()


def _table_names(models) -> list[str]:
    return [model.__table__.name for model in resolve_markets_meta_table_models(models)]


def test_portfolio_runtime_includes_project_foreign_key_dependencies(remote_backend) -> None:
    table_names = _table_names(portfolio_runtime_models())
    application_tables = set(_table_names(application_runtime_models()))

    signal_configuration = "alpaca_connectors__etf_signal_job_configuration"
    bars_configuration = "alpaca_connectors__bars_configuration"
    portfolio_configuration = "alpaca_connectors__etf_portfolio_configuration"

    assert signal_configuration in table_names
    assert bars_configuration in table_names
    assert application_tables.issubset(table_names)
    assert table_names.index(signal_configuration) < table_names.index(portfolio_configuration)
    assert table_names.index(bars_configuration) < table_names.index(portfolio_configuration)


def test_runtimes_attach_only_this_applications_tables(remote_backend) -> None:
    """Markets tables are foreign-key targets by name only (ms-markets ADR 0047)."""
    for models in (account_runtime_models(), portfolio_runtime_models()):
        names = _table_names(models)
        assert names, "the runtime attaches this application's tables"
        assert all(name.startswith("alpaca_connectors__") for name in names), names


def test_application_runtime_attaches_the_own_execution_ledger_and_portfolio_instances(
    remote_backend,
) -> None:
    table_names = set(_table_names(application_runtime_models()))

    assert {
        "alpaca_connectors__ordersts__main",
        "alpaca_connectors__tradests__main",
        "alpaca_connectors__signalweightsts__main",
        "alpaca_connectors__portfoliosts__main",
    } <= table_names


def test_account_and_application_runtimes_are_the_same_model_set() -> None:
    # start_engine rejects a second differing model set in one process.
    assert account_runtime_models() == application_runtime_models()
