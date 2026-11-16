"""Portfolios built from weights an application provides (ADR 0024).

A provided-weights portfolio is a row of the portfolio configuration table with
``weights_source = "provided"``. Its signal is ms-markets' ``ApplicationWeights`` with
``provider`` = this connector's namespace and ``key`` = the configuration UID, bound to this
application's ``AlpacaSignalWeightsStorage`` instance, so its signal UID never changes. Each call
that gives weights publishes one or more observations; ms-markets writes a dropped asset at ``0``
and refuses an observation at or before the last one.

Only the portfolio's owner, the acting owner that created it, gives it weights, changes, pauses
or deletes it. Errors carry a code and never name an asset, a weight or an owner.
"""

from __future__ import annotations

import datetime as dt
import math
import numbers
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from alpaca_connectors.account.ownership import AccountOwner
from alpaca_connectors.portfolios.configurations import AlpacaETFPortfolioConfiguration
from alpaca_connectors.settings import PROJECT_NAMESPACE

PROVIDED_PORTFOLIO_PREFIX = "ALPACA_PORTFOLIO__"
UTC = dt.timezone.utc


class ProvidedWeightsError(Exception):
    """A refusal with a stable code; the message never quotes submitted values."""

    def __init__(self, code: str, message: str, *, status_code: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class PortfolioOwnedByAnotherError(ProvidedWeightsError):
    def __init__(self) -> None:
        # Never names the owner (ADR 0019).
        super().__init__(
            "portfolio_owned_by_another",
            "This portfolio belongs to another owner.",
            status_code=403,
        )


@dataclass(frozen=True, slots=True)
class PublishedWeights:
    signal_uid: str
    observation_count: int
    asset_count: int
    first_observed_at: dt.datetime
    last_observed_at: dt.datetime


@dataclass(frozen=True, slots=True)
class ProvidedPortfolioCreation:
    configuration: AlpacaETFPortfolioConfiguration
    published: PublishedWeights
    portfolio_uid: str
    portfolio_unique_identifier: str
    bars_configuration_created: bool


def provided_portfolio_unique_identifier(configuration_uid: Any) -> str:
    return f"{PROVIDED_PORTFOLIO_PREFIX}{str(configuration_uid).replace('-', '').upper()}"


def provided_weights_signal(configuration_uid: Any) -> Any:
    """The portfolio's ``ApplicationWeights`` signal, bound to this application's instance."""
    from msm_portfolios.contrib.signals import ApplicationWeights

    from alpaca_connectors.instances import AlpacaSignalWeightsStorage

    return ApplicationWeights.from_signal_configuration(
        _signal_configuration(configuration_uid),
        output_table=AlpacaSignalWeightsStorage,
    )


def _signal_configuration(configuration_uid: Any) -> Any:
    from msm_portfolios.contrib.signals import ApplicationWeightsConfig

    return ApplicationWeightsConfig(
        provider=PROJECT_NAMESPACE, key=str(uuid.UUID(str(configuration_uid)))
    )


class _SignalIdentity:
    """The payload ``ApplicationWeights`` hashes into its signal UID, without an updater.

    Building the updater binds its output table to the backend catalog; a UID is a pure hash of
    the class path and the configuration, so listings and the bars resolution compute it here.
    """

    def __init__(self, configuration_uid: Any) -> None:
        self._configuration = _signal_configuration(configuration_uid)

    def build_configuration(self) -> dict[str, Any]:
        from msm_portfolios.contrib.signals import ApplicationWeights
        from msm_portfolios.time_index.signals.weights import _class_import_path

        return {
            "table_updater_class_import_path": _class_import_path(ApplicationWeights),
            "config": self._configuration,
        }


def provided_signal_uid(configuration_uid: Any) -> str:
    """The portfolio's signal UID, the same one its ``ApplicationWeights`` publishes under."""
    from msm_portfolios.time_index.signals.weights import compute_signal_uid

    return str(compute_signal_uid(_SignalIdentity(configuration_uid)))


def is_owner(configuration: AlpacaETFPortfolioConfiguration, owner: AccountOwner) -> bool:
    return (
        configuration.owner_gateway_uid == owner.gateway_uid
        and configuration.owner_user_uid == owner.user_uid
    )


def require_portfolio_owner(
    configuration: AlpacaETFPortfolioConfiguration,
    owner: AccountOwner,
) -> None:
    """Refuse a change to a provided-weights portfolio by anyone but its owner.

    ETF portfolios have no owner and keep their rules (ADR 0024, decision 6).
    """
    if configuration.is_provided and not is_owner(configuration, owner):
        raise PortfolioOwnedByAnotherError()


def _invalid(message: str) -> ProvidedWeightsError:
    return ProvidedWeightsError("invalid_observation", message)


def normalize_observations(
    observations: Sequence[Mapping[str, Any]],
) -> dict[dt.datetime, dict[str, float]]:
    """``[{observed_at, weights}]`` to ``{UTC time: {asset: weight}}``, oldest first."""
    if not observations:
        raise ProvidedWeightsError("empty_observations", "At least one observation is required.")
    normalized: dict[dt.datetime, dict[str, float]] = {}
    for observation in observations:
        observed_at = observation.get("observed_at")
        if isinstance(observed_at, str):
            try:
                observed_at = dt.datetime.fromisoformat(observed_at)
            except ValueError as exc:
                raise _invalid("An observation time is not an ISO 8601 date-time.") from exc
        if not isinstance(observed_at, dt.datetime):
            raise _invalid("Every observation needs observed_at.")
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise _invalid("Observation times must carry a timezone.")
        time = observed_at.astimezone(UTC)
        if time in normalized:
            raise _invalid("Two observations have the same time.")
        weights = observation.get("weights")
        if not isinstance(weights, Mapping):
            raise _invalid("Every observation needs weights, keyed by asset identifier.")
        values: dict[str, float] = {}
        for asset, weight in weights.items():
            if not isinstance(asset, str) or not asset.strip() or asset != asset.strip():
                raise _invalid("An asset identifier is empty or not a string.")
            if isinstance(weight, bool) or not isinstance(weight, numbers.Real):
                raise _invalid("Every weight must be a number.")
            if not math.isfinite(float(weight)):
                raise _invalid("Every weight must be a finite number.")
            values[asset] = float(weight)
        normalized[time] = values
    return dict(sorted(normalized.items()))


def require_alpaca_assets(asset_identifiers: Sequence[str]) -> list[str]:
    """Refuse an identifier Markets does not know, or one with no Alpaca details.

    Two set-based reads: the Markets assets by identifier, and this application's Alpaca details
    by asset uid. No message names an asset.
    """
    from alpaca_connectors.holdings.allocation import read_alpaca_details
    from alpaca_connectors.markets_api import assets_by_identifier

    identifiers = sorted(set(asset_identifiers))
    if not identifiers:
        return []
    assets = assets_by_identifier(identifiers)
    if set(identifiers) - set(assets):
        raise ProvidedWeightsError(
            "unknown_asset", "An asset identifier is not a registered Main Sequence asset."
        )
    uids = {identifier: str(uuid.UUID(str(asset.uid))) for identifier, asset in assets.items()}
    details = read_alpaca_details(list(uids.values()))
    if any(uid not in details for uid in uids.values()):
        raise ProvidedWeightsError(
            "asset_not_alpaca", "An asset is not a registered Alpaca asset, so it has no bars."
        )
    return identifiers


def _last_observed_at(signal_uid: str) -> dt.datetime | None:
    from alpaca_connectors.portfolios.signal_history import read_signal_last_observation_at

    return read_signal_last_observation_at(signal_uid=signal_uid)


def _not_after_last() -> ProvidedWeightsError:
    return ProvidedWeightsError(
        "observation_not_after_last",
        "An observation is at or before the portfolio's last stored observation; "
        "published observations are never restated.",
        status_code=409,
    )


def ensure_provided_signal_metadata(
    configuration: AlpacaETFPortfolioConfiguration,
) -> str:
    """Register the signal in Markets before its first publication (its rows reference it)."""
    from msm_portfolios.api.market_metadata import SignalMetadata

    signal = provided_weights_signal(configuration.uid)
    SignalMetadata.upsert(
        signal_uid=signal.signal_uid,
        signal_description=(
            f"Weights provided for the portfolio {configuration.name!r} by its owner application."
        ),
    )
    return str(signal.signal_uid)


def _observation_assets(normalized: Mapping[dt.datetime, Mapping[str, float]]) -> list[str]:
    return sorted({asset for weights in normalized.values() for asset in weights})


def publish_provided_weights(
    configuration: AlpacaETFPortfolioConfiguration,
    observations: Sequence[Mapping[str, Any]],
) -> PublishedWeights:
    """Validate and publish one or more observations of the portfolio's signal, in one write."""
    if not configuration.is_provided:
        raise ProvidedWeightsError(
            "not_a_provided_weights_portfolio",
            "Only a provided-weights portfolio takes weights.",
            status_code=409,
        )
    normalized = normalize_observations(observations)
    assets = _observation_assets(normalized)
    require_alpaca_assets(assets)
    return _publish_validated(configuration, normalized, assets)


def _publish_validated(
    configuration: AlpacaETFPortfolioConfiguration,
    normalized: Mapping[dt.datetime, Mapping[str, float]],
    assets: Sequence[str],
) -> PublishedWeights:
    """Publish observations whose shape and assets were already validated.

    Refuse an observation at or before the last stored one, record the assets (ADR 0024
    decision 5), then publish. ms-markets repeats its own checks. Which of its refusals is a time
    conflict is decided from the stored last time, not from the wording of its message.
    """
    signal = provided_weights_signal(configuration.uid)
    signal_uid = str(signal.signal_uid)
    first_time = next(iter(normalized))
    last_stored = _last_observed_at(signal_uid)
    if last_stored is not None and first_time <= last_stored:
        raise _not_after_last()

    from alpaca_connectors.portfolios.signal_assets import record_signal_assets

    record_signal_assets(signal_uid, assets)
    try:
        signal.publish(dict(normalized), description=configuration.description)
    except LookupError as exc:
        raise ProvidedWeightsError(
            "unknown_asset", "An asset identifier is not a registered Main Sequence asset."
        ) from exc
    except (TypeError, ValueError) as exc:
        # Another call may have published since the check above.
        last_stored = _last_observed_at(signal_uid)
        if last_stored is not None and first_time <= last_stored:
            raise _not_after_last() from exc
        raise _invalid(
            "The observations were refused: an empty set with nothing to close, or a value the "
            "signal cannot store."
        ) from exc

    from alpaca_connectors.portfolios.datasets import register_signal_dataset

    register_signal_dataset(signal_uid)
    times = list(normalized)
    return PublishedWeights(
        signal_uid=signal_uid,
        observation_count=len(times),
        asset_count=len(assets),
        first_observed_at=times[0],
        last_observed_at=times[-1],
    )


def give_portfolio_weights(
    configuration_uid: Any,
    observations: Sequence[Mapping[str, Any]],
    *,
    owner: AccountOwner,
) -> PublishedWeights:
    """Give an existing provided-weights portfolio one or more new observations."""
    from alpaca_connectors.portfolios.configurations import get_portfolio_configuration

    configuration = get_portfolio_configuration(configuration_uid)
    if configuration is None:
        raise LookupError(f"Portfolio configuration {configuration_uid!s} does not exist.")
    require_portfolio_owner(configuration, owner)
    return publish_provided_weights(configuration, observations)


def _upsert_portfolio_row(
    configuration: AlpacaETFPortfolioConfiguration,
    *,
    first_observed_at: dt.datetime,
) -> Any:
    from etfhextractor.calendars import ensure_trading_calendar
    from msm.api.portfolios import Portfolio

    from alpaca_connectors.portfolios.configurations import get_rebalance_configuration

    rebalance = get_rebalance_configuration(configuration.rebalance_configuration_uid)
    if rebalance is None:
        raise LookupError(
            f"Rebalance configuration {configuration.rebalance_configuration_uid!s} does not exist."
        )
    backtest_days = max((dt.date.today() - first_observed_at.date()).days, 0)
    calendar_row = ensure_trading_calendar(
        rebalance.calendar_identifier,
        backtest_start_days=backtest_days,
    )
    return Portfolio.upsert(
        unique_identifier=provided_portfolio_unique_identifier(configuration.uid),
        calendar_uid=calendar_row.uid,
    )


def create_provided_weights_portfolio(
    *,
    owner: AccountOwner,
    name: str,
    rebalance_configuration_uid: Any,
    observations: Sequence[Mapping[str, Any]],
    bars_configuration_uid: Any | None = None,
    bars_configuration: Mapping[str, Any] | None = None,
    description: str | None = None,
    upsample_frequency_id: str = "1d",
    intraday_bar_interpolation_rule: str = "ffill",
    valuation_column: str = "close",
    valuation_maximum_staleness_seconds: int = 86_400,
    fail_on_missing_prices: bool = True,
    commission_fee: float = 0.00018,
    scheduled: bool = True,
) -> ProvidedPortfolioCreation:
    """Create a provided-weights portfolio from its first observation or a history.

    Order: validate the observations and assets, create the inline `portfolios` bars
    configuration when one is given, store the row, register and publish the signal, then create
    the `Portfolio` row. A failure after a row was stored deletes the rows this call stored.
    Ownership of an inline bars configuration's account is the caller's check (ADR 0022).
    """
    from alpaca_connectors.market_data.configurations import (
        create_bar_configuration,
        delete_bar_configuration,
    )
    from alpaca_connectors.portfolios.configurations import (
        create_portfolio_configuration_row,
        delete_portfolio_configuration_row,
        update_portfolio_configuration_row,
    )

    if (bars_configuration_uid is None) == (bars_configuration is None):
        raise ProvidedWeightsError(
            "invalid_bars_configuration",
            "Give either bars_configuration_uid or bars_configuration, not both.",
        )
    normalized = normalize_observations(observations)
    assets = _observation_assets(normalized)
    require_alpaca_assets(assets)

    created_bars_uid: uuid.UUID | None = None
    configuration: AlpacaETFPortfolioConfiguration | None = None
    try:
        if bars_configuration is not None:
            created = create_bar_configuration(
                **{**dict(bars_configuration), "asset_source": "portfolios"}
            )
            created_bars_uid = created.uid
            bars_configuration_uid = created.uid
        configuration = create_portfolio_configuration_row(
            name=name,
            description=description,
            weights_source="provided",
            owner_gateway_uid=owner.gateway_uid,
            owner_user_uid=owner.user_uid,
            bars_configuration_uid=bars_configuration_uid,
            rebalance_configuration_uid=rebalance_configuration_uid,
            upsample_frequency_id=upsample_frequency_id,
            intraday_bar_interpolation_rule=intraday_bar_interpolation_rule,
            valuation_column=valuation_column,
            valuation_maximum_staleness_seconds=valuation_maximum_staleness_seconds,
            fail_on_missing_prices=fail_on_missing_prices,
            commission_fee=commission_fee,
            scheduled=scheduled,
        )
        ensure_provided_signal_metadata(configuration)
        published = _publish_validated(configuration, normalized, assets)
        portfolio_row = _upsert_portfolio_row(
            configuration, first_observed_at=published.first_observed_at
        )
        configuration = update_portfolio_configuration_row(
            configuration.uid,
            values={"portfolio_uid": uuid.UUID(str(portfolio_row.uid))},
        )
    except Exception:
        if configuration is not None:
            delete_portfolio_configuration_row(configuration.uid)
        if created_bars_uid is not None:
            delete_bar_configuration(created_bars_uid)
        raise
    return ProvidedPortfolioCreation(
        configuration=configuration,
        published=published,
        portfolio_uid=str(portfolio_row.uid),
        portfolio_unique_identifier=str(portfolio_row.unique_identifier),
        bars_configuration_created=created_bars_uid is not None,
    )


__all__ = [
    "PROVIDED_PORTFOLIO_PREFIX",
    "PortfolioOwnedByAnotherError",
    "ProvidedPortfolioCreation",
    "ProvidedWeightsError",
    "PublishedWeights",
    "create_provided_weights_portfolio",
    "ensure_provided_signal_metadata",
    "give_portfolio_weights",
    "is_owner",
    "normalize_observations",
    "provided_portfolio_unique_identifier",
    "provided_signal_uid",
    "provided_weights_signal",
    "publish_provided_weights",
    "require_alpaca_assets",
    "require_portfolio_owner",
]
