"""Portfolios built from weights an application provides (ADR 0024), owned by accounts (ADR 0028).

Every portfolio of this connector is one (ADR 0027): a row of the portfolio configuration table
that belongs to one account and follows one signal definition of that account (ADR 0028). The
signal is ms-markets' ``ApplicationWeights`` with ``provider`` = this connector's namespace and the
definition's ``signal_key``, bound to this application's ``AlpacaSignalWeightsStorage`` instance.
Each call that gives weights publishes one or more observations to that signal; ms-markets
writes a dropped asset at ``0`` and refuses an observation at or before the last one.

Only a portfolio's owner gives it weights, changes, pauses or deletes it. Errors carry a code and
never name an asset, a weight or an owner.

The routes of ADR 0024 are supported, with the rules they had before ADR 0028; Hunch uses them.
``create_provided_weights_portfolio`` creates the portfolio with a definition of its own whose key
is the configuration UID, as its signal's key was, and ``give_portfolio_weights`` publishes to the
signal the portfolio follows. A portfolio not yet adopted keeps working as before ADR 0028: it
publishes to its own signal, keyed by its configuration UID, and runs on the bars feed it names.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
import numbers
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from alpaca_connectors.account.ownership import AccountOwner
from alpaca_connectors.portfolios.configurations import AlpacaPortfolioConfiguration
from alpaca_connectors.portfolios.signal_definitions import (
    SignalDefinition,
    legacy_signal_key,
    signal_uid_for_key,
    weights_signal,
)

PROVIDED_PORTFOLIO_PREFIX = "ALPACA_PORTFOLIO__"
OWN_BARS_SOURCE = "portfolios"
OWN_BARS_SUFFIX = " prices"
UTC = dt.timezone.utc

logger = logging.getLogger(__name__)


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
    configuration: AlpacaPortfolioConfiguration
    published: PublishedWeights | None
    portfolio_uid: str
    portfolio_unique_identifier: str
    bars_configuration_created: bool
    bars_configuration_uid: str
    signal_definition: SignalDefinition


def provided_portfolio_unique_identifier(configuration_uid: Any) -> str:
    return f"{PROVIDED_PORTFOLIO_PREFIX}{str(configuration_uid).replace('-', '').upper()}"


def provided_signal_uid(configuration_uid: Any) -> str:
    """The UID of the signal a portfolio created before ADR 0028 published to (its own UID)."""
    return signal_uid_for_key(legacy_signal_key(configuration_uid))


def portfolio_signal_key(
    configuration: AlpacaPortfolioConfiguration,
    *,
    definitions: Mapping[str, SignalDefinition] | None = None,
) -> str:
    """The key of the signal the portfolio follows: its definition's, or its own UID before it."""
    from alpaca_connectors.portfolios.signal_definitions import get_signal_definition

    if configuration.signal_definition_uid is None:
        return legacy_signal_key(configuration.uid)
    definition = (definitions or {}).get(str(configuration.signal_definition_uid))
    if definition is None:
        definition = get_signal_definition(configuration.signal_definition_uid)
    if definition is None:
        raise LookupError(
            f"Signal definition {configuration.signal_definition_uid!s} does not exist."
        )
    return definition.signal_key


def portfolio_signal_uid(
    configuration: AlpacaPortfolioConfiguration,
    *,
    definitions: Mapping[str, SignalDefinition] | None = None,
) -> str:
    """The UID of the signal the portfolio follows."""
    return signal_uid_for_key(portfolio_signal_key(configuration, definitions=definitions))


def is_owner(configuration: AlpacaPortfolioConfiguration, owner: AccountOwner) -> bool:
    return (
        configuration.owner_gateway_uid == owner.gateway_uid
        and configuration.owner_user_uid == owner.user_uid
    )


def require_portfolio_owner(
    configuration: AlpacaPortfolioConfiguration,
    owner: AccountOwner,
) -> None:
    """Refuse a change to a portfolio by anyone but its owner (ADR 0024, decision 6)."""
    if not is_owner(configuration, owner):
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
        "An observation is at or before the signal's last stored observation; "
        "published observations are never restated.",
        status_code=409,
    )


def ensure_signal_metadata(signal_key: str, *, description: str) -> str:
    """Register the signal in Markets before its first publication (its rows reference it)."""
    from msm_portfolios.api.market_metadata import SignalMetadata

    signal = weights_signal(signal_key)
    SignalMetadata.upsert(signal_uid=signal.signal_uid, signal_description=description)
    return str(signal.signal_uid)


def _signal_description(definition: SignalDefinition) -> str:
    return f"Weights the application provides for the signal {definition.name!r}."


def _observation_assets(normalized: Mapping[dt.datetime, Mapping[str, float]]) -> list[str]:
    return sorted({asset for weights in normalized.values() for asset in weights})


def publish_definition_weights(
    definition: SignalDefinition,
    observations: Sequence[Mapping[str, Any]],
) -> PublishedWeights:
    """Validate and publish one or more observations of the definition's signal, in one write."""
    normalized = normalize_observations(observations)
    assets = _observation_assets(normalized)
    require_alpaca_assets(assets)
    return _publish_validated(
        definition.signal_key,
        normalized,
        assets,
        description=definition.description,
        metadata_description=_signal_description(definition),
    )


def publish_provided_weights(
    configuration: AlpacaPortfolioConfiguration,
    observations: Sequence[Mapping[str, Any]],
) -> PublishedWeights:
    """Publish to the signal the portfolio follows (the routes of ADR 0024).

    A portfolio adopted by an account follows its signal definition. One not yet adopted publishes
    to its own signal, keyed by its configuration UID, as it did before ADR 0028; adopting it
    later keeps that signal.
    """
    from alpaca_connectors.portfolios.signal_definitions import get_signal_definition

    if configuration.signal_definition_uid is None:
        normalized = normalize_observations(observations)
        assets = _observation_assets(normalized)
        require_alpaca_assets(assets)
        return _publish_validated(
            legacy_signal_key(configuration.uid),
            normalized,
            assets,
            description=configuration.description,
            metadata_description=(
                f"Weights the application provides for the portfolio {configuration.name!r}."
            ),
        )
    definition = get_signal_definition(configuration.signal_definition_uid)
    if definition is None:
        raise LookupError(
            f"Signal definition {configuration.signal_definition_uid!s} does not exist."
        )
    return publish_definition_weights(definition, observations)


def _publish_validated(
    signal_key: str,
    normalized: Mapping[dt.datetime, Mapping[str, float]],
    assets: Sequence[str],
    *,
    description: str | None,
    metadata_description: str,
) -> PublishedWeights:
    """Publish observations whose shape and assets were already validated.

    Refuse an observation at or before the last stored one, register the signal in Markets on its
    first publication (its rows reference it), record the assets (ADR 0024 decision 5), then
    publish. ms-markets repeats its own checks. Which of its refusals is a time conflict is
    decided from the stored last time, not from the wording of its message.
    """
    signal = weights_signal(signal_key)
    signal_uid = str(signal.signal_uid)
    first_time = next(iter(normalized))
    last_stored = _last_observed_at(signal_uid)
    if last_stored is not None and first_time <= last_stored:
        raise _not_after_last()
    if last_stored is None:
        ensure_signal_metadata(signal_key, description=metadata_description)

    from alpaca_connectors.portfolios.signal_assets import record_signal_assets

    record_signal_assets(signal_uid, assets)
    try:
        signal.publish(dict(normalized), description=description)
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


@dataclass(frozen=True, slots=True)
class PortfolioRemoval:
    configuration_uid: str
    portfolio_uid: str | None
    portfolio_unique_identifier: str
    portfolio_state: str
    retired_at: dt.datetime | None
    # The definition it followed: ``deleted`` with it, ``followed`` by another portfolio, or
    # ``failed``: the portfolio is deleted, and the definition's own delete did not complete.
    signal_definition_uid: str | None = None
    signal_definition_state: str | None = None
    # Its signal's rows, when the definition went: ``deleted``, or ``kept`` while a retired
    # `Portfolio` still follows the signal.
    signal: dict[str, Any] | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "configuration_uid": self.configuration_uid,
            "deleted": True,
            "portfolio": {
                "uid": self.portfolio_uid,
                "unique_identifier": self.portfolio_unique_identifier,
                "state": self.portfolio_state,
                "retired_at": self.retired_at,
            },
            "signal_definition": (
                {"uid": self.signal_definition_uid, "state": self.signal_definition_state}
                if self.signal_definition_uid is not None
                else None
            ),
            "signal": self.signal,
        }


def ended_portfolio_account_uid(configuration: AlpacaPortfolioConfiguration) -> str | None:
    """The account an ended portfolio belonged to.

    A portfolio not yet adopted has none; its former bars configuration's account, whose keys
    priced it, takes its place.
    """
    if configuration.account_uid is not None:
        return str(configuration.account_uid)
    if configuration.bars_configuration_uid is None:
        return None
    from alpaca_connectors.market_data.configurations import get_bar_configuration

    bars = get_bar_configuration(configuration.bars_configuration_uid)
    if bars is None or bars.account_uid is None:
        return None
    return str(bars.account_uid)


def _end_followed_definition(
    configuration: AlpacaPortfolioConfiguration,
) -> tuple[str | None, str | None, dict[str, Any] | None]:
    """Delete the definition the ended portfolio followed once no portfolio follows it.

    Issue #38: a portfolio is the connector's, and so is what it is made of, so the application
    does not delete the definition in a second call. Its signal's rows go as
    ``delete_signal_rows`` decides: kept while a retired `Portfolio` still follows the signal,
    which the one just retired does, else deleted. A definition another portfolio of the account
    still follows stays, and goes with the last of them. Answers ``(uid, state, signal)``.

    The portfolio is already deleted when this runs, so a failure here never fails its delete,
    which a retry could not repeat: the answer says ``failed``, and the definition, which no
    portfolio follows any more, is deleted from the account's signals.
    """
    from alpaca_connectors.portfolios.signal_definitions import (
        SignalDefinitionError,
        delete_signal_definition,
    )

    if configuration.signal_definition_uid is None:
        return None, None, None
    uid = str(configuration.signal_definition_uid)
    try:
        result = delete_signal_definition(uid)
    except SignalDefinitionError as exc:
        if exc.code == "signal_followed":
            return uid, "followed", None
        reason = exc.code
    except LookupError:
        return uid, "deleted", None  # An earlier, interrupted attempt deleted it.
    except Exception as exc:  # noqa: BLE001 - reported, never raised: the portfolio is gone
        reason = type(exc).__name__
    else:
        return uid, "deleted", result["signal"]
    logger.warning(
        "The portfolio is deleted, but its signal definition's delete failed (%s); delete the "
        "definition from the account's signals.",
        reason,
    )
    return uid, "failed", None


def end_portfolio_configuration(
    configuration: AlpacaPortfolioConfiguration,
) -> PortfolioRemoval:
    """End a portfolio: retire its `Portfolio`, record it, then delete its configuration.

    Every delete ends a portfolio this way (ADR 0027 decision 4, ADR 0028): retiring comes first
    (ms-markets ADR 0049), so a portfolio an account's current target names is refused with
    ``409 portfolio_in_account_target`` and nothing changes. A configuration whose `Portfolio` no
    longer exists answers ``absent``. The retired `Portfolio` and the signal it followed are
    recorded under the account, which deletes them when it is deleted; then the configuration
    row is deleted, and its own bars configuration with it, then the signal definition it
    followed once no other portfolio follows it (issue #38). The `Portfolio`, its series, its
    signal's rows and account history stay. Retiring a retired portfolio does nothing, so a retry
    after a partial failure is safe. It checks no owner: its callers do.
    """
    from msm.api.remote import MarketsConflictError

    from alpaca_connectors.markets_api import retire_portfolio
    from alpaca_connectors.portfolios.configurations import (
        delete_portfolio_configuration_row,
        record_ended_portfolio,
    )

    portfolio_uid = (
        str(configuration.portfolio_uid) if configuration.portfolio_uid is not None else None
    )
    retired_at = None
    state = "absent"
    if portfolio_uid is not None:
        try:
            portfolio = retire_portfolio(portfolio_uid)
        except MarketsConflictError as exc:
            # Never names the accounts: they are the account owners' business (ADR 0019).
            raise ProvidedWeightsError(
                "portfolio_in_account_target",
                "An account's current target names this portfolio. Remove it from that target, "
                "then delete the portfolio.",
                status_code=409,
            ) from exc
        retired_at = getattr(portfolio, "retired_at", None)
        state = "retired"
        account_uid = ended_portfolio_account_uid(configuration)
        if account_uid is not None:
            record_ended_portfolio(
                account_uid=account_uid,
                portfolio_uid=portfolio_uid,
                portfolio_configuration_uid=configuration.uid,
                signal_uid=portfolio_signal_uid(configuration),
            )
    delete_portfolio_configuration_row(configuration.uid)
    definition_uid, definition_state, signal = _end_followed_definition(configuration)
    return PortfolioRemoval(
        configuration_uid=str(configuration.uid),
        portfolio_uid=portfolio_uid,
        portfolio_unique_identifier=provided_portfolio_unique_identifier(configuration.uid),
        portfolio_state=state,
        retired_at=retired_at,
        signal_definition_uid=definition_uid,
        signal_definition_state=definition_state,
        signal=signal,
    )


def delete_provided_weights_portfolio(
    configuration_uid: Any,
    *,
    owner: AccountOwner,
) -> PortfolioRemoval:
    """End a portfolio for its owner (ADR 0024, decision 6).

    Anyone but its owner is refused before anything changes; then
    ``end_portfolio_configuration`` retires its `Portfolio` and deletes its configuration.
    """
    from alpaca_connectors.portfolios.configurations import get_portfolio_configuration

    configuration = get_portfolio_configuration(configuration_uid)
    if configuration is None:
        raise LookupError(f"Portfolio configuration {configuration_uid!s} does not exist.")
    require_portfolio_owner(configuration, owner)
    return end_portfolio_configuration(configuration)


def give_portfolio_weights(
    configuration_uid: Any,
    observations: Sequence[Mapping[str, Any]],
    *,
    owner: AccountOwner,
) -> PublishedWeights:
    """Give the signal the portfolio follows one or more new observations (ADR 0024's route)."""
    from alpaca_connectors.portfolios.configurations import get_portfolio_configuration

    configuration = get_portfolio_configuration(configuration_uid)
    if configuration is None:
        raise LookupError(f"Portfolio configuration {configuration_uid!s} does not exist.")
    require_portfolio_owner(configuration, owner)
    return publish_provided_weights(configuration, observations)


def _upsert_portfolio_row(configuration: AlpacaPortfolioConfiguration) -> Any:
    from msm.api.portfolios import Portfolio

    from alpaca_connectors.assets.calendars import ensure_trading_calendar
    from alpaca_connectors.portfolios.configurations import get_rebalance_configuration

    rebalance = get_rebalance_configuration(configuration.rebalance_configuration_uid)
    if rebalance is None:
        raise LookupError(
            f"Rebalance configuration {configuration.rebalance_configuration_uid!s} does not exist."
        )
    calendar_row = ensure_trading_calendar(rebalance.calendar_identifier)
    return Portfolio.upsert(
        unique_identifier=provided_portfolio_unique_identifier(configuration.uid),
        calendar_uid=calendar_row.uid,
    )


def _create_own_bars(
    configuration: AlpacaPortfolioConfiguration,
    profile: Mapping[str, Any],
) -> Any:
    """The portfolio's own bars configuration, which prices its signal's assets (decision 4)."""
    from alpaca_connectors.market_data.configurations import create_portfolio_bar_configuration

    return create_portfolio_bar_configuration(
        portfolio_configuration_uid=configuration.uid,
        name=profile.get("name") or (configuration.name + OWN_BARS_SUFFIX)[:255],
        description=profile.get("description"),
        frequency_id=profile.get("frequency_id") or "1d",
        feed=profile.get("feed") or "sip",
        adjustment=profile.get("adjustment") or "all",
        enabled=profile.get("enabled", True),
        scheduled=profile.get("scheduled", True),
    )


def create_portfolio(
    *,
    owner: AccountOwner,
    account_uid: Any,
    signal_definition_uid: Any,
    name: str,
    rebalance_configuration_uid: Any,
    bars: Mapping[str, Any] | None = None,
    description: str | None = None,
    upsample_frequency_id: str = "1d",
    intraday_bar_interpolation_rule: str = "ffill",
    valuation_column: str = "close",
    valuation_maximum_staleness_seconds: int = 86_400,
    fail_on_missing_prices: bool = True,
    commission_fee: float = 0.00018,
    scheduled: bool = True,
) -> ProvidedPortfolioCreation:
    """Create a portfolio of the account that follows one of the account's signal definitions.

    The caller has checked that ``owner`` owns the account (ADR 0028, decision 3). Order: the
    configuration row, its own bars configuration, then the `Portfolio` row. A failure after the
    row was stored deletes it, and its bars configuration with it.
    """
    from alpaca_connectors.portfolios.configurations import (
        create_portfolio_configuration_row,
        delete_portfolio_configuration_row,
        update_portfolio_configuration_row,
    )
    from alpaca_connectors.portfolios.signal_definitions import get_signal_definition

    definition = get_signal_definition(signal_definition_uid)
    if definition is None or str(definition.account_uid) != str(uuid.UUID(str(account_uid))):
        raise ProvidedWeightsError(
            "signal_of_another_account",
            "The portfolio must follow a signal definition of its own account.",
            status_code=409,
        )
    configuration: AlpacaPortfolioConfiguration | None = None
    try:
        configuration = create_portfolio_configuration_row(
            name=name,
            description=description,
            account_uid=account_uid,
            signal_definition_uid=definition.uid,
            owner_gateway_uid=owner.gateway_uid,
            owner_user_uid=owner.user_uid,
            rebalance_configuration_uid=rebalance_configuration_uid,
            upsample_frequency_id=upsample_frequency_id,
            intraday_bar_interpolation_rule=intraday_bar_interpolation_rule,
            valuation_column=valuation_column,
            valuation_maximum_staleness_seconds=valuation_maximum_staleness_seconds,
            fail_on_missing_prices=fail_on_missing_prices,
            commission_fee=commission_fee,
            scheduled=scheduled,
        )
        own_bars = _create_own_bars(configuration, bars or {})
        portfolio_row = _upsert_portfolio_row(configuration)
        configuration = update_portfolio_configuration_row(
            configuration.uid,
            values={"portfolio_uid": uuid.UUID(str(portfolio_row.uid))},
        )
    except Exception:
        if configuration is not None:
            delete_portfolio_configuration_row(configuration.uid)
        raise
    return ProvidedPortfolioCreation(
        configuration=configuration,
        published=None,
        portfolio_uid=str(portfolio_row.uid),
        portfolio_unique_identifier=str(portfolio_row.unique_identifier),
        bars_configuration_created=True,
        bars_configuration_uid=str(own_bars.uid),
        signal_definition=definition,
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
    """Create a portfolio from its first observation or a history (ADR 0024's route).

    The portfolio belongs to the account of the bars configuration the request gives inline or
    names, which the caller has checked the owner owns; it gets its own bars configuration with
    that profile, and a signal definition of that account whose key is its configuration UID, as
    its signal's key was (ADR 0028). Order: validate the observations and assets, store the row,
    its definition and its own bars configuration, publish, then create the `Portfolio` row. A
    failure after a row was stored deletes the rows this call stored.
    """
    from alpaca_connectors.market_data.configurations import get_bar_configuration
    from alpaca_connectors.portfolios.configurations import (
        create_portfolio_configuration_row,
        delete_portfolio_configuration_row,
        update_portfolio_configuration_row,
    )
    from alpaca_connectors.portfolios.signal_definitions import (
        SignalDefinition,
        create_signal_definition,
    )

    if (bars_configuration_uid is None) == (bars_configuration is None):
        raise ProvidedWeightsError(
            "invalid_bars_configuration",
            "Give either bars_configuration_uid or bars_configuration, not both.",
        )
    normalized = normalize_observations(observations)
    assets = _observation_assets(normalized)
    require_alpaca_assets(assets)

    if bars_configuration is not None:
        profile = dict(bars_configuration)
        account_uid = profile.get("account_uid")
    else:
        named = get_bar_configuration(bars_configuration_uid)
        if named is None:
            raise LookupError(f"Bars configuration {bars_configuration_uid!s} does not exist.")
        if named.account_uid is None:
            raise ProvidedWeightsError(
                "invalid_bars_configuration",
                "Name a bars feed of an account; a portfolio's own configuration is its alone.",
            )
        account_uid = named.account_uid
        profile = {
            "frequency_id": named.frequency_id,
            "feed": named.feed,
            "adjustment": named.adjustment,
            "enabled": named.enabled,
            "scheduled": named.scheduled,
        }
    if account_uid is None:
        raise ProvidedWeightsError(
            "invalid_bars_configuration", "The bars configuration must name its account."
        )

    configuration: AlpacaPortfolioConfiguration | None = None
    definition: SignalDefinition | None = None
    try:
        configuration = create_portfolio_configuration_row(
            name=name,
            description=description,
            account_uid=account_uid,
            signal_definition_uid=None,
            owner_gateway_uid=owner.gateway_uid,
            owner_user_uid=owner.user_uid,
            rebalance_configuration_uid=rebalance_configuration_uid,
            upsample_frequency_id=upsample_frequency_id,
            intraday_bar_interpolation_rule=intraday_bar_interpolation_rule,
            valuation_column=valuation_column,
            valuation_maximum_staleness_seconds=valuation_maximum_staleness_seconds,
            fail_on_missing_prices=fail_on_missing_prices,
            commission_fee=commission_fee,
            scheduled=scheduled,
        )
        key = legacy_signal_key(configuration.uid)
        definition = create_signal_definition(
            account_uid=account_uid,
            key=key,
            name=configuration.name,
            description=description,
            signal_key=key,
        )
        configuration = update_portfolio_configuration_row(
            configuration.uid,
            values={"signal_definition_uid": definition.uid},
        )
        own_bars = _create_own_bars(configuration, profile)
        published = _publish_validated(
            definition.signal_key,
            normalized,
            assets,
            description=description,
            metadata_description=_signal_description(definition),
        )
        portfolio_row = _upsert_portfolio_row(configuration)
        configuration = update_portfolio_configuration_row(
            configuration.uid,
            values={"portfolio_uid": uuid.UUID(str(portfolio_row.uid))},
        )
    except Exception:
        if configuration is not None:
            delete_portfolio_configuration_row(configuration.uid)
        if definition is not None:
            SignalDefinition.delete(definition.uid)
        raise
    return ProvidedPortfolioCreation(
        configuration=configuration,
        published=published,
        portfolio_uid=str(portfolio_row.uid),
        portfolio_unique_identifier=str(portfolio_row.unique_identifier),
        bars_configuration_created=True,
        bars_configuration_uid=str(own_bars.uid),
        signal_definition=definition,
    )


__all__ = [
    "OWN_BARS_SOURCE",
    "PROVIDED_PORTFOLIO_PREFIX",
    "PortfolioOwnedByAnotherError",
    "PortfolioRemoval",
    "ProvidedPortfolioCreation",
    "ProvidedWeightsError",
    "PublishedWeights",
    "create_portfolio",
    "create_provided_weights_portfolio",
    "delete_provided_weights_portfolio",
    "end_portfolio_configuration",
    "ended_portfolio_account_uid",
    "ensure_signal_metadata",
    "give_portfolio_weights",
    "is_owner",
    "normalize_observations",
    "portfolio_signal_key",
    "portfolio_signal_uid",
    "provided_portfolio_unique_identifier",
    "provided_signal_uid",
    "publish_definition_weights",
    "publish_provided_weights",
    "require_alpaca_assets",
    "require_portfolio_owner",
]
