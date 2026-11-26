"""Portfolios built from weights an application provides (ADR 0024), owned by accounts (ADR 0028).

Every portfolio of this connector is one (ADR 0027): a row of the portfolio configuration table
that belongs to one account and follows one signal definition of that account (ADR 0028). The
signal is ms-markets' ``ApplicationWeights`` with ``provider`` = this connector's namespace and the
definition's ``signal_key``. Each call that gives weights publishes one or more observations to
that signal in Markets with the ms-markets recipe ``publish_signal_weights`` (ADR 0031); Markets
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
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from msm.base import new_markets_uid

from alpaca_connectors.account.ownership import AccountOwner
from alpaca_connectors.portfolios.configurations import (
    AlpacaPortfolioConfiguration,
    OwnTableWrites,
)
from alpaca_connectors.portfolios.signal_definitions import (
    SignalDefinition,
    legacy_signal_key,
    signal_uid_for_key,
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
    # When these weights take effect for the portfolio they were given to: the rebalance after
    # the last of them (issue #48). Set by ``give_portfolio_weights``; None otherwise, or when
    # its calendar has no rebalance in the search window.
    weights_effective_at: dt.datetime | None = None


@dataclass(frozen=True, slots=True)
class ProvidedPortfolioCreation:
    configuration: AlpacaPortfolioConfiguration
    published: PublishedWeights | None
    portfolio_uid: str
    portfolio_unique_identifier: str
    bars_configuration_created: bool
    bars_configuration_uid: str
    signal_definition: SignalDefinition
    rebalance_configuration_created: bool = False
    # False when a create named a key the account already used: the portfolio created with it
    # is answered and nothing is created or published (issue #43).
    created: bool = True
    # What the create wrote or resolved, so its answer and its first run read none of it again
    # (issue #46); None when it was not in hand.
    rebalance_configuration: Any = None
    bars_configuration: Any = None
    # The portfolio's first rebalance, after its first weights (issue #48); computed with the
    # publication by a create that created, None for one that did not or when the calendar has
    # no rebalance in the search window.
    first_rebalance_at: dt.datetime | None = None


def provided_portfolio_unique_identifier(configuration_uid: Any) -> str:
    return f"{PROVIDED_PORTFOLIO_PREFIX}{str(configuration_uid).replace('-', '').upper()}"


def configuration_uid_from_portfolio_identifier(unique_identifier: str) -> uuid.UUID | None:
    """The configuration UID a portfolio's ``unique_identifier`` was made from, or None."""
    value = str(unique_identifier or "")
    if not value.startswith(PROVIDED_PORTFOLIO_PREFIX):
        return None
    try:
        return uuid.UUID(hex=value[len(PROVIDED_PORTFOLIO_PREFIX) :])
    except ValueError:
        return None


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
    """Refuse an identifier that is not a registered Alpaca asset.

    One read of this application's Alpaca details, by the ``alpaca_asset_id`` each
    ``ALPACA::<alpaca_asset_id>`` identifier carries (ADR 0004), and no Markets call (issue #46):
    a details row goes with its Markets asset, so a row found is a registered asset. An Alpaca
    identifier with no row is ``unknown_asset``; an identifier that is not an Alpaca one can have
    no bars, ``asset_not_alpaca``. No message names an asset.
    """
    from alpaca_connectors.assets.alpaca_asset_details import parse_alpaca_unique_identifier
    from alpaca_connectors.assets.resolution import alpaca_details_by_identifier

    identifiers = sorted(set(asset_identifiers))
    if not identifiers:
        return []
    missing = set(identifiers) - set(alpaca_details_by_identifier(identifiers))
    if not missing:
        return identifiers

    def is_alpaca(identifier: str) -> bool:
        try:
            parse_alpaca_unique_identifier(identifier)
        except (TypeError, ValueError):
            return False
        return True

    if any(is_alpaca(identifier) for identifier in missing):
        raise ProvidedWeightsError(
            "unknown_asset", "An asset identifier is not a registered Main Sequence asset."
        )
    raise ProvidedWeightsError(
        "asset_not_alpaca", "An asset is not a registered Alpaca asset, so it has no bars."
    )


def _not_after_last() -> ProvidedWeightsError:
    return ProvidedWeightsError(
        "observation_not_after_last",
        "An observation is at or before the signal's last stored observation; "
        "published observations are never restated.",
        status_code=409,
    )


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


def _last_observed_at(signal_uid: str) -> dt.datetime | None:
    """The signal's last published observation time, read from Markets (``signal_history``)."""
    from alpaca_connectors.portfolios.signal_observations import read_signal_observation_bounds

    return read_signal_observation_bounds(signal_uid=signal_uid).last_observation_at


def _publish_validated(
    signal_key: str,
    normalized: Mapping[dt.datetime, Mapping[str, float]],
    assets: Sequence[str],
    *,
    description: str | None,
    metadata_description: str,
    new_signal: bool = False,
) -> PublishedWeights:
    """Publish observations whose shape and assets were already validated.

    Refuse an observation at or before the signal's last published one, record the assets
    (ADR 0024 decision 5), then publish with the ms-markets recipe ``publish_signal_weights``
    (ADR 0031, decision 4), which creates the signal's ``SignalMetadata`` on its first
    publication and writes an asset dropped since the last observation at 0. Nothing is
    registered or bound.

    The not-after-last rule is checked here first, with one Markets read: the recipe refuses an
    earlier observation only when it differs from what is stored, and silently skips one equal
    to it, while this application refuses both (``409 observation_not_after_last``). The recipe
    refuses every other bad observation with the same exception type, so which refusal is a
    time conflict (another call published first) is decided from the stored last time, never
    from the wording of Markets' message.

    ``new_signal`` is the create's: its signal's key comes from the configuration UID that call
    made, so it has no observation and no recorded asset, and neither is read (issue #46).
    """
    from alpaca_connectors.markets_api import SignalWeightsRefused, publish_signal_weights
    from alpaca_connectors.portfolios.signal_assets import record_signal_assets

    signal_uid = signal_uid_for_key(signal_key)
    first_time = next(iter(normalized))
    last_stored = None if new_signal else _last_observed_at(signal_uid)
    if last_stored is not None and first_time <= last_stored:
        raise _not_after_last()
    # First, so the recorded assets stay a superset of the published ones (ADR 0024).
    record_signal_assets(signal_uid, assets, new_signal=new_signal)
    try:
        # The signal's own description, or on its first publication the connector's words for
        # it, as the signal's metadata described it before (ADR 0024).
        publish_signal_weights(
            signal_uid, normalized, description=description or metadata_description
        )
    except SignalWeightsRefused as exc:
        if exc.reason == "unknown_asset":
            raise ProvidedWeightsError(
                "unknown_asset", "An asset identifier is not a registered Main Sequence asset."
            ) from exc
        last_stored = None if new_signal else _last_observed_at(signal_uid)
        if last_stored is not None and first_time <= last_stored:
            raise _not_after_last() from exc
        raise _invalid(
            "The observations were refused: an empty set with nothing to close, or a value the "
            "signal cannot store."
        ) from exc
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
    # The definition it followed: ``deleted`` with it, or ``followed`` by another portfolio.
    signal_definition_uid: str | None = None
    signal_definition_state: str | None = None
    # Its signal's rows, when the definition went: ``deleted``, or ``kept`` while a retired
    # `Portfolio` still follows the signal.
    signal: dict[str, Any] | None = None
    # The rebalance configuration created inline with it (issue #42): ``deleted`` with it, or
    # ``followed`` while another portfolio names it. None for a shared rule.
    rebalance_configuration_uid: str | None = None
    rebalance_configuration_state: str | None = None
    # The signal recorded with its retired `Portfolio` under the account (ADR 0028), which the
    # account's delete deletes; None when nothing was recorded. Not part of the answer.
    recorded_signal_uid: str | None = None

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
            "rebalance_configuration": (
                {
                    "uid": self.rebalance_configuration_uid,
                    "state": self.rebalance_configuration_state,
                }
                if self.rebalance_configuration_uid is not None
                else None
            ),
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


def _in_account_target(exc: Exception) -> ProvidedWeightsError:
    # Never names the accounts: they are the account owners' business (ADR 0019).
    error = ProvidedWeightsError(
        "portfolio_in_account_target",
        "An account's current target names this portfolio. Remove it from that target, "
        "then delete the portfolio.",
        status_code=409,
    )
    error.__cause__ = exc
    return error


def _ended_accounts(configurations: Sequence[AlpacaPortfolioConfiguration]) -> dict[str, str]:
    """The account each ended portfolio belonged to, as ``ended_portfolio_account_uid`` says,
    with the former feeds of the portfolios not yet adopted read in one request."""
    from alpaca_connectors.market_data.configurations import bar_configuration_rows_by_uids

    feeds = bar_configuration_rows_by_uids(
        [
            configuration.bars_configuration_uid
            for configuration in configurations
            if configuration.account_uid is None
            and configuration.bars_configuration_uid is not None
        ]
    )
    accounts: dict[str, str] = {}
    for configuration in configurations:
        if configuration.account_uid is not None:
            accounts[str(configuration.uid)] = str(configuration.account_uid)
            continue
        feed = feeds.get(str(configuration.bars_configuration_uid))
        if feed is not None and feed.account_uid is not None:
            accounts[str(configuration.uid)] = str(feed.account_uid)
    return accounts


def end_portfolio_configuration(
    configuration: AlpacaPortfolioConfiguration,
) -> PortfolioRemoval:
    """End a portfolio: retire its `Portfolio`, then record it and delete what it was made of.

    Every delete ends a portfolio this way (ADR 0027 decision 4, ADR 0028). Retiring comes first
    (ms-markets ADR 0049), so a portfolio an account's current target names is refused with
    ``409 portfolio_in_account_target`` and nothing changes. A configuration whose `Portfolio` no
    longer exists answers ``absent``. Then one transaction:

    - records the retired `Portfolio` and the signal it followed under the account, which deletes
      them when it is deleted;
    - deletes the configuration row, and its own bars configuration with it;
    - deletes the signal definition it followed, once no other portfolio follows it (issue #38);
    - deletes a rebalance configuration created inline with it, once no other portfolio names it
      (issue #42).

    The `Portfolio`, its series, its signal's rows and account history stay. Retiring a retired
    portfolio does nothing and a record already stored is kept, so a retry after a failure is
    safe. It checks no owner: its callers do.
    """
    return end_portfolio_configurations([configuration])[0]


def end_portfolio_configurations(
    configurations: Sequence[AlpacaPortfolioConfiguration],
    *,
    definitions: Iterable[SignalDefinition] | None = None,
    writes: OwnTableWrites | None = None,
) -> list[PortfolioRemoval]:
    """End several portfolios as ``end_portfolio_configuration`` ends one, in bulk (#46).

    1. **Retire.** The `Portfolio` rows are retired first, in one ms-markets call
       (``retire_portfolios``, MainSequenceMarkets#124). One Markets no longer has ends as
       ``absent``.
    2. **Read, in one step.** These reads run together:
       - the definitions they followed, unless the caller passes ``definitions`` (the account
         delete passes the account's own, already read);
       - the portfolios still following each definition and the rules created inline with them,
         both leaving out the configurations being deleted;
       - the former feeds of the portfolios not yet adopted.

       Only when there are inline rules, a second read asks which of them another portfolio
       names.
    3. **Write, in one transaction** (``OwnTableWrites``): their records, then the configurations,
       then the definitions no other portfolio follows and the inline rules no other portfolio
       names. The caller's own ``writes`` go first in the same transaction, when every portfolio
       was retired.
    4. **Signals.** The signals of the deleted definitions go as ``delete_signals_rows`` decides.
       The ones just recorded are known to be kept and are not read.

    When retiring one is refused, every other portfolio is ended all the same, without the
    caller's ``writes``, and then ``portfolio_in_account_target`` is raised: the refused
    portfolios are unchanged.
    """
    from msm.api.remote import MarketsConflictError

    from alpaca_connectors.markets_api import all_together, retire_portfolios
    from alpaca_connectors.portfolios.configurations import (
        AlpacaPortfolioConfigurationTable,
        EndedPortfolioTable,
        PortfolioRebalanceConfigurationTable,
        ended_portfolio_rows,
        inline_rebalance_configurations,
        portfolio_counts_by_definition,
        rebalance_configuration_uids_named,
    )
    from alpaca_connectors.portfolios.signal_definitions import (
        SignalDefinitionTable,
        definition_signal_uid,
        delete_signals_rows,
        signal_definitions_by_uids,
    )

    retirement = retire_portfolios(
        [row.portfolio_uid for row in configurations if row.portfolio_uid is not None]
    )
    ended: list[AlpacaPortfolioConfiguration] = []
    retired: dict[str, Any] = {}
    refusal: ProvidedWeightsError | None = None
    for configuration in configurations:
        if configuration.portfolio_uid is not None:
            portfolio_uid = str(uuid.UUID(str(configuration.portfolio_uid)))
            if portfolio_uid in retirement.refused:
                refusal = refusal or _in_account_target(
                    MarketsConflictError(retirement.refused[portfolio_uid])
                )
                continue
            if portfolio_uid in retirement.retired:
                retired[str(configuration.uid)] = retirement.retired[portfolio_uid]
        ended.append(configuration)
    if not ended:
        if refusal is not None:
            raise refusal
        return []

    ended_uids = [row.uid for row in ended]
    definition_uids = list(
        dict.fromkeys(
            str(row.signal_definition_uid) for row in ended if row.signal_definition_uid is not None
        )
    )
    retired_rows = [row for row in ended if str(row.uid) in retired]
    given = None if definitions is None else {str(row.uid): row for row in definitions}
    followed_definitions, accounts, followers, inline = all_together(
        (lambda: given)
        if given is not None
        else (lambda: signal_definitions_by_uids(definition_uids)),
        lambda: _ended_accounts(retired_rows),
        lambda: portfolio_counts_by_definition(definition_uids, excluding=ended_uids),
        lambda: inline_rebalance_configurations(ended_uids),
    )
    named = (
        rebalance_configuration_uids_named(
            [rule.uid for rule in inline.values()], excluding=ended_uids
        )
        if inline
        else set()
    )
    records = [
        {
            "account_uid": accounts[str(row.uid)],
            "portfolio_uid": str(row.portfolio_uid),
            "portfolio_configuration_uid": row.uid,
            "signal_uid": portfolio_signal_uid(row, definitions=followed_definitions),
        }
        for row in retired_rows
        if str(row.uid) in accounts
    ]
    free_definitions = [
        followed_definitions[uid]
        for uid in definition_uids
        if uid in followed_definitions and not followers.get(uid)
    ]
    free_rules = [rule.uid for rule in inline.values() if str(rule.uid) not in named]

    transaction = OwnTableWrites()
    if writes is not None and refusal is None:
        transaction.extend(writes)
    if records:
        transaction.insert_missing(
            EndedPortfolioTable, ended_portfolio_rows(records), conflict_columns=("portfolio_uid",)
        )
    transaction.delete_in(AlpacaPortfolioConfigurationTable, "uid", ended_uids)
    transaction.delete_in(SignalDefinitionTable, "uid", [row.uid for row in free_definitions])
    transaction.delete_in(PortfolioRebalanceConfigurationTable, "uid", free_rules)
    transaction.run()

    recorded = {
        str(record["portfolio_configuration_uid"]): str(record["signal_uid"]) for record in records
    }
    signals = delete_signals_rows(
        [definition_signal_uid(row) for row in free_definitions], kept=list(recorded.values())
    )
    # A definition no longer stored was deleted by an earlier, interrupted attempt.
    definition_answers: dict[str, dict[str, Any]] = {
        uid: (
            {"state": "followed", "signal": None}
            if followers.get(uid)
            else {"state": "deleted", "signal": None}
        )
        for uid in definition_uids
    }
    for row in free_definitions:
        signal_uid = definition_signal_uid(row)
        definition_answers[str(row.uid)] = {
            "state": "deleted",
            "signal": {"uid": signal_uid, "state": signals[signal_uid]},
        }
    rebalances = {
        uid: (str(rule.uid), "followed" if str(rule.uid) in named else "deleted")
        for uid, rule in inline.items()
    }

    removals: list[PortfolioRemoval] = []
    for row in ended:
        portfolio = retired.get(str(row.uid))
        definition_uid = (
            str(row.signal_definition_uid) if row.signal_definition_uid is not None else None
        )
        definition_answer = definition_answers.get(definition_uid or "", {})
        rebalance_uid, rebalance_state = rebalances.get(str(row.uid), (None, None))
        removals.append(
            PortfolioRemoval(
                configuration_uid=str(row.uid),
                portfolio_uid=str(row.portfolio_uid) if row.portfolio_uid is not None else None,
                portfolio_unique_identifier=provided_portfolio_unique_identifier(row.uid),
                portfolio_state="retired" if str(row.uid) in retired else "absent",
                retired_at=getattr(portfolio, "retired_at", None),
                signal_definition_uid=definition_uid,
                signal_definition_state=definition_answer.get("state"),
                signal=definition_answer.get("signal"),
                rebalance_configuration_uid=rebalance_uid,
                rebalance_configuration_state=rebalance_state,
                recorded_signal_uid=recorded.get(str(row.uid)),
            )
        )
    if refusal is not None:
        raise refusal
    return removals


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
    """Give the signal the portfolio follows one or more new observations (ADR 0024's route).

    The answer says when the last of them takes effect for the portfolio (issue #48): the
    rebalance after it, computed while they are published.
    """
    from alpaca_connectors.markets_api import all_together
    from alpaca_connectors.portfolios.configurations import get_portfolio_configuration

    configuration = get_portfolio_configuration(configuration_uid)
    if configuration is None:
        raise LookupError(f"Portfolio configuration {configuration_uid!s} does not exist.")
    require_portfolio_owner(configuration, owner)
    latest = list(normalize_observations(observations))[-1]
    published, effective = all_together(
        lambda: publish_provided_weights(configuration, observations),
        lambda: _rebalance_after(configuration.rebalance_configuration_uid, latest),
    )
    return replace(published, weights_effective_at=effective)


def _rebalance_after(rebalance_configuration_uid: Any, observed_at: dt.datetime) -> Any:
    """The rebalance after weights observed at ``observed_at``, by the portfolio's rule."""
    from alpaca_connectors.portfolios import rebalance_schedule
    from alpaca_connectors.portfolios.configurations import get_rebalance_configuration

    rebalance = get_rebalance_configuration(rebalance_configuration_uid)
    if rebalance is None:
        return None
    return rebalance_schedule.first_rebalance_at(rebalance, starts_at=observed_at)


def _upsert_portfolio_row(configuration_uid: Any, rebalance: Any) -> Any:
    """The configuration's `Portfolio` row, on the calendar of ``rebalance``, the rule it names,
    which the create already holds."""
    from msm.api.portfolios import Portfolio

    from alpaca_connectors.assets.calendars import ensure_trading_calendar

    calendar_row = ensure_trading_calendar(rebalance.calendar_identifier)
    return Portfolio.upsert(
        unique_identifier=provided_portfolio_unique_identifier(configuration_uid),
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
    row was stored deletes it, and its bars configuration with it. The rebalance configuration is
    read once and nothing this call writes is read back (issue #46).
    """
    from alpaca_connectors.portfolios.configurations import (
        create_portfolio_configuration_row,
        delete_portfolio_configuration_row,
        update_portfolio_configuration_row,
        validate_portfolio_references,
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
        rebalance = validate_portfolio_references(
            rebalance_configuration_uid=uuid.UUID(str(rebalance_configuration_uid))
        )
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
            rebalance=rebalance,
        )
        own_bars = _create_own_bars(configuration, bars or {})
        portfolio_row = _upsert_portfolio_row(configuration.uid, rebalance)
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
        rebalance_configuration=rebalance,
        bars_configuration=own_bars,
    )


def _discard_failed_creation(
    definition: SignalDefinition | None,
    portfolio_row: Any,
    rebalance: Any = None,
) -> None:
    """Delete what a failed create stored, so nothing of it stays (issue #37).

    The configuration and its own bars configuration are the create's last write, one
    transaction, so a failed create never stored them. The rebalance configuration the request
    gave inline goes first; then the `Portfolio` row when it was created, which nothing references
    yet; then the signal's rows and its definition. The signal is this call's alone: its key is
    the new configuration UID. A step that fails is logged and the next still runs, so the caller
    gets the error that ended the create.
    """
    from alpaca_connectors.markets_api import delete_portfolio
    from alpaca_connectors.portfolios.configurations import delete_rebalance_configuration
    from alpaca_connectors.portfolios.signal_definitions import delete_signal_rows

    steps: list[Callable[[], Any]] = []
    if rebalance is not None:
        steps.append(lambda: delete_rebalance_configuration(rebalance.uid))
    if portfolio_row is not None:
        steps.append(lambda: delete_portfolio(str(portfolio_row.uid)))
    if definition is not None:
        steps.append(lambda: delete_signal_rows(signal_uid_for_key(definition.signal_key)))
        steps.append(lambda: SignalDefinition.delete(definition.uid))
    for step in steps:
        try:
            step()
        except Exception as exc:  # noqa: BLE001 - the create's own error is the one raised
            logger.warning("A failed portfolio create left a row behind (%s).", type(exc).__name__)


def _portfolio_created_with_key(
    account_uid: Any,
    key: str,
    *,
    owner: AccountOwner,
) -> ProvidedPortfolioCreation | None:
    """The portfolio an earlier create made with ``key`` in the account, or None (issue #43).

    The key is its signal definition's, unique per account. A definition with the key that no
    finished portfolio follows (one made on its own, or a create still running) is refused; so is
    another owner's portfolio.
    """
    from alpaca_connectors.market_data.configurations import portfolio_bar_configurations
    from alpaca_connectors.portfolios.configurations import list_portfolio_configurations
    from alpaca_connectors.portfolios.signal_definitions import account_signal_definition

    definition = account_signal_definition(account_uid, key)
    if definition is None:
        return None
    rows, _ = list_portfolio_configurations(limit=1, offset=0, signal_definition_uid=definition.uid)
    configuration = rows[0] if rows else None
    if configuration is None or configuration.portfolio_uid is None:
        raise ProvidedWeightsError(
            "signal_key_exists",
            "This account already has a signal with this key, and no finished portfolio follows "
            "it. If a create with this key is still running, ask again when it has ended.",
            status_code=409,
        )
    require_portfolio_owner(configuration, owner)
    # Its own bars configuration's row alone: the answer needs only its UID.
    bars = portfolio_bar_configurations([configuration.uid]).get(str(configuration.uid))
    return ProvidedPortfolioCreation(
        configuration=configuration,
        published=None,
        portfolio_uid=str(configuration.portfolio_uid),
        portfolio_unique_identifier=provided_portfolio_unique_identifier(configuration.uid),
        bars_configuration_created=False,
        bars_configuration_uid=str(bars.uid) if bars is not None else "",
        signal_definition=definition,
        created=False,
        bars_configuration=bars,
    )


def _outcome(call: Callable[[], Any]) -> Any:
    """What ``call`` answered, or the exception it raised, so calls run together can each be kept
    or undone before the first failure is raised."""
    try:
        return call()
    except Exception as exc:  # noqa: BLE001 - the caller raises it
        return exc


def _together(*calls: Callable[[], Any]) -> list[Any]:
    """Run ``calls`` together (``all_together``) and answer each one's outcome."""
    from alpaca_connectors.markets_api import all_together

    return all_together(*(lambda call=call: _outcome(call) for call in calls))


def _kept(outcome: Any) -> Any:
    return None if isinstance(outcome, Exception) else outcome


def _raise_first(outcomes: Iterable[Any]) -> None:
    for outcome in outcomes:
        if isinstance(outcome, Exception):
            raise outcome


def create_provided_weights_portfolio(
    *,
    owner: AccountOwner,
    name: str,
    observations: Sequence[Mapping[str, Any]],
    key: str | None = None,
    rebalance_configuration_uid: Any | None = None,
    rebalance_configuration: Mapping[str, Any] | None = None,
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
    its signal's key was (ADR 0028).

    Calls that do not depend on each other go together (issue #46), in four steps:

    1. **Read.** The assets, the named bars configuration, the named rebalance rule and, once the
       account is known, the portfolio an earlier create made with ``key``. Their errors come in
       the order the create checks them: the assets, the bars configuration, the key, the rule.
       Then the configuration row and its own bars configuration are validated, before anything
       is written.
    2. **Definition.** The signal definition, alongside an inline rebalance rule. The account's
       unique key makes a concurrent create with the same key fail here, before the signal or the
       `Portfolio` exists.
    3. **Publish and `Portfolio`.** The observations are published and the `Portfolio` row is
       created together, while the first rebalance is read from the calendar (issue #48).
    4. **Configuration.** The configuration row, naming its definition and its `Portfolio`, and
       its own bars configuration, in one transaction. Until then a create with the same key finds
       no finished portfolio.

    A failure deletes everything this call stored, its signal's rows included (issue #37), so a
    retry starts clean. Nothing it wrote is read back.

    The rebalance configuration is named, or given inline and created with the definition, as the
    bars configuration is; an inline one goes with the portfolio (issue #42).

    ``key`` makes the create repeatable (issue #43): it is the portfolio's signal definition key,
    unique in the account. A key the account already used answers the portfolio created with it
    (``created`` false) and creates and publishes nothing: the repeated ``observations`` are
    ignored. Without a key, the definition's key is the configuration UID, as before.
    """
    from alpaca_connectors.market_data.configurations import (
        AlpacaBarsConfigurationTable,
        get_bar_configuration,
        portfolio_bar_configuration_values,
        stored_portfolio_bar_configuration,
    )
    from alpaca_connectors.portfolios import rebalance_schedule
    from alpaca_connectors.portfolios.configurations import (
        AlpacaPortfolioConfigurationTable,
        create_rebalance_configuration,
        portfolio_configuration_values,
        validate_portfolio_references,
    )
    from alpaca_connectors.portfolios.signal_definitions import (
        create_signal_definition,
        normalize_key,
    )

    if (bars_configuration_uid is None) == (bars_configuration is None):
        raise ProvidedWeightsError(
            "invalid_bars_configuration",
            "Give either bars_configuration_uid or bars_configuration, not both.",
        )
    if (rebalance_configuration_uid is None) == (rebalance_configuration is None):
        raise ProvidedWeightsError(
            "invalid_rebalance_configuration",
            "Give either rebalance_configuration_uid or rebalance_configuration, not both.",
        )
    normalized = normalize_observations(observations)
    assets = _observation_assets(normalized)
    if key is not None:
        key = normalize_key(key)
    profile = dict(bars_configuration) if bars_configuration is not None else None
    account_uid = profile.get("account_uid") if profile is not None else None

    # 1. Read. The assets' and the named bars' errors are raised as they come; the key's answer
    # and the rule's error wait for the checks before them.
    replay_read = key is not None and account_uid is not None
    reads: list[Callable[[], Any]] = [lambda: require_alpaca_assets(assets)]
    if bars_configuration_uid is not None:
        reads.append(lambda: get_bar_configuration(bars_configuration_uid))
    if replay_read:
        reads.append(lambda: _portfolio_created_with_key(account_uid, key, owner=owner))
    if rebalance_configuration_uid is not None:
        reads.append(
            lambda: validate_portfolio_references(
                rebalance_configuration_uid=uuid.UUID(str(rebalance_configuration_uid))
            )
        )
    outcomes = _together(*reads)
    _raise_first(outcomes[: 2 if bars_configuration_uid is not None else 1])
    answers = iter(outcomes[1:])
    named = next(answers) if bars_configuration_uid is not None else None
    replayed = next(answers) if replay_read else None
    named_rebalance = next(answers) if rebalance_configuration_uid is not None else None

    if profile is None:
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
    if key is not None:
        if not replay_read:
            replayed = _portfolio_created_with_key(account_uid, key, owner=owner)
        _raise_first([replayed])
        if replayed is not None:
            return replayed
    _raise_first([named_rebalance])

    configuration_uid = new_markets_uid()
    rebalance_uid = named_rebalance.uid if named_rebalance is not None else new_markets_uid()
    signal_key = legacy_signal_key(configuration_uid)
    configuration_values = portfolio_configuration_values(
        uid=configuration_uid,
        name=name,
        description=description,
        account_uid=account_uid,
        signal_definition_uid=None,
        owner_gateway_uid=owner.gateway_uid,
        owner_user_uid=owner.user_uid,
        rebalance_configuration_uid=rebalance_uid,
        upsample_frequency_id=upsample_frequency_id,
        intraday_bar_interpolation_rule=intraday_bar_interpolation_rule,
        valuation_column=valuation_column,
        valuation_maximum_staleness_seconds=valuation_maximum_staleness_seconds,
        fail_on_missing_prices=fail_on_missing_prices,
        commission_fee=commission_fee,
        scheduled=scheduled,
    )
    own_bars_values = portfolio_bar_configuration_values(
        portfolio_configuration_uid=configuration_uid,
        name=profile.get("name") or (configuration_values["name"] + OWN_BARS_SUFFIX)[:255],
        description=profile.get("description"),
        frequency_id=profile.get("frequency_id") or "1d",
        feed=profile.get("feed") or "sip",
        adjustment=profile.get("adjustment") or "all",
        enabled=profile.get("enabled", True),
        scheduled=profile.get("scheduled", True),
    )

    definition: SignalDefinition | None = None
    portfolio_row: Any = None
    rebalance: Any = None
    try:
        # 2. Definition. The signal keeps the configuration UID as its key; the definition takes
        # the caller's key when one is given (issue #43), which was read in step 1 or is new.
        writes: list[Callable[[], Any]] = [
            lambda: create_signal_definition(
                account_uid=account_uid,
                key=key if key is not None else signal_key,
                name=configuration_values["name"],
                description=description,
                signal_key=signal_key,
                key_checked=True,
            )
        ]
        if rebalance_configuration is not None:
            writes.append(
                lambda: create_rebalance_configuration(
                    **dict(rebalance_configuration),
                    uid=rebalance_uid,
                    portfolio_configuration_uid=configuration_uid,
                )
            )
        outcomes = _together(*writes)
        definition = _kept(outcomes[0])
        if rebalance_configuration is not None:
            rebalance = _kept(outcomes[1])
        _raise_first(outcomes)
        rule = named_rebalance if named_rebalance is not None else rebalance

        # 3. Publish and `Portfolio`. The first rebalance is read from the calendar alongside them,
        # and a failed read fails the create like any other step (issue #48).
        outcomes = _together(
            lambda: _publish_validated(
                definition.signal_key,
                normalized,
                assets,
                description=description,
                metadata_description=_signal_description(definition),
                new_signal=True,
            ),
            lambda: _upsert_portfolio_row(configuration_uid, rule),
            lambda: rebalance_schedule.first_rebalance_at(rule, starts_at=next(iter(normalized))),
        )
        published, portfolio_row, first_rebalance = (_kept(outcome) for outcome in outcomes)
        _raise_first(outcomes)

        # 4. Configuration, with its own bars configuration, which names it.
        configuration_values.update(
            signal_definition_uid=definition.uid,
            portfolio_uid=uuid.UUID(str(portfolio_row.uid)),
        )
        OwnTableWrites().insert(AlpacaPortfolioConfigurationTable, [configuration_values]).insert(
            AlpacaBarsConfigurationTable, [own_bars_values]
        ).run()
    except Exception:
        _discard_failed_creation(definition, portfolio_row, rebalance)
        if key is not None and definition is None:
            from alpaca_connectors.portfolios.signal_definitions import account_signal_definition

            # Two creates with one key: the one that lost answers the other's portfolio.
            if account_signal_definition(account_uid, key) is not None:
                existing = _portfolio_created_with_key(account_uid, key, owner=owner)
                if existing is not None:
                    return existing
        raise
    own_bars = stored_portfolio_bar_configuration(own_bars_values)
    return ProvidedPortfolioCreation(
        configuration=AlpacaPortfolioConfiguration.model_validate(configuration_values),
        published=published,
        portfolio_uid=str(portfolio_row.uid),
        portfolio_unique_identifier=str(portfolio_row.unique_identifier),
        bars_configuration_created=True,
        bars_configuration_uid=str(own_bars.uid),
        signal_definition=definition,
        rebalance_configuration_created=rebalance is not None,
        rebalance_configuration=rule,
        bars_configuration=own_bars,
        first_rebalance_at=first_rebalance,
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
    "end_portfolio_configurations",
    "ended_portfolio_account_uid",
    "give_portfolio_weights",
    "is_owner",
    "normalize_observations",
    "portfolio_signal_key",
    "portfolio_signal_uid",
    "configuration_uid_from_portfolio_identifier",
    "provided_portfolio_unique_identifier",
    "provided_signal_uid",
    "publish_definition_weights",
    "publish_provided_weights",
    "require_alpaca_assets",
    "require_portfolio_owner",
]
