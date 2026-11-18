"""API shaping for durable analytical portfolio configurations.

Every configuration runs on the portfolio Job declared in the repository workflow (ADR 0013). A
portfolio belongs to one account and follows one of its account's signal definitions (ADR 0028);
it is built from the weights an application provides (ADR 0024, ADR 0027) and is created,
changed, paused, given weights or deleted only by its owner, the account's owner.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from alpaca_connectors.account.ownership import AccountOwner
from alpaca_connectors.operations import (
    configuration_job_state,
    launch_portfolio_job,
    list_portfolio_job_runs,
)
from alpaca_connectors.portfolios import (
    PortfolioRebalanceConfiguration,
    create_rebalance_configuration,
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
    create_portfolio,
    create_provided_weights_portfolio,
    end_portfolio_configuration,
    give_portfolio_weights,
    portfolio_signal_uid,
    require_portfolio_owner,
)

from ..schemas import (
    PortfolioConfigurationDetailResponse,
    PortfolioConfigurationResponse,
    PortfolioConfigurationUpdateRequest,
    PortfolioCreateRequest,
    PortfolioCreateResponse,
    PortfolioJobRunAcceptedResponse,
    PortfolioJobRunResponse,
    PortfolioRebalanceConfigurationCreateRequest,
    PortfolioRebalanceConfigurationResponse,
    PortfolioRebalanceConfigurationUpdateRequest,
    PortfolioWeightsRequest,
    ProvidedWeightsPortfolioCreateRequest,
    ProvidedWeightsPortfolioCreateResponse,
    PublishedWeightsResponse,
    SignalObservationAssetResponse,
    SignalObservationsResponse,
)
from .common import collection_response
from .configuration_jobs import accepted_run, job_fields


def _responses(rows: list[Any]) -> list[PortfolioConfigurationResponse]:
    from alpaca_connectors.market_data.configurations import portfolio_bar_configurations
    from alpaca_connectors.portfolios.signal_definitions import signal_definitions_by_uids

    if not rows:
        return []
    state = configuration_job_state("portfolio", [row.uid for row in rows])
    rebalance_uids = list(dict.fromkeys(str(row.rebalance_configuration_uid) for row in rows))
    rebalance_configurations = rebalance_configurations_by_uids(rebalance_uids)
    definitions = signal_definitions_by_uids(row.signal_definition_uid for row in rows)
    own_bars = portfolio_bar_configurations([row.uid for row in rows])
    responses: list[PortfolioConfigurationResponse] = []
    for row in rows:
        rebalance_configuration = rebalance_configurations.get(str(row.rebalance_configuration_uid))
        if rebalance_configuration is None:
            raise RuntimeError(
                f"Portfolio configuration {row.uid!s} has an unresolved required relationship."
            )
        # The owner pair stays internal: it can name a gateway's person (ADR 0019).
        values = row.model_dump(mode="json", exclude={"owner_gateway_uid", "owner_user_uid"})
        bars = own_bars.get(str(row.uid))
        responses.append(
            PortfolioConfigurationResponse.model_validate(
                {
                    **values,
                    "adopted": row.adopted,
                    "bars_configuration_uid": (
                        str(bars.uid) if bars is not None else values["bars_configuration_uid"]
                    ),
                    "signal_uid": portfolio_signal_uid(row, definitions=definitions),
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
    ordering: str,
    account_uid: str | None = None,
    signal_definition_uid: str | None = None,
):
    rows, total = list_portfolio_configurations(
        limit=limit,
        offset=offset,
        search=search,
        account_uids=[account_uid] if account_uid is not None else None,
        signal_definition_uid=signal_definition_uid,
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
    from alpaca_connectors.market_data.configurations import (
        get_bar_configuration,
        portfolio_bar_configuration,
    )
    from alpaca_connectors.portfolios.provided_weights import ended_portfolio_account_uid
    from alpaca_connectors.portfolios.signal_assets import assets_for_signals
    from alpaca_connectors.portfolios.signal_history import read_signal_observation_bounds

    row = get_portfolio_configuration(configuration_uid)
    if row is None:
        return None
    base = _responses([row])[0]
    rebalance_configuration = get_rebalance_configuration(row.rebalance_configuration_uid)
    bars_configuration = (
        portfolio_bar_configuration(row.uid)
        if row.adopted
        else get_bar_configuration(row.bars_configuration_uid)
    )
    if rebalance_configuration is None or bars_configuration is None:
        raise RuntimeError(
            f"Portfolio configuration {configuration_uid!s} has an unresolved required relationship."
        )

    bounds = read_signal_observation_bounds(signal_uid=base.signal_uid)
    provided_weights = {
        "signal_uid": base.signal_uid,
        "first_observation_at": bounds.first_observation_at,
        "last_observation_at": bounds.last_observation_at,
        "asset_count": len(assets_for_signals([base.signal_uid])),
    }
    # The portfolio's account runs its bars (ADR 0028); before adoption, its former feed's did.
    account_uid = ended_portfolio_account_uid(row)
    bars_account = get_account_registration(str(account_uid)) if account_uid else None
    if bars_account is None:
        raise RuntimeError(f"Portfolio detail references missing Alpaca account {account_uid}.")
    asset_count: int | None
    if bars_configuration.asset_source == "assets":
        asset_count = len(bars_configuration.asset_uids)
        asset_source_name = f"{asset_count} explicitly selected registered assets"
    elif bars_configuration.asset_source == "portfolios":
        asset_count = None
        asset_source_name = "Every asset the portfolio's signal has published"
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
        else (latest_observation.calculated_close if latest_observation is not None else None)
    )
    return PortfolioConfigurationDetailResponse.model_validate(
        {
            **base.model_dump(mode="json"),
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


def _require_owner(row: Any, owner: Callable[[], AccountOwner]) -> None:
    """Refuse anyone but the portfolio's owner (ADR 0024, decision 6)."""
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
    """End a portfolio: retire its ms-markets `Portfolio`, then delete its configuration.

    Only its owner may end it. The answer says what became of its `Portfolio` under
    ``portfolio`` (issue #33, ADR 0027); its JobRuns and published data remain.
    """
    row = get_portfolio_configuration(configuration_uid)
    if row is None:
        raise LookupError(f"Portfolio configuration {configuration_uid!s} does not exist.")
    _require_owner(row, owner)
    return end_portfolio_configuration(row).summary()


def create_configuration(
    request: PortfolioCreateRequest,
    *,
    owner: AccountOwner,
) -> PortfolioCreateResponse:
    """Create a portfolio of an account the acting owner owns (ADR 0028, decision 3)."""
    from .accounts import require_account_owned_by

    require_account_owned_by(request.account_uid, owner)
    created = create_portfolio(
        owner=owner,
        **request.model_dump(exclude={"bars"}),
        bars=request.bars.model_dump(),
    )
    return PortfolioCreateResponse(
        configuration=_responses([created.configuration])[0],
        portfolio_uid=created.portfolio_uid,
        portfolio_unique_identifier=created.portfolio_unique_identifier,
        bars_configuration_uid=created.bars_configuration_uid,
    )


def create_provided_configuration(
    request: ProvidedWeightsPortfolioCreateRequest,
    *,
    owner: AccountOwner,
) -> ProvidedWeightsPortfolioCreateResponse:
    """Create a portfolio by ADR 0024's route, kept until the applications move (ADR 0028).

    The portfolio belongs to the account of the bars configuration given inline or named, so
    that account must be the acting owner's.
    """
    from alpaca_connectors.market_data.configurations import get_bar_configuration

    from .accounts import require_account_owned_by

    values = request.model_dump(exclude={"observations", "bars_configuration"})
    if request.bars_configuration is not None:
        require_account_owned_by(request.bars_configuration.account_uid, owner)
        values["bars_configuration"] = request.bars_configuration.model_dump()
    else:
        named = get_bar_configuration(request.bars_configuration_uid)
        if named is None:
            raise LookupError(
                f"Bars configuration {request.bars_configuration_uid} does not exist."
            )
        if named.account_uid is not None:
            require_account_owned_by(named.account_uid, owner)
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


def observations_response(configuration_uid: Any, matrix: Any) -> SignalObservationsResponse:
    """A portfolio's signal observation matrix as the API answers it."""
    assets = [
        SignalObservationAssetResponse(
            asset_identifier=asset.asset_identifier,
            symbol=asset.symbol,
            name=asset.name,
            weights=list(asset.weights),
        )
        for asset in matrix.assets
    ]
    return SignalObservationsResponse(
        configuration_uid=str(configuration_uid),
        signal_uid=matrix.signal_uid,
        observation_count=len(matrix.time_indexes),
        asset_count=len(assets),
        time_indexes=list(matrix.time_indexes),
        assets=assets,
    )


def configuration_weights(configuration_uid: str, *, limit: int) -> SignalObservationsResponse:
    """The stored observations of a portfolio's provided weights, newest first."""
    from alpaca_connectors.portfolios import read_signal_observation_matrix

    row = get_portfolio_configuration(configuration_uid)
    if row is None:
        raise LookupError(f"Portfolio configuration {configuration_uid!s} does not exist.")
    matrix = read_signal_observation_matrix(
        signal_uid=portfolio_signal_uid(row),
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
    "observations_response",
    "run_configuration",
    "update_configuration",
    "update_rebalance",
]
