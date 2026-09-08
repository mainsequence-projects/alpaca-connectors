from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from api.app.main import app
from api.app.schemas import (
    PortfolioConfigurationDetailResponse,
    PortfolioConfigurationResponse,
    PortfolioJobResponse,
    PortfolioJobRunAcceptedResponse,
    PortfolioRebalanceConfigurationResponse,
)
from api.app.services import portfolio_configurations as portfolio_service
from fastapi.testclient import TestClient

from src.portfolios import AlpacaETFPortfolioConfiguration, PortfolioRebalanceConfiguration
from src.portfolios.portfolio_history import (
    PortfolioHistory,
    PortfolioPerformance,
    PortfolioValueObservation,
)

CONFIGURATION_UID = "11111111-1111-4111-8111-111111111111"
SIGNAL_CONFIGURATION_UID = "22222222-2222-4222-8222-222222222222"
BARS_CONFIGURATION_UID = "33333333-3333-4333-8333-333333333333"
REBALANCE_CONFIGURATION_UID = "44444444-4444-4444-8444-444444444444"
JOB_UID = "55555555-5555-4555-8555-555555555555"
JOB_RUN_UID = "66666666-6666-4666-8666-666666666666"


def performance_summary(
    *,
    observation_count: int,
    period_start: dt.datetime | None = None,
    period_end: dt.datetime | None = None,
) -> PortfolioPerformance:
    return PortfolioPerformance(
        methodology="empyrical-reloaded",
        frequency="daily",
        annualization_factor=252,
        risk_free_rate=0.0,
        observation_count=observation_count,
        return_observation_count=max(observation_count - 1, 0),
        period_start=period_start,
        period_end=period_end,
        total_return=None,
        annualized_return=None,
        annualized_volatility=None,
        sharpe_ratio=None,
        sortino_ratio=None,
        max_drawdown=None,
        calmar_ratio=None,
        best_period_return=None,
        worst_period_return=None,
        positive_period_ratio=None,
    )


def response() -> PortfolioConfigurationResponse:
    now = dt.datetime(2026, 9, 5, 10, tzinfo=dt.UTC)
    return PortfolioConfigurationResponse(
        uid=CONFIGURATION_UID,
        name="Daily IVV analytical portfolio",
        description="NYSE-close ETF portfolio backtest.",
        signal_configuration_uid=SIGNAL_CONFIGURATION_UID,
        signal_uid="signal-uid",
        bars_configuration_uid=BARS_CONFIGURATION_UID,
        rebalance_configuration_uid=REBALANCE_CONFIGURATION_UID,
        rebalance_strategy="calendar_event_signal",
        portfolio_uid=None,
        job_uid=JOB_UID,
        upsample_frequency_id="1d",
        intraday_bar_interpolation_rule="ffill",
        valuation_column="close",
        valuation_maximum_staleness_seconds=86_400,
        fail_on_missing_prices=True,
        commission_fee=0.00018,
        job=PortfolioJobResponse(
            uid=JOB_UID,
            schedule_type="interval",
            schedule_every=1,
            schedule_period="days",
            schedule_expression=None,
            schedule_timezone=None,
            schedule_timezone_explicit=None,
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
        "description": "NYSE-close ETF portfolio backtest.",
        "signal_configuration_uid": SIGNAL_CONFIGURATION_UID,
        "bars_configuration_uid": BARS_CONFIGURATION_UID,
        "rebalance_configuration_uid": REBALANCE_CONFIGURATION_UID,
        "upsample_frequency_id": "1d",
        "intraday_bar_interpolation_rule": "ffill",
        "valuation_column": "close",
        "valuation_maximum_staleness_seconds": 86_400,
        "fail_on_missing_prices": True,
        "commission_fee": 0.00018,
        "job": {
            "schedule_type": "interval",
            "schedule_every": 1,
            "schedule_period": "days",
            "schedule_expression": None,
            "schedule_timezone": None,
            "schedule_start_time": None,
            "cpu_request": "0.25",
            "memory_request": "0.5",
            "max_runtime_seconds": 3600,
            "spot": False,
        },
    }


def test_portfolio_job_response_preserves_backend_crontab_timezone() -> None:
    payload = portfolio_service._schedule_payload(
        SimpleNamespace(
            task_schedule={
                "schedule": {
                    "type": "crontab",
                    "expression": "0 20 * * 1-5",
                    "start_time": None,
                    "timezone": "UTC",
                    "timezone_explicit": False,
                }
            }
        )
    )

    assert payload == {
        "schedule_type": "crontab",
        "schedule_every": None,
        "schedule_period": None,
        "schedule_expression": "0 20 * * 1-5",
        "schedule_timezone": "UTC",
        "schedule_timezone_explicit": False,
        "schedule_start_time": None,
    }


def rebalance_row() -> PortfolioRebalanceConfiguration:
    now = dt.datetime(2026, 9, 5, 12, tzinfo=dt.UTC)
    return PortfolioRebalanceConfiguration.model_validate(
        {
            "uid": uuid.UUID(REBALANCE_CONFIGURATION_UID),
            "name": "NYSE close",
            "description": "Use the latest observed ETF weight frame at each NYSE close.",
            "strategy": "calendar_event_signal",
            "calendar_identifier": "NYSE",
            "session_label": "regular",
            "rebalance_event": "market_close",
            "event_offset_seconds": 0,
            "rebalance_cadence": "every_session",
            "rebalance_weekday": 0,
            "created_at": now,
            "updated_at": now,
        }
    )


def portfolio_row() -> AlpacaETFPortfolioConfiguration:
    now = dt.datetime(2026, 9, 5, 12, tzinfo=dt.UTC)
    return AlpacaETFPortfolioConfiguration.model_validate(
        {
            "uid": uuid.UUID(CONFIGURATION_UID),
            "name": "Daily IVV analytical portfolio",
            "description": "NYSE-close ETF portfolio backtest.",
            "signal_configuration_uid": uuid.UUID(SIGNAL_CONFIGURATION_UID),
            "bars_configuration_uid": uuid.UUID(BARS_CONFIGURATION_UID),
            "rebalance_configuration_uid": uuid.UUID(REBALANCE_CONFIGURATION_UID),
            "portfolio_uid": None,
            "job_uid": uuid.UUID(JOB_UID),
            "upsample_frequency_id": "1d",
            "intraday_bar_interpolation_rule": "ffill",
            "valuation_column": "close",
            "valuation_maximum_staleness_seconds": 86_400,
            "fail_on_missing_prices": True,
            "commission_fee": 0.00018,
            "created_at": now,
            "updated_at": now,
        }
    )


def test_portfolio_response_resolves_linked_metatables_with_bulk_uid_queries() -> None:
    row = portfolio_row()
    signal = SimpleNamespace(uid=uuid.UUID(SIGNAL_CONFIGURATION_UID))
    rebalance = rebalance_row()

    with (
        patch.object(portfolio_service, "_runtime_by_job_uid", return_value=({}, {})),
        patch.object(
            portfolio_service,
            "signal_job_configurations_by_uids",
            return_value={SIGNAL_CONFIGURATION_UID: signal},
        ) as signals_by_uids,
        patch.object(
            portfolio_service,
            "rebalance_configurations_by_uids",
            return_value={REBALANCE_CONFIGURATION_UID: rebalance},
        ) as rebalances_by_uids,
        patch.object(
            portfolio_service,
            "signal_uid_for_configuration",
            return_value="signal-uid",
        ),
    ):
        result = portfolio_service._responses([row])

    assert result[0].uid == CONFIGURATION_UID
    assert result[0].rebalance_strategy == "calendar_event_signal"
    signals_by_uids.assert_called_once_with([SIGNAL_CONFIGURATION_UID])
    rebalances_by_uids.assert_called_once_with([REBALANCE_CONFIGURATION_UID])


def test_rebalance_crud_serializes_storage_domain_models() -> None:
    row = rebalance_row()
    request = {
        "name": row.name,
        "description": row.description,
        "strategy": row.strategy,
        "calendar_identifier": row.calendar_identifier,
        "session_label": row.session_label,
        "rebalance_event": row.rebalance_event,
        "event_offset_seconds": row.event_offset_seconds,
        "rebalance_cadence": row.rebalance_cadence,
        "rebalance_weekday": row.rebalance_weekday,
    }
    client = TestClient(app)

    with patch(
        "api.app.services.portfolio_configurations.list_rebalance_configurations",
        return_value=([row], 1),
    ):
        listed = client.get("/v1/portfolio-rebalance-configurations")
    with patch(
        "api.app.services.portfolio_configurations.create_rebalance_configuration",
        return_value=row,
    ):
        created = client.post("/v1/portfolio-rebalance-configurations", json=request)
    with patch(
        "api.app.services.portfolio_configurations.get_rebalance_configuration",
        return_value=row,
    ):
        retrieved = client.get(
            f"/v1/portfolio-rebalance-configurations/{REBALANCE_CONFIGURATION_UID}"
        )
    with patch(
        "api.app.services.portfolio_configurations.update_rebalance_configuration",
        return_value=row,
    ):
        updated = client.patch(
            f"/v1/portfolio-rebalance-configurations/{REBALANCE_CONFIGURATION_UID}",
            json={"name": row.name},
        )

    assert [created.status_code, retrieved.status_code, updated.status_code] == [201, 200, 200]
    for response in (created, retrieved, updated):
        assert PortfolioRebalanceConfigurationResponse.model_validate(response.json()).uid == (
            REBALANCE_CONFIGURATION_UID
        )
    assert listed.status_code == 200
    assert listed.json()["items"][0]["uid"] == REBALANCE_CONFIGURATION_UID


def test_portfolio_discovery_columns_are_renderable() -> None:
    result = TestClient(app).get("/v1/portfolio-configurations/discovery")

    assert result.status_code == 200
    assert [column["id"] for column in result.json()["list"]["columns"]] == [
        "name",
        "rebalance-strategy",
        "job-image-status",
        "latest-run-status",
        "latest-run-at",
        "updated-at",
    ]
    for column in result.json()["list"]["columns"]:
        if column["id"] not in {
            "name",
            "rebalance-strategy",
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
    assert (
        client.post("/v1/portfolio-configurations", json=invalid_interpolation).status_code == 422
    )


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


def test_portfolio_detail_resolves_business_labels_and_value_history() -> None:
    now = dt.datetime(2026, 9, 5, 12, tzinfo=dt.UTC)
    signal = SimpleNamespace(
        uid=uuid.UUID(SIGNAL_CONFIGURATION_UID),
        name="Daily IVV observation",
        description="Observe current IVV weights.",
        enabled=True,
        universe_uid=uuid.UUID("77777777-7777-4777-8777-777777777777"),
        account_uid=uuid.UUID("88888888-8888-4888-8888-888888888888"),
    )
    bars = SimpleNamespace(
        uid=uuid.UUID(BARS_CONFIGURATION_UID),
        name="Daily IVV bars",
        description="Daily SIP adjusted prices.",
        enabled=True,
        account_uid=signal.account_uid,
        asset_source="universe",
        universe_uid=signal.universe_uid,
        asset_uids=[],
        frequency_id="1d",
        feed="sip",
        adjustment="all",
    )
    history = PortfolioHistory(
        materialized=True,
        portfolio_identifier="ALPACA_ETF_PORTFOLIO__111",
        description="Canonical portfolio metadata.",
        calendar_name="US equities",
        calendar_type="exchange",
        calendar_timezone="America/New_York",
        calendar_valid_from=dt.date(2018, 1, 1),
        calendar_valid_to=dt.date(2027, 1, 1),
        backtest_price_column="close",
        observations=(
            PortfolioValueObservation(
                time_index=now,
                close=103.25,
                period_return=0.0125,
                calculated_close=103.25,
                close_time=now,
            ),
        ),
        total_observation_count=1,
        performance=performance_summary(
            observation_count=1,
            period_start=now,
            period_end=now,
        ),
    )
    account = {"account_name": "Paper account", "is_paper": True}
    universe = {
        "display_name": "S&P 500 holdings",
        "symbol": "IVV",
        "asset_count": 504,
    }

    with (
        patch.object(portfolio_service, "get_portfolio_configuration", return_value=portfolio_row()),
        patch.object(portfolio_service, "_responses", return_value=[response()]),
        patch.object(
            portfolio_service,
            "signal_job_configurations_by_uids",
            return_value={SIGNAL_CONFIGURATION_UID: signal},
        ),
        patch.object(
            portfolio_service,
            "get_rebalance_configuration",
            return_value=rebalance_row(),
        ),
        patch("src.market_data.get_bar_configuration", return_value=bars),
        patch("src.account.services.get_account_registration", return_value=account),
        patch("src.universes.get_asset_universe_view", return_value=universe),
        patch.object(portfolio_service, "read_portfolio_history", return_value=history),
    ):
        detail = portfolio_service.get_configuration_detail(
            CONFIGURATION_UID,
            observation_limit=100,
        )

    assert isinstance(detail, PortfolioConfigurationDetailResponse)
    assert detail.linked_signal.name == "Daily IVV observation"
    assert detail.linked_signal.universe_name == "S&P 500 holdings"
    assert detail.linked_bars.asset_source_name == "S&P 500 holdings (IVV)"
    assert detail.linked_bars.asset_count == 504
    assert detail.linked_rebalance.name == "NYSE close"
    assert detail.canonical_portfolio.observation_count == 1
    assert detail.canonical_portfolio.latest_close == 103.25


def test_portfolio_detail_route_loads_latest_100_observations() -> None:
    detail = PortfolioConfigurationDetailResponse.model_validate(
        {
            **response().model_dump(mode="json"),
            "linked_signal": {
                "name": "Daily IVV observation",
                "description": None,
                "enabled": True,
                "universe_name": "S&P 500 holdings",
                "universe_symbol": "IVV",
                "account_name": "Paper account",
                "account_environment": "paper",
            },
            "linked_bars": {
                "name": "Daily IVV bars",
                "description": None,
                "enabled": True,
                "account_name": "Paper account",
                "account_environment": "paper",
                "asset_source": "universe",
                "asset_source_name": "S&P 500 holdings (IVV)",
                "asset_count": 504,
                "frequency_id": "1d",
                "feed": "sip",
                "adjustment": "all",
            },
            "linked_rebalance": {
                "name": "NYSE close",
                "description": None,
                "strategy": "calendar_event_signal",
                "calendar_identifier": "NYSE",
                "session_label": "regular",
                "rebalance_event": "market_close",
                "event_offset_seconds": 0,
                "rebalance_cadence": "every_session",
                "rebalance_weekday": 0,
            },
            "canonical_portfolio": {
                "materialized": True,
                "description": None,
                "calendar_name": "US equities",
                "calendar_type": "exchange",
                "calendar_timezone": "America/New_York",
                "calendar_valid_from": "2018-01-01",
                "calendar_valid_to": "2027-01-01",
                "backtest_price_column": "close",
                "observation_count": 0,
                "total_observation_count": 0,
                "history_window_truncated": False,
                "latest_observation_at": None,
                "latest_close": None,
                "latest_period_return": None,
                "performance": {
                    "methodology": "empyrical-reloaded",
                    "frequency": "daily",
                    "annualization_factor": 252,
                    "risk_free_rate": 0.0,
                    "observation_count": 0,
                    "return_observation_count": 0,
                    "period_start": None,
                    "period_end": None,
                    "total_return": None,
                    "annualized_return": None,
                    "annualized_volatility": None,
                    "sharpe_ratio": None,
                    "sortino_ratio": None,
                    "max_drawdown": None,
                    "calmar_ratio": None,
                    "best_period_return": None,
                    "worst_period_return": None,
                    "positive_period_ratio": None,
                },
                "observations": [],
            },
        }
    )
    with patch(
        "api.app.routers.portfolio_configurations.get_configuration_detail",
        return_value=detail,
    ) as get_detail:
        result = TestClient(app).get(
            f"/v1/portfolio-configurations/{CONFIGURATION_UID}?observation_limit=100"
        )

    assert result.status_code == 200
    assert result.json()["linked_signal"]["name"] == "Daily IVV observation"
    get_detail.assert_called_once_with(CONFIGURATION_UID, observation_limit=100)
