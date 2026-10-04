"""Lifecycle and execution of Universe-backed Alpaca ETF signal configurations (ADR 0013).

Creating, updating, pausing or deleting a configuration touches only its row and its ms-markets
Signal metadata. The signal Job declared in ``.mainsequence/workflows/alpaca-connectors-api.yaml``
runs every configuration; :mod:`alpaca_connectors.operations.configuration_jobs` starts its runs.
"""

from __future__ import annotations

import uuid
from typing import Any

from alpaca_connectors.operations.signal_job_configurations import (
    AlpacaETFSignalJobConfiguration,
    create_signal_job_configuration_row,
    delete_signal_job_configuration_row,
    get_signal_job_configuration,
    signal_job_configurations_for_universe,
    update_signal_job_configuration_row,
    validate_signal_job_references,
)

_UNSET = object()


def _require_configuration(configuration_uid: uuid.UUID | str) -> AlpacaETFSignalJobConfiguration:
    configuration = get_signal_job_configuration(configuration_uid)
    if configuration is None:
        raise LookupError(f"Signal configuration {configuration_uid!s} does not exist.")
    return configuration


def signal_uid_for_configuration(
    configuration: AlpacaETFSignalJobConfiguration,
) -> str:
    """Resolve the canonical Signal identity without publishing observations."""
    from alpaca_connectors.portfolios import build_alpaca_etf_holdings_signal

    return build_alpaca_etf_holdings_signal(
        universe_uid=str(configuration.universe_uid),
        account_uid=str(configuration.account_uid),
    ).signal_uid


def _ensure_signal_metadata(configuration: AlpacaETFSignalJobConfiguration) -> str:
    """Materialize the real ms-markets Signal identity before the first run publishes."""
    from msm_portfolios.api.market_metadata import SignalMetadata

    from alpaca_connectors.portfolios import build_alpaca_etf_holdings_signal

    signal = build_alpaca_etf_holdings_signal(
        universe_uid=str(configuration.universe_uid),
        account_uid=str(configuration.account_uid),
    )
    SignalMetadata.upsert(
        signal_uid=signal.signal_uid,
        signal_description=configuration.description or signal.get_explanation(),
    )
    return signal.signal_uid


def create_signal_job_configuration(
    *,
    name: str,
    universe_uid: uuid.UUID | str,
    account_uid: uuid.UUID | str,
    description: str | None = None,
    scheduled: bool = True,
) -> AlpacaETFSignalJobConfiguration:
    """Store the configuration and materialize its Signal metadata; no Job is created."""
    configuration = create_signal_job_configuration_row(
        name=name,
        universe_uid=universe_uid,
        account_uid=account_uid,
        description=description,
        scheduled=scheduled,
    )
    _ensure_signal_metadata(configuration)
    return configuration


def update_signal_job_configuration(
    configuration_uid: uuid.UUID | str,
    *,
    name: str | None = None,
    description: str | None | object = _UNSET,
    universe_uid: uuid.UUID | str | None = None,
    account_uid: uuid.UUID | str | None = None,
    scheduled: bool | None = None,
) -> AlpacaETFSignalJobConfiguration:
    """Update the configuration and its Signal metadata."""
    current = _require_configuration(configuration_uid)
    values: dict[str, Any] = {}
    if universe_uid is not None or account_uid is not None:
        normalized_universe_uid = uuid.UUID(str(universe_uid or current.universe_uid))
        normalized_account_uid = uuid.UUID(str(account_uid or current.account_uid))
        validate_signal_job_references(
            universe_uid=normalized_universe_uid,
            account_uid=normalized_account_uid,
        )
        if normalized_universe_uid != current.universe_uid:
            conflicts = signal_job_configurations_for_universe(normalized_universe_uid)
            if any(row.uid != current.uid for row in conflicts):
                raise ValueError(
                    f"Asset Universe {normalized_universe_uid!s} already has a signal "
                    "configuration in this Environment."
                )
        values["universe_uid"] = normalized_universe_uid
        values["account_uid"] = normalized_account_uid
    if name is not None:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("Signal configuration name must not be empty.")
        values["name"] = normalized_name
    if description is not _UNSET:
        values["description"] = (
            str(description).strip() if description is not None else None
        ) or None
    if scheduled is not None:
        values["enabled"] = bool(scheduled)
    if not values:
        raise ValueError("At least one signal configuration field must be provided.")
    updated = update_signal_job_configuration_row(current.uid, values=values)
    _ensure_signal_metadata(updated)
    return updated


def pause_signal_job_configuration(
    configuration_uid: uuid.UUID | str,
) -> AlpacaETFSignalJobConfiguration:
    """Leave the configuration out of the signal Job's scheduled run."""
    return update_signal_job_configuration(configuration_uid, scheduled=False)


def resume_signal_job_configuration(
    configuration_uid: uuid.UUID | str,
) -> AlpacaETFSignalJobConfiguration:
    """Include the configuration in the signal Job's scheduled run again."""
    return update_signal_job_configuration(configuration_uid, scheduled=True)


def reconcile_signal_job_configuration(
    configuration_uid: uuid.UUID | str,
) -> AlpacaETFSignalJobConfiguration:
    """Ensure the configuration's Signal metadata; the Job itself is workflow-owned."""
    configuration = _require_configuration(configuration_uid)
    _ensure_signal_metadata(configuration)
    return configuration


def delete_signal_job_configuration(
    configuration_uid: uuid.UUID | str,
) -> dict[str, Any]:
    """Delete the configuration row only; its JobRuns and published signal data remain."""
    configuration = _require_configuration(configuration_uid)
    result = delete_signal_job_configuration_row(configuration.uid)
    return {
        "configuration_uid": str(configuration.uid),
        "deleted": True,
        "storage_result": result,
    }


def execute_signal_configuration(configuration_uid: uuid.UUID | str) -> dict[str, Any]:
    """Run one Universe-backed signal update for the selected configuration."""
    from alpaca_connectors.universes import run_asset_universe

    configuration = _require_configuration(configuration_uid)
    result = run_asset_universe(
        configuration.universe_uid,
        account_uid=configuration.account_uid,
    )
    return {
        "configuration_uid": str(configuration.uid),
        "universe_uid": str(configuration.universe_uid),
        "signal_uid": result.signal_uid,
        "observed_at": result.observed_at,
        "signal_weight_row_count": result.signal_weight_row_count,
        "asset_count": len(result.asset_uids),
    }


__all__ = [
    "create_signal_job_configuration",
    "delete_signal_job_configuration",
    "execute_signal_configuration",
    "pause_signal_job_configuration",
    "reconcile_signal_job_configuration",
    "resume_signal_job_configuration",
    "signal_uid_for_configuration",
    "update_signal_job_configuration",
]
