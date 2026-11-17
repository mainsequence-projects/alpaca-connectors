"""Durable operational records for user-visible project workflows."""

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
    ALPACA_PORTFOLIO_EXECUTION_PATH,
    ConfigurationJobRun,
    ConfigurationJobState,
    ConfigurationJobSubmission,
    configuration_job_state,
    get_configuration_job_run,
    launch_portfolio_job,
    list_portfolio_job_runs,
)


def project_operation_models() -> list[type]:
    """All project-owned operational/control-plane MetaTables."""
    return [
        *project_asset_registration_operation_models(),
        *project_account_sync_operation_models(),
    ]


__all__ = [
    "ACCOUNT_SYNC_ACTION",
    "AccountSyncOperation",
    "AccountSyncOperationTable",
    "AssetRegistrationOperation",
    "AssetRegistrationOperationTable",
    "ALPACA_BARS_UPDATE_EXECUTION_PATH",
    "ALPACA_BARS_UPDATE_JOB_NAME",
    "ALPACA_PORTFOLIO_EXECUTION_PATH",
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
    "create_asset_registration_operation",
    "fail_asset_registration_operation",
    "get_asset_registration_operation",
    "get_configuration_job_run",
    "get_alpaca_bars_update_job_run",
    "launch_alpaca_bars_update",
    "project_operation_models",
    "set_asset_registration_step",
    "launch_portfolio_job",
    "list_portfolio_job_runs",
]
