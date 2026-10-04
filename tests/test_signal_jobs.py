from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from alpaca_connectors.operations import signal_job_configurations, signal_jobs
from alpaca_connectors.operations.signal_job_configurations import (
    RETIRED_JOB_COLUMN_VALUES,
    AlpacaETFSignalJobConfiguration,
    AlpacaETFSignalJobConfigurationTable,
    create_signal_job_configuration_row,
    validate_signal_job_references,
)
from alpaca_connectors.operations.signal_jobs import (
    _ensure_signal_metadata,
    execute_signal_configuration,
    update_signal_job_configuration,
)

CONFIGURATION_UID = uuid.UUID("11111111-1111-4111-8111-111111111111")
UNIVERSE_UID = uuid.UUID("22222222-2222-4222-8222-222222222222")
ACCOUNT_UID = uuid.UUID("33333333-3333-4333-8333-333333333333")
OTHER_UNIVERSE_UID = uuid.UUID("44444444-4444-4444-8444-444444444444")


def configuration(**overrides) -> AlpacaETFSignalJobConfiguration:
    now = dt.datetime(2026, 9, 4, 9, tzinfo=dt.UTC)
    values = {
        "uid": CONFIGURATION_UID,
        "name": "Daily IVV observation",
        "description": "Observe the registered Universe.",
        "universe_uid": UNIVERSE_UID,
        "account_uid": ACCOUNT_UID,
        "enabled": True,
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return AlpacaETFSignalJobConfiguration.model_validate(values)


def test_configuration_is_one_per_universe_and_account_is_runtime_only() -> None:
    indexes = list(AlpacaETFSignalJobConfigurationTable.__table__.indexes)

    assert any(
        index.unique and [column.name for column in index.columns] == ["universe_uid"]
        for index in indexes
    )
    assert AlpacaETFSignalJobConfigurationTable.__table__.c.account_uid.foreign_keys
    assert "environment_uid" not in AlpacaETFSignalJobConfigurationTable.__table__.c
    assert configuration(enabled=False).scheduled is False


def test_signal_reference_validation_accepts_active_account_registration_mapping() -> None:
    universe = SimpleNamespace(is_active=True)

    with (
        patch("alpaca_connectors.universes.get_asset_universe", return_value=universe),
        patch(
            "alpaca_connectors.account.services.get_account_registration",
            return_value={"account_is_active": True},
        ),
    ):
        validate_signal_job_references(
            universe_uid=UNIVERSE_UID,
            account_uid=ACCOUNT_UID,
        )


def test_signal_reference_validation_rejects_inactive_account_registration_mapping() -> None:
    universe = SimpleNamespace(is_active=True)

    with (
        patch("alpaca_connectors.universes.get_asset_universe", return_value=universe),
        patch(
            "alpaca_connectors.account.services.get_account_registration",
            return_value={"account_is_active": False},
        ),
        pytest.raises(ValueError, match="is inactive"),
    ):
        validate_signal_job_references(
            universe_uid=UNIVERSE_UID,
            account_uid=ACCOUNT_UID,
        )


def test_create_stores_the_scheduled_flag_and_retired_placeholders() -> None:
    created = configuration(enabled=False)
    create_model = Mock(return_value="result")

    with (
        patch("alpaca_connectors.runtime.start_markets_engine"),
        patch.object(signal_job_configurations, "validate_signal_job_references") as validate,
        patch.object(
            signal_job_configurations, "signal_job_configurations_for_universe", return_value=[]
        ),
        patch("msm.bootstrap.resolve_runtime", return_value=SimpleNamespace(context="context")),
        patch("msm.repositories.crud.create_model", create_model),
        patch.object(
            signal_job_configurations,
            "operation_result_rows",
            return_value=[created.model_dump()],
        ),
    ):
        row = create_signal_job_configuration_row(
            name=" Daily IVV observation ",
            universe_uid=str(UNIVERSE_UID),
            account_uid=str(ACCOUNT_UID),
            scheduled=False,
        )

    assert row == created
    validate.assert_called_once_with(universe_uid=UNIVERSE_UID, account_uid=ACCOUNT_UID)
    values = create_model.call_args.kwargs["values"]
    assert values["name"] == "Daily IVV observation"
    assert values["enabled"] is False
    assert "job_uid" not in values
    assert {key: values[key] for key in RETIRED_JOB_COLUMN_VALUES} == RETIRED_JOB_COLUMN_VALUES


def test_create_rejects_a_second_configuration_for_the_universe() -> None:
    with (
        patch("alpaca_connectors.runtime.start_markets_engine"),
        patch.object(signal_job_configurations, "validate_signal_job_references"),
        patch.object(
            signal_job_configurations,
            "signal_job_configurations_for_universe",
            return_value=[configuration()],
        ),
        pytest.raises(ValueError, match="already has a signal configuration"),
    ):
        create_signal_job_configuration_row(
            name="Second",
            universe_uid=UNIVERSE_UID,
            account_uid=ACCOUNT_UID,
        )


def test_signal_metadata_is_materialized_without_publishing_weights() -> None:
    row = configuration(description="Observe the registered Universe.")
    signal = SimpleNamespace(signal_uid="signal-uid", get_explanation=Mock())

    with (
        patch(
            "alpaca_connectors.portfolios.build_alpaca_etf_holdings_signal",
            return_value=signal,
        ) as build_signal,
        patch("msm_portfolios.api.market_metadata.SignalMetadata.upsert") as upsert,
    ):
        signal_uid = _ensure_signal_metadata(row)

    assert signal_uid == "signal-uid"
    build_signal.assert_called_once_with(
        universe_uid=str(UNIVERSE_UID),
        account_uid=str(ACCOUNT_UID),
    )
    upsert.assert_called_once_with(
        signal_uid="signal-uid",
        signal_description="Observe the registered Universe.",
    )
    signal.get_explanation.assert_not_called()


def test_update_validates_a_changed_universe_and_refreshes_signal_metadata() -> None:
    current = configuration()
    updated = configuration(universe_uid=OTHER_UNIVERSE_UID)

    with (
        patch.object(signal_jobs, "get_signal_job_configuration", return_value=current),
        patch.object(signal_jobs, "validate_signal_job_references") as validate,
        patch.object(signal_jobs, "signal_job_configurations_for_universe", return_value=[]),
        patch.object(
            signal_jobs, "update_signal_job_configuration_row", return_value=updated
        ) as update_row,
        patch.object(signal_jobs, "_ensure_signal_metadata") as ensure_metadata,
    ):
        result = update_signal_job_configuration(
            CONFIGURATION_UID,
            universe_uid=str(OTHER_UNIVERSE_UID),
            name=" Renamed ",
            scheduled=False,
        )

    assert result is updated
    validate.assert_called_once_with(universe_uid=OTHER_UNIVERSE_UID, account_uid=ACCOUNT_UID)
    update_row.assert_called_once_with(
        CONFIGURATION_UID,
        values={
            "universe_uid": OTHER_UNIVERSE_UID,
            "account_uid": ACCOUNT_UID,
            "name": "Renamed",
            "enabled": False,
        },
    )
    ensure_metadata.assert_called_once_with(updated)


def test_update_rejects_a_universe_that_already_has_a_configuration() -> None:
    with (
        patch.object(signal_jobs, "get_signal_job_configuration", return_value=configuration()),
        patch.object(signal_jobs, "validate_signal_job_references"),
        patch.object(
            signal_jobs,
            "signal_job_configurations_for_universe",
            return_value=[configuration(uid=uuid.uuid4(), universe_uid=OTHER_UNIVERSE_UID)],
        ),
        patch.object(signal_jobs, "update_signal_job_configuration_row") as update_row,
        pytest.raises(ValueError, match="already has a signal configuration"),
    ):
        update_signal_job_configuration(CONFIGURATION_UID, universe_uid=OTHER_UNIVERSE_UID)

    update_row.assert_not_called()


def test_execution_runs_one_universe_signal_update_for_the_selected_configuration() -> None:
    row = configuration(enabled=False)
    materialization = SimpleNamespace(
        signal_uid="signal-uid",
        observed_at=dt.datetime(2026, 9, 4, 10, tzinfo=dt.UTC),
        signal_weight_row_count=503,
        asset_uids={"AAPL": "asset-uid"},
    )

    with (
        patch.object(signal_jobs, "get_signal_job_configuration", return_value=row) as get_row,
        patch(
            "alpaca_connectors.universes.run_asset_universe",
            return_value=materialization,
        ) as run_universe,
    ):
        result = execute_signal_configuration(str(CONFIGURATION_UID))

    # A configuration left out of the scheduled run still runs when it is selected.
    get_row.assert_called_once_with(str(CONFIGURATION_UID))
    run_universe.assert_called_once_with(UNIVERSE_UID, account_uid=ACCOUNT_UID)
    assert result["configuration_uid"] == str(CONFIGURATION_UID)
    assert result["signal_uid"] == "signal-uid"
    assert result["signal_weight_row_count"] == 503
    assert result["asset_count"] == 1


def test_execution_of_a_missing_configuration_fails() -> None:
    with (
        patch.object(signal_jobs, "get_signal_job_configuration", return_value=None),
        pytest.raises(LookupError, match="Signal configuration"),
    ):
        execute_signal_configuration(str(CONFIGURATION_UID))
