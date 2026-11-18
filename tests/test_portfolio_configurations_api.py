from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from alpaca_connectors.api.app.schemas import (
    ConfigurationJobResponse,
    PortfolioConfigurationDetailResponse,
    PortfolioConfigurationResponse,
    PortfolioJobRunAcceptedResponse,
    PortfolioRebalanceConfigurationResponse,
)
from alpaca_connectors.api.app.services import configuration_jobs as job_shaping
from alpaca_connectors.api.app.services import portfolio_configurations as portfolio_service
from alpaca_connectors.operations import ConfigurationJobState
from alpaca_connectors.portfolios import (
    AlpacaPortfolioConfiguration,
    PortfolioRebalanceConfiguration,
)
from alpaca_connectors.portfolios.portfolio_history import (
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
ACCOUNT_UID = "88888888-8888-4888-8888-888888888888"
DEFINITION_UID = "99999999-9999-4999-8999-999999999999"


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
        name="Daily analytical portfolio",
        description="NYSE-close portfolio backtest.",
        signal_uid="signal-uid",
        bars_configuration_uid=BARS_CONFIGURATION_UID,
        rebalance_configuration_uid=REBALANCE_CONFIGURATION_UID,
        rebalance_strategy="calendar_event_signal",
        portfolio_uid=None,
        scheduled=True,
        job_uid=JOB_UID,
        upsample_frequency_id="1d",
        intraday_bar_interpolation_rule="ffill",
        valuation_column="close",
        valuation_maximum_staleness_seconds=86_400,
        fail_on_missing_prices=True,
        commission_fee=0.00018,
        job=ConfigurationJobResponse(
            uid=JOB_UID,
            schedule_type="crontab",
            schedule_every=None,
            schedule_period=None,
            schedule_expression="0 2 * * *",
            schedule_timezone="UTC",
            schedule_timezone_explicit=True,
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
    """A provided-weights portfolio, the only kind created (ADR 0024, ADR 0027)."""
    return {
        "name": "Daily IVV analytical portfolio",
        "description": "NYSE-close portfolio backtest.",
        "bars_configuration_uid": BARS_CONFIGURATION_UID,
        "rebalance_configuration_uid": REBALANCE_CONFIGURATION_UID,
        "upsample_frequency_id": "1d",
        "intraday_bar_interpolation_rule": "ffill",
        "valuation_column": "close",
        "valuation_maximum_staleness_seconds": 86_400,
        "fail_on_missing_prices": True,
        "commission_fee": 0.00018,
        "scheduled": False,
        "observations": [
            {"observed_at": "2026-11-16T14:00:00+00:00", "weights": {"ALPACA::x": 1.0}}
        ],
    }


def test_portfolio_job_response_preserves_backend_crontab_timezone() -> None:
    payload = job_shaping.schedule_payload(
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
            "description": "Use the latest weights observed at each NYSE close.",
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


def portfolio_row() -> AlpacaPortfolioConfiguration:
    now = dt.datetime(2026, 9, 5, 12, tzinfo=dt.UTC)
    return AlpacaPortfolioConfiguration.model_validate(
        {
            "uid": uuid.UUID(CONFIGURATION_UID),
            "name": "Daily analytical portfolio",
            "description": "NYSE-close portfolio backtest.",
            "owner_gateway_uid": "owner-uid",
            "owner_user_uid": "owner-uid",
            "account_uid": uuid.UUID(ACCOUNT_UID),
            "signal_definition_uid": uuid.UUID(DEFINITION_UID),
            "bars_configuration_uid": None,
            "rebalance_configuration_uid": uuid.UUID(REBALANCE_CONFIGURATION_UID),
            "portfolio_uid": None,
            "scheduled": True,
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
    rebalance = rebalance_row()

    with (
        patch.object(
            portfolio_service,
            "configuration_job_state",
            return_value=ConfigurationJobState(job=None, latest_runs={}),
        ) as job_state,
        patch.object(
            portfolio_service,
            "rebalance_configurations_by_uids",
            return_value={REBALANCE_CONFIGURATION_UID: rebalance},
        ) as rebalances_by_uids,
        patch(
            "alpaca_connectors.portfolios.signal_definitions.signal_definitions_by_uids",
            return_value={DEFINITION_UID: SimpleNamespace(key="key", signal_key="account/key")},
        ) as definitions_by_uids,
        patch(
            "alpaca_connectors.market_data.configurations.portfolio_bar_configurations",
            return_value={CONFIGURATION_UID: SimpleNamespace(uid=BARS_CONFIGURATION_UID)},
        ) as own_bars,
    ):
        result = portfolio_service._responses([row])

    assert result[0].uid == CONFIGURATION_UID
    assert result[0].rebalance_strategy == "calendar_event_signal"
    assert (result[0].job_uid, result[0].job, result[0].scheduled) == (None, None, True)
    job_state.assert_called_once_with("portfolio", [row.uid])
    rebalances_by_uids.assert_called_once_with([REBALANCE_CONFIGURATION_UID])
    # One read each for the signal definitions and the portfolios' own bars (ADR 0028).
    assert list(definitions_by_uids.call_args.args[0]) == [uuid.UUID(DEFINITION_UID)]
    own_bars.assert_called_once_with([row.uid])
    assert result[0].bars_configuration_uid == BARS_CONFIGURATION_UID
    assert (result[0].account_uid, result[0].adopted) == (ACCOUNT_UID, True)
    # Its definition's key, readable on the configuration (issue #43).
    assert result[0].key == "key"


def test_rebalance_crud_serializes_storage_domain_models(api_client) -> None:
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
    client = api_client

    with patch(
        "alpaca_connectors.api.app.services.portfolio_configurations.list_rebalance_configurations",
        return_value=([row], 1),
    ):
        listed = client.get("/v1/portfolio-rebalance-configurations")
    with patch(
        "alpaca_connectors.api.app.services.portfolio_configurations.create_rebalance_configuration",
        return_value=row,
    ):
        created = client.post("/v1/portfolio-rebalance-configurations", json=request)
    with patch(
        "alpaca_connectors.api.app.services.portfolio_configurations.get_rebalance_configuration",
        return_value=row,
    ):
        retrieved = client.get(
            f"/v1/portfolio-rebalance-configurations/{REBALANCE_CONFIGURATION_UID}"
        )
    with patch(
        "alpaca_connectors.api.app.services.portfolio_configurations.update_rebalance_configuration",
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


def test_portfolio_discovery_columns_are_renderable(api_client) -> None:
    result = api_client.get("/v1/portfolio-configurations/discovery")

    assert result.status_code == 200
    assert [column["id"] for column in result.json()["list"]["columns"]] == [
        "name",
        "account-uid",
        "signal-definition-uid",
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


def test_only_provided_weights_portfolios_are_created(api_client) -> None:
    # ADR 0027: a portfolio is created only from provided weights, and names no signal
    # configuration; ADR 0028: it names its account and one of its account's signals.
    signal_based = {**create_request(), "signal_configuration_uid": SIGNAL_CONFIGURATION_UID}
    del signal_based["observations"]
    assert api_client.post("/v1/portfolio-configurations", json=signal_based).status_code == 422
    provided = "/v1/portfolio-configurations/provided-weights"
    assert api_client.post(provided, json=signal_based).status_code == 422

    invalid = create_request()
    invalid["environment_uid"] = "77777777-7777-4777-8777-777777777777"
    assert api_client.post(provided, json=invalid).status_code == 422


def test_portfolio_create_rejects_unimplemented_rebalance_and_interpolation(api_client) -> None:
    invalid_strategy = create_request()
    invalid_strategy["rebalance_strategy"] = "time_weighted"
    invalid_interpolation = create_request()
    invalid_interpolation["intraday_bar_interpolation_rule"] = "linear"

    client = api_client
    provided = "/v1/portfolio-configurations/provided-weights"
    assert client.post(provided, json=invalid_strategy).status_code == 422
    assert client.post(provided, json=invalid_interpolation).status_code == 422


def test_portfolio_run_returns_jobrun_status_url_without_arguments(api_client) -> None:
    accepted = PortfolioJobRunAcceptedResponse(
        configuration_uid=CONFIGURATION_UID,
        job_uid=JOB_UID,
        job_run_uid=JOB_RUN_UID,
        status="PENDING",
        status_url=f"/v1/operations/job-runs/{JOB_RUN_UID}",
    )
    with patch(
        "alpaca_connectors.api.app.routers.portfolio_configurations.run_configuration",
        return_value=accepted,
    ) as run:
        result = api_client.post(f"/v1/portfolio-configurations/{CONFIGURATION_UID}/actions/run")

    assert result.status_code == 202
    assert result.json()["status_url"] == f"/v1/operations/job-runs/{JOB_RUN_UID}"
    run.assert_called_once_with(CONFIGURATION_UID)


def test_portfolio_detail_resolves_business_labels_and_value_history() -> None:
    now = dt.datetime(2026, 9, 5, 12, tzinfo=dt.UTC)
    bars = SimpleNamespace(
        uid=uuid.UUID(BARS_CONFIGURATION_UID),
        name="Daily portfolio bars",
        description="Daily SIP adjusted prices.",
        enabled=True,
        scheduled=False,
        account_uid=uuid.UUID("88888888-8888-4888-8888-888888888888"),
        asset_source="portfolios",
        asset_uids=[],
        frequency_id="1d",
        feed="sip",
        adjustment="all",
    )
    history = PortfolioHistory(
        materialized=True,
        portfolio_identifier="ALPACA_PORTFOLIO__111",
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
    bounds = SimpleNamespace(first_observation_at=now, last_observation_at=now)

    with (
        patch.object(
            portfolio_service, "get_portfolio_configuration", return_value=portfolio_row()
        ),
        patch.object(portfolio_service, "_responses", return_value=[response()]),
        patch.object(
            portfolio_service,
            "get_rebalance_configuration",
            return_value=rebalance_row(),
        ),
        patch(
            "alpaca_connectors.market_data.configurations.portfolio_bar_configuration",
            return_value=bars,
        ),
        patch("alpaca_connectors.account.services.get_account_registration", return_value=account),
        patch(
            "alpaca_connectors.portfolios.signal_history.read_signal_observation_bounds",
            return_value=bounds,
        ),
        patch(
            "alpaca_connectors.portfolios.signal_assets.assets_for_signals",
            return_value=["ALPACA::a", "ALPACA::b"],
        ),
        patch.object(portfolio_service, "read_portfolio_history", return_value=history),
    ):
        detail = portfolio_service.get_configuration_detail(
            CONFIGURATION_UID,
            observation_limit=100,
        )

    assert isinstance(detail, PortfolioConfigurationDetailResponse)
    assert detail.provided_weights.signal_uid == "signal-uid"
    assert detail.provided_weights.asset_count == 2
    assert (
        detail.linked_bars.asset_source_name == "Every asset the portfolio's signal has published"
    )
    assert detail.linked_bars.asset_count is None
    # An unscheduled bars configuration stays linked, and the detail shows it is not updated daily.
    assert detail.linked_bars.scheduled is False
    assert detail.linked_rebalance.name == "NYSE close"
    assert detail.canonical_portfolio.observation_count == 1
    assert detail.canonical_portfolio.latest_close == 103.25


def test_portfolio_detail_route_loads_latest_100_observations(api_client) -> None:
    detail = PortfolioConfigurationDetailResponse.model_validate(
        {
            **response().model_dump(mode="json"),
            "provided_weights": {
                "signal_uid": "signal-uid",
                "first_observation_at": None,
                "last_observation_at": None,
                "asset_count": 0,
            },
            "linked_bars": {
                "name": "Daily portfolio bars",
                "description": None,
                "enabled": True,
                "scheduled": True,
                "account_name": "Paper account",
                "account_environment": "paper",
                "asset_source": "portfolios",
                "asset_source_name": "Every asset of the live portfolios that use these prices",
                "asset_count": None,
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
        "alpaca_connectors.api.app.routers.portfolio_configurations.get_configuration_detail",
        return_value=detail,
    ) as get_detail:
        result = api_client.get(
            f"/v1/portfolio-configurations/{CONFIGURATION_UID}?observation_limit=100"
        )

    assert result.status_code == 200
    assert result.json()["provided_weights"]["signal_uid"] == "signal-uid"
    assert "linked_signal" not in result.json()
    get_detail.assert_called_once_with(CONFIGURATION_UID, observation_limit=100)


def test_only_the_accounts_owner_creates_its_portfolio(api_client, local_caller) -> None:
    """ADR 0028: the account's owner creates its portfolios, which follow one of its signals."""
    body = {
        "name": "Momentum basket",
        "account_uid": ACCOUNT_UID,
        "signal_definition_uid": DEFINITION_UID,
        "rebalance_configuration_uid": REBALANCE_CONFIGURATION_UID,
        "bars": {"feed": "iex", "adjustment": "raw"},
    }
    accounts = "alpaca_connectors.api.app.services.accounts.get_account_registration"
    owned = {"account_uid": ACCOUNT_UID, "owner_gateway_uid": "x", "owner_user_uid": "x"}
    with (
        patch(accounts, return_value=owned),
        patch.object(portfolio_service, "create_portfolio") as create,
    ):
        refused = api_client.post("/v1/portfolio-configurations", json=body)
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "account_owned_by_another"
    create.assert_not_called()

    owned = {**owned, "owner_gateway_uid": local_caller.uid, "owner_user_uid": local_caller.uid}
    with (
        patch(accounts, return_value=owned),
        patch.object(
            portfolio_service, "create_portfolio", side_effect=LookupError("stop")
        ) as create,
    ):
        api_client.post("/v1/portfolio-configurations", json=body)
    kwargs = create.call_args.kwargs
    assert (kwargs["account_uid"], kwargs["signal_definition_uid"]) == (ACCOUNT_UID, DEFINITION_UID)
    assert (kwargs["bars"]["feed"], kwargs["bars"]["adjustment"]) == ("iex", "raw")
    assert "bars_configuration_uid" not in kwargs


# ------------------------------------------------------------------------------------------------
# The create starts the first run when asked (issue #43)
# ------------------------------------------------------------------------------------------------
def _create_and_run(*, run: bool, created: bool = True, run_error=None):
    from alpaca_connectors.account.ownership import AccountOwner
    from alpaca_connectors.api.app.schemas import ProvidedWeightsPortfolioCreateRequest
    from alpaca_connectors.api.app.services import portfolio_configurations as service

    creation = SimpleNamespace(
        configuration=SimpleNamespace(uid=uuid.UUID(CONFIGURATION_UID)),
        published=None,
        portfolio_uid="77777777-7777-4777-8777-000000000007",
        portfolio_unique_identifier="ALPACA_PORTFOLIO__X",
        created=created,
        bars_configuration_created=created,
        rebalance_configuration_created=False,
    )
    accepted = PortfolioJobRunAcceptedResponse(
        configuration_uid=CONFIGURATION_UID,
        job_uid=JOB_UID,
        job_run_uid=JOB_RUN_UID,
        status="PENDING",
        status_url=f"/v1/operations/job-runs/{JOB_RUN_UID}",
    )
    request = ProvidedWeightsPortfolioCreateRequest.model_validate(
        {**create_request(), "key": "hunch-17", "run": run}
    )
    owner = AccountOwner.main_sequence_user("owner-uid")
    with (
        patch.object(service, "create_provided_weights_portfolio", return_value=creation) as make,
        patch.object(service, "_responses", return_value=["configuration"]),
        patch.object(
            service, "run_configuration", side_effect=run_error, return_value=accepted
        ) as start,
        # The answer's fields, without validating the stand-in configuration.
        patch.object(service, "ProvidedWeightsPortfolioCreateResponse", SimpleNamespace),
    ):
        response = service.create_provided_configuration(request, owner=owner)
    return make, start, response, accepted


def test_the_create_starts_the_first_run_and_answers_it() -> None:
    make, start, response, accepted = _create_and_run(run=True)

    # The key reaches the create; run is the API's, not the package's.
    assert make.call_args.kwargs["key"] == "hunch-17"
    assert "run" not in make.call_args.kwargs
    start.assert_called_once_with(CONFIGURATION_UID)
    assert (response.run, response.run_error, response.created) == (accepted, None, True)


def test_a_run_that_does_not_start_never_fails_the_create() -> None:
    _, _, response, _ = _create_and_run(
        run=True, run_error=ValueError("Portfolio preflight is blocked: no prices.")
    )
    assert response.run is None
    assert response.run_error == (
        "Portfolio preflight is blocked: no prices. The scheduled run covers it."
    )

    _, _, response, _ = _create_and_run(run=True, run_error=RuntimeError("secret detail"))
    assert response.run_error == (
        "The portfolio Job could not be started (RuntimeError). The scheduled run covers it."
    )


def test_no_run_is_started_unasked_or_for_a_portfolio_not_created_again() -> None:
    for run, created in ((False, True), (True, False)):
        _, start, response, _ = _create_and_run(run=run, created=created)
        start.assert_not_called()
        assert (response.run, response.run_error, response.weights) == (None, None, None)
