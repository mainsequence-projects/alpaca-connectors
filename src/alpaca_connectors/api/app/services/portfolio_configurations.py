"""API shaping for durable analytical portfolio configurations.

Every configuration runs on the portfolio Job declared in the repository workflow (ADR 0013). A
provided-weights portfolio (ADR 0024) is changed, paused, given weights or deleted only by its
owner; ETF portfolios keep their rules.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from typing import Any, Mapping

from alpaca_connectors.account.ownership import AccountOwner
from alpaca_connectors.operations import (
    configuration_job_state,
    launch_portfolio_job,
    list_portfolio_job_runs,
    signal_job_configurations_by_uids,
    signal_uid_for_configuration,
)
from alpaca_connectors.portfolios import (
    PortfolioRebalanceConfiguration,
    create_portfolio_configuration_row,
    create_rebalance_configuration,
    delete_portfolio_configuration_row,
    delete_rebalance_configuration,
    get_portfolio_configuration,
    get_rebalance_configuration,
    list_portfolio_configurations,
    list_rebalance_configurations,
    read_portfolio_history,
    rebalance_configurations_by_uids,
    update_portfolio_configuration,
    update_rebalance_configuration,
)
from alpaca_connectors.portfolios.provided_weights import (
    create_provided_weights_portfolio,
    give_portfolio_weights,
    provided_signal_uid,
    require_portfolio_owner,
)

from ..schemas import (
    PortfolioConfigurationCreateRequest,
    PortfolioConfigurationDetailResponse,
    PortfolioConfigurationResponse,
    PortfolioConfigurationUpdateRequest,
    PortfolioJobRunAcceptedResponse,
    PortfolioJobRunResponse,
    PortfolioRebalanceConfigurationCreateRequest,
    PortfolioRebalanceConfigurationResponse,
    PortfolioRebalanceConfigurationUpdateRequest,
    PortfolioWeightsRequest,
    ProvidedWeightsPortfolioCreateRequest,
    ProvidedWeightsPortfolioCreateResponse,
    PublishedWeightsResponse,
    SignalObservationsResponse,
)
from .common import collection_response
from .configuration_jobs import accepted_run, job_fields
from .signal_jobs import observations_response


def _responses(rows: list[Any]) -> list[PortfolioConfigurationResponse]:
    if not rows:
        return []
    state = configuration_job_state("portfolio", [row.uid for row in rows])
    signal_uids = list(
        dict.fromkeys(
            str(row.signal_configuration_uid)
            for row in rows
            if row.signal_configuration_uid is not None
        )
    )
    rebalance_uids = list(dict.fromkeys(str(row.rebalance_configuration_uid) for row in rows))
    signal_configurations = signal_job_configurations_by_uids(signal_uids)
    rebalance_configurations = rebalance_configurations_by_uids(rebalance_uids)
    responses: list[PortfolioConfigurationResponse] = []
    for row in rows:
        rebalance_configuration = rebalance_configurations.get(str(row.rebalance_configuration_uid))
        if row.is_provided:
            signal_uid = provided_signal_uid(row.uid)
        else:
            signal_configuration = signal_configurations.get(str(row.signal_configuration_uid))
            signal_uid = (
                signal_uid_for_configuration(signal_configuration)
                if signal_configuration is not None
                else None
            )
        if signal_uid is None or rebalance_configuration is None:
            raise RuntimeError(
                f"Portfolio configuration {row.uid!s} has an unresolved required relationship."
            )
        # The owner pair stays internal: it can name a gateway's person (ADR 0019).
        values = row.model_dump(mode="json", exclude={"owner_gateway_uid", "owner_user_uid"})
        responses.append(
            PortfolioConfigurationResponse.model_validate(
                {
                    **values,
                    "signal_uid": signal_uid,
                    "rebalance_strategy": rebalance_configuration.strategy,
                    **job_fields(
                        state.job,
                        state.latest_runs.get(str(row.uid)),
                        dispatcher=state.dispatcher,
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
    weights_source: str | None = None,
):
    rows, total = list_portfolio_configurations(
        limit=limit,
        offset=offset,
        search=search,
        signal_configuration_uid=signal_configuration_uid,
        bars_configuration_uid=bars_configuration_uid,
        weights_source=weights_source,
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
    from alpaca_connectors.account.services import get_account_registration
    from alpaca_connectors.market_data import get_bar_configuration
    from alpaca_connectors.universes import get_asset_universe_view

    row = get_portfolio_configuration(configuration_uid)
    if row is None:
        return None
    base = _responses([row])[0]
    signal_configuration = (
        None
        if row.is_provided
        else signal_job_configurations_by_uids([row.signal_configuration_uid]).get(
            str(row.signal_configuration_uid)
        )
    )
    rebalance_configuration = get_rebalance_configuration(row.rebalance_configuration_uid)
    bars_configuration = get_bar_configuration(row.bars_configuration_uid)
    if (
        (signal_configuration is None and not row.is_provided)
        or rebalance_configuration is None
        or bars_configuration is None
    ):
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

    linked_signal: dict[str, Any] | None = None
    provided_weights: dict[str, Any] | None = None
    if signal_configuration is not None:
        signal_account = account_details(signal_configuration.account_uid)
        signal_universe = universe_details(signal_configuration.universe_uid)
        linked_signal = {
            "name": signal_configuration.name,
            "description": signal_configuration.description,
            "scheduled": signal_configuration.scheduled,
            "universe_name": signal_universe["display_name"],
            "universe_symbol": signal_universe["symbol"],
            "account_name": signal_account["account_name"],
            "account_environment": "paper" if signal_account["is_paper"] else "live",
        }
    else:
        from alpaca_connectors.portfolios.signal_assets import assets_for_signals
        from alpaca_connectors.portfolios.signal_history import read_signal_observation_bounds

        bounds = read_signal_observation_bounds(signal_uid=base.signal_uid)
        provided_weights = {
            "signal_uid": base.signal_uid,
            "first_observation_at": bounds.first_observation_at,
            "last_observation_at": bounds.last_observation_at,
            "asset_count": len(assets_for_signals([base.signal_uid])),
        }
    bars_account = account_details(bars_configuration.account_uid)
    if bars_configuration.asset_source == "universe":
        bars_universe = universe_details(bars_configuration.universe_uid)
        asset_source_name = f"{bars_universe['display_name']} ({bars_universe['symbol']})"
        asset_count: int | None = int(bars_universe.get("asset_count", 0))
    elif bars_configuration.asset_source == "assets":
        asset_count = len(bars_configuration.asset_uids)
        asset_source_name = f"{asset_count} explicitly selected registered assets"
    elif bars_configuration.asset_source == "portfolios":
        asset_count = None
        asset_source_name = "Every asset of the live portfolios that use these prices"
    else:
        asset_count = None
        asset_source_name = (
            f"Latest holdings for {bars_account['account_name']} within the trailing 30 days"
        )

    history = read_portfolio_history(
        configuration_uid=configuration_uid,
        observation_limit=observation_limit,
        weights_source=row.weights_source,
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
        else (latest_observation.calculated_close if latest_observation is not None else None)
    )
    return PortfolioConfigurationDetailResponse.model_validate(
        {
            **base.model_dump(mode="json"),
            "linked_signal": linked_signal,
            "provided_weights": provided_weights,
            "linked_bars": {
                "name": bars_configuration.name,
                "description": bars_configuration.description,
                "enabled": bars_configuration.enabled,
                "scheduled": bars_configuration.scheduled,
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
                "calendar_identifier": rebalance_configuration.calendar_identifier,
                "session_label": rebalance_configuration.session_label,
                "rebalance_event": rebalance_configuration.rebalance_event,
                "event_offset_seconds": rebalance_configuration.event_offset_seconds,
                "rebalance_cadence": rebalance_configuration.rebalance_cadence,
                "rebalance_weekday": rebalance_configuration.rebalance_weekday,
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
                "history_window_truncated": (history.total_observation_count > len(observations)),
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
    """Store the configuration only; the shared portfolio Job runs it."""
    row = create_portfolio_configuration_row(**request.model_dump())
    return _responses([row])[0]


def _require_owner(row: Any, owner: Callable[[], AccountOwner]) -> None:
    """Only a provided-weights portfolio has an owner; the acting owner is read only for it."""
    if row.is_provided:
        require_portfolio_owner(row, owner())


def update_configuration(
    configuration_uid: str,
    request: PortfolioConfigurationUpdateRequest,
    *,
    owner: Callable[[], AccountOwner],
) -> PortfolioConfigurationResponse:
    current = get_portfolio_configuration(configuration_uid)
    if current is None:
        raise LookupError(f"Portfolio configuration {configuration_uid!s} does not exist.")
    _require_owner(current, owner)
    row = update_portfolio_configuration(
        configuration_uid,
        **request.model_dump(exclude_unset=True),
    )
    return _responses([row])[0]


def delete_configuration(
    configuration_uid: str,
    *,
    owner: Callable[[], AccountOwner],
) -> dict[str, Any]:
    """Delete the configuration row only; its JobRuns and published data remain."""
    row = get_portfolio_configuration(configuration_uid)
    if row is None:
        raise LookupError(f"Portfolio configuration {configuration_uid!s} does not exist.")
    _require_owner(row, owner)
    return {
        "configuration_uid": str(row.uid),
        "deleted": True,
        "storage_result": delete_portfolio_configuration_row(row.uid),
    }


def create_provided_configuration(
    request: ProvidedWeightsPortfolioCreateRequest,
    *,
    owner: AccountOwner,
) -> ProvidedWeightsPortfolioCreateResponse:
    """Create a provided-weights portfolio owned by the acting owner (ADR 0024).

    An inline bars configuration runs with its account's keys, so its account must be one the
    owner may configure (ADR 0022), as for ``POST /v1/bar-configurations``.
    """
    from .accounts import require_owner_may_configure_account

    values = request.model_dump(exclude={"observations", "bars_configuration"})
    if request.bars_configuration is not None:
        require_owner_may_configure_account(request.bars_configuration.account_uid, owner)
        values["bars_configuration"] = request.bars_configuration.model_dump()
    created = create_provided_weights_portfolio(
        owner=owner,
        observations=[observation.model_dump() for observation in request.observations],
        **values,
    )
    return ProvidedWeightsPortfolioCreateResponse(
        configuration=_responses([created.configuration])[0],
        portfolio_uid=created.portfolio_uid,
        portfolio_unique_identifier=created.portfolio_unique_identifier,
        bars_configuration_created=created.bars_configuration_created,
        weights=_published_response(created.configuration.uid, created.published),
    )


def _published_response(configuration_uid: Any, published: Any) -> PublishedWeightsResponse:
    return PublishedWeightsResponse(
        configuration_uid=str(configuration_uid),
        signal_uid=published.signal_uid,
        observation_count=published.observation_count,
        asset_count=published.asset_count,
        first_observed_at=published.first_observed_at,
        last_observed_at=published.last_observed_at,
    )


def give_weights(
    configuration_uid: str,
    request: PortfolioWeightsRequest,
    *,
    owner: AccountOwner,
) -> PublishedWeightsResponse:
    """Give a provided-weights portfolio one more observation, or several."""
    published = give_portfolio_weights(
        configuration_uid,
        [observation.model_dump() for observation in request.observations],
        owner=owner,
    )
    return _published_response(configuration_uid, published)


def configuration_weights(configuration_uid: str, *, limit: int) -> SignalObservationsResponse:
    """The stored observations of a provided-weights portfolio, newest first."""
    from alpaca_connectors.portfolios import read_signal_observation_matrix

    row = get_portfolio_configuration(configuration_uid)
    if row is None:
        raise LookupError(f"Portfolio configuration {configuration_uid!s} does not exist.")
    if not row.is_provided:
        raise ValueError("Only a provided-weights portfolio has provided weights.")
    matrix = read_signal_observation_matrix(
        signal_uid=provided_signal_uid(row.uid),
        observation_limit=limit,
    )
    return observations_response(row.uid, matrix)


def run_configuration(configuration_uid: str) -> PortfolioJobRunAcceptedResponse:
    return PortfolioJobRunAcceptedResponse.model_validate(
        accepted_run(launch_portfolio_job(configuration_uid))
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
    "configuration_weights",
    "create_configuration",
    "create_provided_configuration",
    "create_rebalance",
    "delete_configuration",
    "delete_rebalance",
    "get_configuration",
    "get_configuration_detail",
    "get_rebalance",
    "give_weights",
    "list_configurations",
    "list_rebalances",
    "run_configuration",
    "update_configuration",
    "update_rebalance",
]
