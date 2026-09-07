"""API shaping for durable analytical ETF portfolio configurations."""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict
from typing import Any, Mapping

from src.operations import (
    create_portfolio_job_configuration,
    delete_portfolio_job_configuration,
    launch_portfolio_job,
    list_portfolio_job_runs,
    normalize_portfolio_job_settings,
    signal_job_configurations_by_uids,
    signal_uid_for_configuration,
    update_portfolio_job_configuration,
)
from src.portfolios import (
    PortfolioRebalanceConfiguration,
    create_rebalance_configuration,
    delete_rebalance_configuration,
    get_portfolio_configuration,
    get_rebalance_configuration,
    list_portfolio_configurations,
    list_rebalance_configurations,
    read_portfolio_history,
    rebalance_configurations_by_uids,
    update_rebalance_configuration,
)

from ..schemas import (
    PortfolioConfigurationCreateRequest,
    PortfolioConfigurationDetailResponse,
    PortfolioConfigurationResponse,
    PortfolioConfigurationUpdateRequest,
    PortfolioJobResponse,
    PortfolioJobRunAcceptedResponse,
    PortfolioJobRunResponse,
    PortfolioRebalanceConfigurationCreateRequest,
    PortfolioRebalanceConfigurationResponse,
    PortfolioRebalanceConfigurationUpdateRequest,
)
from .common import collection_response


def _value(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _schedule_payload(job: Any) -> dict[str, Any]:
    task_schedule = getattr(job, "task_schedule", None)
    schedule = _value(task_schedule, "schedule") if task_schedule is not None else None
    if schedule is None:
        return {
            "schedule_type": None,
            "schedule_every": None,
            "schedule_period": None,
            "schedule_expression": None,
            "schedule_timezone": None,
            "schedule_timezone_explicit": None,
            "schedule_start_time": None,
        }
    schedule_type = str(_value(schedule, "type", _value(schedule, "schedule_type", ""))).lower()
    if schedule_type == "interval":
        return {
            "schedule_type": "interval",
            "schedule_every": _value(schedule, "every"),
            "schedule_period": _value(schedule, "period"),
            "schedule_expression": None,
            "schedule_timezone": None,
            "schedule_timezone_explicit": None,
            "schedule_start_time": _value(schedule, "start_time"),
        }
    expression = _value(schedule, "expression")
    if not expression:
        expression = " ".join(
            str(_value(schedule, key, "*"))
            for key in ("minute", "hour", "day_of_month", "month_of_year", "day_of_week")
        )
    return {
        "schedule_type": "crontab",
        "schedule_every": None,
        "schedule_period": None,
        "schedule_expression": expression,
        "schedule_timezone": _value(schedule, "timezone"),
        "schedule_timezone_explicit": _value(schedule, "timezone_explicit"),
        "schedule_start_time": _value(schedule, "start_time"),
    }


def _job_response(job: Any | None) -> PortfolioJobResponse | None:
    if job is None:
        return None
    image_status = getattr(job, "image_status", None)
    image_status = getattr(image_status, "value", image_status)
    return PortfolioJobResponse(
        uid=str(job.uid),
        **_schedule_payload(job),
        cpu_request=(str(job.cpu_request) if job.cpu_request is not None else None),
        memory_request=(str(job.memory_request) if job.memory_request is not None else None),
        max_runtime_seconds=job.max_runtime_seconds,
        spot=bool(job.spot),
        image_status=(str(image_status) if image_status is not None else None),
        automatic_deployment=bool(job.automatic_deployment),
    )


def _runtime_by_job_uid(rows: list[Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    from mainsequence.client import JobRun
    from src.operations.platform_jobs import PlatformJob

    job_uids = list(dict.fromkeys(str(row.job_uid) for row in rows if row.job_uid is not None))
    if not job_uids:
        return {}, {}
    jobs = list(PlatformJob.filter(uid__in=job_uids, timeout=60))
    runs = list(JobRun.filter(job__uid__in=job_uids, timeout=60))
    jobs_by_uid = {str(job.uid): job for job in jobs}
    latest_by_job_uid: dict[str, Any] = {}
    minimum = dt.datetime.min.replace(tzinfo=dt.UTC)
    for run in runs:
        job_uid = str(run.job_uid or "")
        current = latest_by_job_uid.get(job_uid)
        if current is None or (run.execution_start or minimum) > (
            current.execution_start or minimum
        ):
            latest_by_job_uid[job_uid] = run
    return jobs_by_uid, latest_by_job_uid


def _canonical_status(value: Any) -> str:
    raw = getattr(value, "value", value)
    status = str(raw or "").strip().upper()
    return {
        "COMPLETE": "SUCCEEDED",
        "COMPLETED": "SUCCEEDED",
        "SUCCESS": "SUCCEEDED",
        "SUCCESSFUL": "SUCCEEDED",
        "ERROR": "FAILED",
        "ERRORED": "FAILED",
    }.get(status, status)


def _responses(rows: list[Any]) -> list[PortfolioConfigurationResponse]:
    if not rows:
        return []
    jobs, latest_runs = _runtime_by_job_uid(rows)
    signal_uids = list(dict.fromkeys(str(row.signal_configuration_uid) for row in rows))
    rebalance_uids = list(dict.fromkeys(str(row.rebalance_configuration_uid) for row in rows))
    signal_configurations = signal_job_configurations_by_uids(signal_uids)
    rebalance_configurations = rebalance_configurations_by_uids(rebalance_uids)
    responses: list[PortfolioConfigurationResponse] = []
    for row in rows:
        signal_configuration = signal_configurations.get(str(row.signal_configuration_uid))
        rebalance_configuration = rebalance_configurations.get(str(row.rebalance_configuration_uid))
        if signal_configuration is None or rebalance_configuration is None:
            raise RuntimeError(
                f"Portfolio configuration {row.uid!s} has an unresolved required relationship."
            )
        latest_run = latest_runs.get(str(row.job_uid))
        responses.append(
            PortfolioConfigurationResponse.model_validate(
                {
                    **row.model_dump(mode="json"),
                    "signal_uid": signal_uid_for_configuration(signal_configuration),
                    "rebalance_strategy": rebalance_configuration.strategy,
                    "job": _job_response(jobs.get(str(row.job_uid))),
                    "latest_run_status": (
                        _canonical_status(latest_run.status) if latest_run is not None else None
                    ),
                    "latest_run_at": (
                        latest_run.execution_start if latest_run is not None else None
                    ),
                }
            )
        )
    return responses


def list_configurations(
    *,
    limit: int,
    offset: int,
    search: str | None,
    signal_configuration_uid: str | None,
    bars_configuration_uid: str | None,
    ordering: str,
):
    rows, total = list_portfolio_configurations(
        limit=limit,
        offset=offset,
        search=search,
        signal_configuration_uid=signal_configuration_uid,
        bars_configuration_uid=bars_configuration_uid,
        ordering=ordering,
    )
    return collection_response(
        items=_responses(rows),
        total=total,
        limit=limit,
        offset=offset,
    )


def get_configuration(configuration_uid: str) -> PortfolioConfigurationResponse | None:
    row = get_portfolio_configuration(configuration_uid)
    return _responses([row])[0] if row is not None else None


def get_configuration_detail(
    configuration_uid: str,
    *,
    observation_limit: int,
) -> PortfolioConfigurationDetailResponse | None:
    """Resolve all business-facing portfolio detail and its latest value history."""
    from src.account.services import get_account_registration
    from src.market_data import get_bar_configuration
    from src.universes import get_asset_universe_view

    row = get_portfolio_configuration(configuration_uid)
    if row is None:
        return None
    base = _responses([row])[0]
    signal_configuration = signal_job_configurations_by_uids(
        [row.signal_configuration_uid]
    ).get(str(row.signal_configuration_uid))
    rebalance_configuration = get_rebalance_configuration(row.rebalance_configuration_uid)
    bars_configuration = get_bar_configuration(row.bars_configuration_uid)
    if signal_configuration is None or rebalance_configuration is None or bars_configuration is None:
        raise RuntimeError(
            f"Portfolio configuration {configuration_uid!s} has an unresolved required relationship."
        )

    account_cache: dict[str, Mapping[str, Any]] = {}

    def account_details(account_uid: Any) -> Mapping[str, Any]:
        key = str(account_uid)
        if key not in account_cache:
            account = get_account_registration(key)
            if account is None:
                raise RuntimeError(f"Portfolio detail references missing Alpaca account {key}.")
            account_cache[key] = account
        return account_cache[key]

    universe_cache: dict[str, Mapping[str, Any]] = {}

    def universe_details(universe_uid: Any) -> Mapping[str, Any]:
        key = str(universe_uid)
        if key not in universe_cache:
            universe = get_asset_universe_view(key)
            if universe is None:
                raise RuntimeError(f"Portfolio detail references missing Asset Universe {key}.")
            universe_cache[key] = universe
        return universe_cache[key]

    signal_account = account_details(signal_configuration.account_uid)
    signal_universe = universe_details(signal_configuration.universe_uid)
    bars_account = account_details(bars_configuration.account_uid)
    if bars_configuration.asset_source == "universe":
        bars_universe = universe_details(bars_configuration.universe_uid)
        asset_source_name = (
            f"{bars_universe['display_name']} ({bars_universe['symbol']})"
        )
        asset_count: int | None = int(bars_universe.get("asset_count", 0))
    elif bars_configuration.asset_source == "assets":
        asset_count = len(bars_configuration.asset_uids)
        asset_source_name = f"{asset_count} explicitly selected registered assets"
    else:
        asset_count = None
        asset_source_name = (
            f"Latest holdings for {bars_account['account_name']} within the trailing 30 days"
        )

    history = read_portfolio_history(
        configuration_uid=configuration_uid,
        observation_limit=observation_limit,
    )
    observations = [
        {
            "time_index": observation.time_index,
            "close": observation.close,
            "period_return": observation.period_return,
            "calculated_close": observation.calculated_close,
            "close_time": observation.close_time,
            "cumulative_return": observation.cumulative_return,
            "drawdown": observation.drawdown,
        }
        for observation in history.observations
    ]
    performance = history.performance
    if performance is None:
        raise RuntimeError("Portfolio history did not include performance statistics.")
    latest_observation = history.observations[-1] if history.observations else None
    latest_close = (
        latest_observation.close
        if latest_observation is not None and latest_observation.close is not None
        else (
            latest_observation.calculated_close if latest_observation is not None else None
        )
    )
    return PortfolioConfigurationDetailResponse.model_validate(
        {
            **base.model_dump(mode="json"),
            "linked_signal": {
                "name": signal_configuration.name,
                "description": signal_configuration.description,
                "enabled": signal_configuration.enabled,
                "universe_name": signal_universe["display_name"],
                "universe_symbol": signal_universe["symbol"],
                "account_name": signal_account["account_name"],
                "account_environment": "paper" if signal_account["is_paper"] else "live",
            },
            "linked_bars": {
                "name": bars_configuration.name,
                "description": bars_configuration.description,
                "enabled": bars_configuration.enabled,
                "account_name": bars_account["account_name"],
                "account_environment": "paper" if bars_account["is_paper"] else "live",
                "asset_source": bars_configuration.asset_source,
                "asset_source_name": asset_source_name,
                "asset_count": asset_count,
                "frequency_id": bars_configuration.frequency_id,
                "feed": bars_configuration.feed,
                "adjustment": bars_configuration.adjustment,
            },
            "linked_rebalance": {
                "name": rebalance_configuration.name,
                "description": rebalance_configuration.description,
                "strategy": rebalance_configuration.strategy,
            },
            "canonical_portfolio": {
                "materialized": history.materialized,
                "description": history.description,
                "calendar_name": history.calendar_name,
                "calendar_type": history.calendar_type,
                "calendar_timezone": history.calendar_timezone,
                "calendar_valid_from": history.calendar_valid_from,
                "calendar_valid_to": history.calendar_valid_to,
                "backtest_price_column": history.backtest_price_column,
                "observation_count": len(observations),
                "total_observation_count": history.total_observation_count,
                "history_window_truncated": (
                    history.total_observation_count > len(observations)
                ),
                "latest_observation_at": (
                    latest_observation.time_index if latest_observation is not None else None
                ),
                "latest_close": latest_close,
                "latest_period_return": (
                    latest_observation.period_return if latest_observation is not None else None
                ),
                "performance": {
                    "methodology": performance.methodology,
                    "frequency": performance.frequency,
                    "annualization_factor": performance.annualization_factor,
                    "risk_free_rate": performance.risk_free_rate,
                    "observation_count": performance.observation_count,
                    "return_observation_count": performance.return_observation_count,
                    "period_start": performance.period_start,
                    "period_end": performance.period_end,
                    "total_return": performance.total_return,
                    "annualized_return": performance.annualized_return,
                    "annualized_volatility": performance.annualized_volatility,
                    "sharpe_ratio": performance.sharpe_ratio,
                    "sortino_ratio": performance.sortino_ratio,
                    "max_drawdown": performance.max_drawdown,
                    "calmar_ratio": performance.calmar_ratio,
                    "best_period_return": performance.best_period_return,
                    "worst_period_return": performance.worst_period_return,
                    "positive_period_ratio": performance.positive_period_ratio,
                },
                "observations": observations,
            },
        }
    )


def create_configuration(
    request: PortfolioConfigurationCreateRequest,
) -> PortfolioConfigurationResponse:
    payload = request.model_dump()
    job = payload.pop("job")
    row = create_portfolio_job_configuration(**payload, **job)
    return _responses([row])[0]


def update_configuration(
    configuration_uid: str,
    request: PortfolioConfigurationUpdateRequest,
) -> PortfolioConfigurationResponse:
    payload = request.model_dump(exclude_unset=True)
    job_payload = payload.pop("job", None)
    settings = normalize_portfolio_job_settings(**job_payload) if job_payload is not None else None
    row = update_portfolio_job_configuration(
        configuration_uid,
        job_settings=settings,
        **payload,
    )
    return _responses([row])[0]


def delete_configuration(configuration_uid: str) -> dict[str, Any]:
    return delete_portfolio_job_configuration(configuration_uid)


def run_configuration(configuration_uid: str) -> PortfolioJobRunAcceptedResponse:
    submission = launch_portfolio_job(configuration_uid)
    return PortfolioJobRunAcceptedResponse(
        configuration_uid=submission.configuration_uid,
        job_uid=submission.job_uid,
        job_run_uid=submission.job_run_uid,
        status=submission.status,
        status_url=f"/v1/operations/job-runs/{submission.job_run_uid}",
    )


def configuration_runs(configuration_uid: str, *, limit: int) -> list[PortfolioJobRunResponse]:
    return [
        PortfolioJobRunResponse.model_validate(asdict(run))
        for run in list_portfolio_job_runs(configuration_uid, limit=limit)
    ]


def list_rebalances(*, limit: int, offset: int, search: str | None, ordering: str):
    rows, total = list_rebalance_configurations(
        limit=limit,
        offset=offset,
        search=search,
        ordering=ordering,
    )
    return collection_response(
        items=[_rebalance_response(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def _rebalance_response(
    row: PortfolioRebalanceConfiguration,
) -> PortfolioRebalanceConfigurationResponse:
    """Convert the storage-domain model into the API response contract."""

    return PortfolioRebalanceConfigurationResponse.model_validate(row.model_dump(mode="json"))


def create_rebalance(
    request: PortfolioRebalanceConfigurationCreateRequest,
) -> PortfolioRebalanceConfigurationResponse:
    return _rebalance_response(create_rebalance_configuration(**request.model_dump()))


def get_rebalance(configuration_uid: str) -> PortfolioRebalanceConfigurationResponse | None:
    row = get_rebalance_configuration(configuration_uid)
    return _rebalance_response(row) if row else None


def update_rebalance(
    configuration_uid: str,
    request: PortfolioRebalanceConfigurationUpdateRequest,
) -> PortfolioRebalanceConfigurationResponse:
    return _rebalance_response(
        update_rebalance_configuration(
            configuration_uid,
            **request.model_dump(exclude_unset=True),
        )
    )


def delete_rebalance(configuration_uid: str) -> dict[str, Any]:
    return delete_rebalance_configuration(configuration_uid)


__all__ = [
    "configuration_runs",
    "create_configuration",
    "create_rebalance",
    "delete_configuration",
    "delete_rebalance",
    "get_configuration",
    "get_configuration_detail",
    "get_rebalance",
    "list_configurations",
    "list_rebalances",
    "run_configuration",
    "update_configuration",
    "update_rebalance",
]
