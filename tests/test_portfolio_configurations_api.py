from __future__ import annotations

import datetime as dt
from unittest.mock import patch

from api.app.main import app
from api.app.schemas import (
    PortfolioConfigurationResponse,
    PortfolioJobResponse,
    PortfolioJobRunAcceptedResponse,
)
from fastapi.testclient import TestClient

CONFIGURATION_UID = "11111111-1111-4111-8111-111111111111"
SIGNAL_CONFIGURATION_UID = "22222222-2222-4222-8222-222222222222"
BARS_CONFIGURATION_UID = "33333333-3333-4333-8333-333333333333"
REBALANCE_CONFIGURATION_UID = "44444444-4444-4444-8444-444444444444"
JOB_UID = "55555555-5555-4555-8555-555555555555"
JOB_RUN_UID = "66666666-6666-4666-8666-666666666666"


def response() -> PortfolioConfigurationResponse:
    now = dt.datetime(2026, 9, 5, 10, tzinfo=dt.UTC)
    return PortfolioConfigurationResponse(
        uid=CONFIGURATION_UID,
        name="Daily IVV analytical portfolio",
        description="Immediate-signal observation-time backtest.",
        signal_configuration_uid=SIGNAL_CONFIGURATION_UID,
        signal_uid="signal-uid",
        bars_configuration_uid=BARS_CONFIGURATION_UID,
        rebalance_configuration_uid=REBALANCE_CONFIGURATION_UID,
        rebalance_strategy="immediate_signal",
        portfolio_uid=None,
        job_uid=JOB_UID,
        upsample_frequency_id="1d",
        intraday_bar_interpolation_rule="ffill",
        valuation_column="close",
        portfolio_prices_frequency="1d",
        forward_fill_to_now=False,
        fail_on_missing_prices=True,
        commission_fee=0.00018,
        job=PortfolioJobResponse(
            uid=JOB_UID,
            schedule_type="interval",
            schedule_every=1,
            schedule_period="days",
            schedule_expression=None,
            schedule_start_time=None,
            cpu_request="0.25",
            memory_request="0.5",
            max_runtime_seconds=3600,
            spot=False,
            image_status="ready",
            automatic_deployment=True,
        ),
        latest_run_status=None,
        latest_run_at=None,
        created_at=now,
        updated_at=now,
    )


def create_request() -> dict:
    return {
        "name": "Daily IVV analytical portfolio",
        "description": "Immediate-signal observation-time backtest.",
        "signal_configuration_uid": SIGNAL_CONFIGURATION_UID,
        "bars_configuration_uid": BARS_CONFIGURATION_UID,
        "rebalance_configuration_uid": REBALANCE_CONFIGURATION_UID,
        "upsample_frequency_id": "1d",
        "intraday_bar_interpolation_rule": "ffill",
        "valuation_column": "close",
        "portfolio_prices_frequency": "1d",
        "forward_fill_to_now": False,
        "fail_on_missing_prices": True,
        "commission_fee": 0.00018,
        "job": {
            "schedule_type": "interval",
            "schedule_every": 1,
            "schedule_period": "days",
            "schedule_expression": None,
            "schedule_start_time": None,
            "cpu_request": "0.25",
            "memory_request": "0.5",
            "max_runtime_seconds": 3600,
            "spot": False,
        },
    }


def test_portfolio_discovery_columns_are_renderable() -> None:
    result = TestClient(app).get("/v1/portfolio-configurations/discovery")

    assert result.status_code == 200
    assert [column["id"] for column in result.json()["list"]["columns"]] == [
        "name",
        "rebalance-strategy",
        "portfolio-uid",
        "job-image-status",
        "latest-run-status",
        "latest-run-at",
        "updated-at",
    ]
    for column in result.json()["list"]["columns"]:
        if column["id"] not in {
            "name",
            "rebalance-strategy",
            "portfolio-uid",
            "latest-run-status",
        }:
            assert column.get("value_path") or column.get("data_type")


def test_portfolio_create_nests_job_settings_and_rejects_environment() -> None:
    with patch(
        "api.app.routers.portfolio_configurations.create_configuration",
        return_value=response(),
    ) as create:
        result = TestClient(app).post("/v1/portfolio-configurations", json=create_request())

    assert result.status_code == 201
    assert result.json()["job"]["schedule_type"] == "interval"
    assert "schedule_type" not in {
        key: value for key, value in result.json().items() if key != "job"
    }
    request = create.call_args.args[0]
    assert request.signal_configuration_uid == SIGNAL_CONFIGURATION_UID
    assert request.job.schedule_every == 1

    invalid = create_request()
    invalid["environment_uid"] = "77777777-7777-4777-8777-777777777777"
    assert TestClient(app).post("/v1/portfolio-configurations", json=invalid).status_code == 422


def test_portfolio_create_rejects_unimplemented_rebalance_and_interpolation() -> None:
    invalid_strategy = create_request()
    invalid_strategy["rebalance_strategy"] = "time_weighted"
    invalid_interpolation = create_request()
    invalid_interpolation["intraday_bar_interpolation_rule"] = "linear"

    client = TestClient(app)
    assert client.post("/v1/portfolio-configurations", json=invalid_strategy).status_code == 422
    assert client.post("/v1/portfolio-configurations", json=invalid_interpolation).status_code == 422


def test_portfolio_run_returns_jobrun_status_url_without_arguments() -> None:
    accepted = PortfolioJobRunAcceptedResponse(
        configuration_uid=CONFIGURATION_UID,
        job_uid=JOB_UID,
        job_run_uid=JOB_RUN_UID,
        status="PENDING",
        status_url=f"/v1/operations/job-runs/{JOB_RUN_UID}",
    )
    with patch(
        "api.app.routers.portfolio_configurations.run_configuration",
        return_value=accepted,
    ) as run:
        result = TestClient(app).post(
            f"/v1/portfolio-configurations/{CONFIGURATION_UID}/actions/run"
        )

    assert result.status_code == 202
    assert result.json()["status_url"] == f"/v1/operations/job-runs/{JOB_RUN_UID}"
    run.assert_called_once_with(CONFIGURATION_UID)
