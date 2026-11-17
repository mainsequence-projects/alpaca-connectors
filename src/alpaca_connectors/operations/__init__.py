"""Durable operational records for user-visible project workflows.

The ETF signal modules load only when one of their names is used (ADR 0027), so the account sync
and the other operations never load them.
"""

from importlib import import_module

from .account_sync import (
    ACCOUNT_SYNC_ACTION,
    AccountSyncOperation,
    AccountSyncOperationTable,
    fail_account_sync_operation,
    get_account_sync_operation,
    start_account_sync_operation,
    succeed_account_sync_operation,
)
from .account_sync import (
    project_operation_models as project_account_sync_operation_models,
)
from .asset_registration import (
    AssetRegistrationOperation,
    AssetRegistrationOperationTable,
    complete_asset_registration_operation,
    create_asset_registration_operation,
    fail_asset_registration_operation,
    get_asset_registration_operation,
    set_asset_registration_step,
)
from .asset_registration import (
    project_operation_models as project_asset_registration_operation_models,
)
from .bar_updates import (
    ALPACA_BARS_UPDATE_EXECUTION_PATH,
    ALPACA_BARS_UPDATE_JOB_NAME,
    AlpacaBarsUpdateJobRun,
    AlpacaBarsUpdateSubmission,
    get_alpaca_bars_update_job_run,
    launch_alpaca_bars_update,
)
from .configuration_jobs import (
    ALPACA_ETF_PORTFOLIO_EXECUTION_PATH,
    ALPACA_ETF_SIGNAL_EXECUTION_PATH,
    ConfigurationJobRun,
    ConfigurationJobState,
    ConfigurationJobSubmission,
    configuration_job_state,
    get_configuration_job_run,
    launch_portfolio_job,
    launch_signal_job,
    list_portfolio_job_runs,
    list_signal_job_runs,
)

_LAZY_IMPORTS = {
    name: f"alpaca_connectors.operations.{module}"
    for module, names in {
        "signal_job_configurations": (
            "AlpacaETFSignalJobConfiguration",
            "AlpacaETFSignalJobConfigurationTable",
            "get_signal_job_configuration",
            "list_signal_job_configurations",
            "project_signal_job_models",
            "signal_job_configurations_by_uids",
            "signal_job_configurations_for_account",
            "signal_job_configurations_for_universe",
        ),
        "signal_jobs": (
            "create_signal_job_configuration",
            "delete_signal_job_configuration",
            "execute_signal_configuration",
            "pause_signal_job_configuration",
            "reconcile_signal_job_configuration",
            "resume_signal_job_configuration",
            "signal_uid_for_configuration",
            "update_signal_job_configuration",
        ),
    }.items()
    for name in names
}


def __getattr__(name: str):
    if name in _LAZY_IMPORTS:
        value = getattr(import_module(_LAZY_IMPORTS[name]), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def project_operation_models() -> list[type]:
    """All project-owned operational/control-plane MetaTables."""
    from .signal_job_configurations import project_signal_job_models

    return [
        *project_asset_registration_operation_models(),
        *project_account_sync_operation_models(),
        *project_signal_job_models(),
    ]


__all__ = [
    "ACCOUNT_SYNC_ACTION",
    "AccountSyncOperation",
    "AccountSyncOperationTable",
    "AssetRegistrationOperation",
    "AssetRegistrationOperationTable",
    "ALPACA_BARS_UPDATE_EXECUTION_PATH",
    "ALPACA_BARS_UPDATE_JOB_NAME",
    "ALPACA_ETF_SIGNAL_EXECUTION_PATH",
    "ALPACA_ETF_PORTFOLIO_EXECUTION_PATH",
    "AlpacaETFSignalJobConfiguration",
    "AlpacaETFSignalJobConfigurationTable",
    "AlpacaBarsUpdateJobRun",
    "AlpacaBarsUpdateSubmission",
    "ConfigurationJobRun",
    "ConfigurationJobState",
    "ConfigurationJobSubmission",
    "complete_asset_registration_operation",
    "fail_account_sync_operation",
    "get_account_sync_operation",
    "start_account_sync_operation",
    "succeed_account_sync_operation",
    "configuration_job_state",
    "create_signal_job_configuration",
    "create_asset_registration_operation",
    "fail_asset_registration_operation",
    "get_asset_registration_operation",
    "get_configuration_job_run",
    "get_signal_job_configuration",
    "get_alpaca_bars_update_job_run",
    "launch_alpaca_bars_update",
    "launch_signal_job",
    "list_signal_job_configurations",
    "list_signal_job_runs",
    "pause_signal_job_configuration",
    "project_operation_models",
    "reconcile_signal_job_configuration",
    "resume_signal_job_configuration",
    "signal_job_configurations_for_account",
    "signal_job_configurations_by_uids",
    "signal_job_configurations_for_universe",
    "signal_uid_for_configuration",
    "set_asset_registration_step",
    "update_signal_job_configuration",
    "delete_signal_job_configuration",
    "execute_signal_configuration",
    "launch_portfolio_job",
    "list_portfolio_job_runs",
]
