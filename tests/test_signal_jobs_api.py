from __future__ import annotations

import datetime as dt
from unittest.mock import patch

from api.app.main import app
from api.app.schemas import (
    SignalJobConfigurationResponse,
    SignalJobRunAcceptedResponse,
    SignalObservationAssetResponse,
    SignalObservationsResponse,
)
from fastapi.testclient import TestClient

CONFIGURATION_UID = "11111111-1111-4111-8111-111111111111"
UNIVERSE_UID = "22222222-2222-4222-8222-222222222222"
ACCOUNT_UID = "33333333-3333-4333-8333-333333333333"
JOB_UID = "44444444-4444-4444-8444-444444444444"
JOB_RUN_UID = "55555555-5555-4555-8555-555555555555"
ENVIRONMENT_UID = "66666666-6666-4666-8666-666666666666"


def response() -> SignalJobConfigurationResponse:
    now = dt.datetime(2026, 9, 4, 10, tzinfo=dt.UTC)
    return SignalJobConfigurationResponse(
        uid=CONFIGURATION_UID,
        name="Daily IVV observation",
        description=None,
        signal_uid="signal-uid",
        universe_uid=UNIVERSE_UID,
        account_uid=ACCOUNT_UID,
        job_uid=JOB_UID,
        enabled=True,
        schedule_type="interval",
        schedule_every=1,
        schedule_period="days",
        schedule_expression=None,
        schedule_start_time=None,
        cpu_request="0.25",
        memory_request="0.5",
        max_runtime_seconds=3600,
        spot=False,
        lifecycle_state="ready",
        last_error=None,
        job_image_status="ready",
        job_automatic_deployment=True,
        latest_run_status=None,
        latest_run_at=None,
        created_at=now,
        updated_at=now,
    )


def create_request() -> dict:
    return {
        "name": "Daily IVV observation",
        "description": None,
        "universe_uid": UNIVERSE_UID,
        "account_uid": ACCOUNT_UID,
        "enabled": True,
        "schedule_type": "interval",
        "schedule_every": 1,
        "schedule_period": "days",
        "schedule_expression": None,
        "schedule_start_time": None,
        "cpu_request": "0.25",
        "memory_request": "0.5",
        "max_runtime_seconds": 3600,
        "spot": False,
    }


def test_signal_discovery_columns_match_the_local_renderer_contract() -> None:
    result = TestClient(app).get("/v1/signal-jobs/discovery")

    assert result.status_code == 200
    assert [column["id"] for column in result.json()["list"]["columns"]] == [
        "name",
        "signal-uid",
        "lifecycle-state",
        "schedule-type",
        "job-image-status",
        "latest-run-status",
        "latest-run-at",
        "enabled",
    ]


def test_signal_create_forwards_configuration_without_an_environment_selector() -> None:
    with patch(
        "api.app.routers.signal_jobs.create_configuration",
        return_value=response(),
    ) as create:
        result = TestClient(app).post("/v1/signal-jobs", json=create_request())

    assert result.status_code == 201
    assert result.json()["job_uid"] == JOB_UID
    assert result.json()["signal_uid"] == "signal-uid"
    assert "environment_uid" not in result.json()
    request = create.call_args.args[0]
    assert request.universe_uid == UNIVERSE_UID
    assert request.account_uid == ACCOUNT_UID


def test_signal_create_rejects_a_caller_supplied_environment() -> None:
    request = create_request()
    request["environment_uid"] = ENVIRONMENT_UID

    result = TestClient(app).post("/v1/signal-jobs", json=request)

    assert result.status_code == 422


def test_signal_run_returns_the_accepted_job_run_without_arguments() -> None:
    accepted = SignalJobRunAcceptedResponse(
        configuration_uid=CONFIGURATION_UID,
        job_uid=JOB_UID,
        job_run_uid=JOB_RUN_UID,
        status="PENDING",
        status_url=f"/v1/operations/job-runs/{JOB_RUN_UID}",
    )
    with patch(
        "api.app.routers.signal_jobs.run_configuration",
        return_value=accepted,
    ) as run:
        result = TestClient(app).post(
            f"/v1/signal-jobs/{CONFIGURATION_UID}/actions/run",
        )

    assert result.status_code == 202
    assert result.json()["job_run_uid"] == JOB_RUN_UID
    run.assert_called_once_with(CONFIGURATION_UID)


def test_signal_observations_are_loaded_on_demand_with_the_default_limit() -> None:
    observed_at = dt.datetime(2026, 9, 4, 14, tzinfo=dt.UTC)
    observations = SignalObservationsResponse(
        configuration_uid=CONFIGURATION_UID,
        signal_uid="signal-uid",
        observation_count=1,
        asset_count=1,
        time_indexes=[observed_at],
        assets=[
            SignalObservationAssetResponse(
                asset_identifier="ALPACA::asset-uuid",
                symbol="AAPL",
                name="Apple Inc.",
                weights=[0.075],
            )
        ],
    )
    with patch(
        "api.app.routers.signal_jobs.get_configuration_observations",
        return_value=observations,
    ) as get_observations:
        result = TestClient(app).get(
            f"/v1/signal-jobs/{CONFIGURATION_UID}/observations",
        )

    assert result.status_code == 200
    assert result.headers["cache-control"] == "no-store"
    assert result.json()["time_indexes"] == ["2026-09-04T14:00:00Z"]
    assert result.json()["assets"][0]["weights"] == [0.075]
    get_observations.assert_called_once_with(CONFIGURATION_UID, limit=100)


def test_signal_observation_limit_is_bounded() -> None:
    result = TestClient(app).get(
        f"/v1/signal-jobs/{CONFIGURATION_UID}/observations?limit=101",
    )

    assert result.status_code == 422
