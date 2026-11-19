"""Deleting an account deletes everything that belongs to it (ADR 0028, decision 8).

ms-markets' rules stay as they are; the order of this connector's calls is the one their keys
impose: the account's history before its ``Portfolio`` rows, a ``Portfolio`` before the signal it
follows.

1. Refused before anything changes when ms-markets' account delete preflight reports a blocker,
   or when another account's current target holds one of the account's portfolios (ms-markets
   ADR 0049: retiring is refused while any current target names the portfolio).
2. The account's own current target is cleared, since it also names its portfolios.
3. Each portfolio ends as every portfolio does (ADR 0027): its ``Portfolio`` is retired and
   recorded under the account, and the configuration goes with its own bars configuration. A
   portfolio not yet adopted that a ``portfolios`` feed of the account prices runs with the
   account's keys, so it is the account's too and ends with it.
   A signal definition whose signal no retired ``Portfolio`` follows has its signal deleted now.
4. The account's orders and trades in this application's instances are deleted: their
   ``RESTRICT`` keys would refuse the next step.
5. ``Account.delete`` deletes the account with its whole history in one transaction;
   ``ProviderAccountDetails``, the account's bars feeds and its signal definitions go with it.
   A registration made before revision 0018 still names the Main Sequence Secrets its keys were
   in; those this application created are deleted next, as removing the keys deletes them.
6. Each retired ``Portfolio`` of the account that no other account's history names is deleted,
   its series in this application's instances first. One another account's history names stays
   retired, recorded.
7. Each signal no ``Portfolio`` follows any more is deleted.

Every step can run again: a retry after a partial failure finds the records of step 3 and
finishes steps 6 and 7.

Each step reads and writes set-based (issue #46): the portfolios end together
(``end_portfolio_configurations``), the retired ``Portfolio`` rows go through one preflight and
one bulk delete of ms-markets, their series and records through one statement per table, and the
signals through one pass. ms-markets has only one-portfolio routes to list the accounts whose
current target names a portfolio and to retire one, and only one-signal routes to unbind and
delete a signal, so those calls stay one per portfolio or signal.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from typing import Any

UTC = dt.timezone.utc


class AccountDeleteRefused(Exception):
    """The account cannot be deleted now; nothing changed. Never names another account."""

    def __init__(self, blockers: list[str]) -> None:
        super().__init__("The account cannot be deleted: " + "; ".join(blockers))
        self.blockers = blockers


@dataclass(frozen=True, slots=True)
class AccountDeletePreflight:
    account_uid: str
    blockers: list[str]
    warnings: list[str]
    portfolio_count: int
    signal_definition_count: int
    bars_feed_count: int
    ended_portfolio_count: int

    @property
    def allowed(self) -> bool:
        return not self.blockers

    def summary(self) -> dict[str, Any]:
        return {
            "account_uid": self.account_uid,
            "allowed": self.allowed,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
            "portfolio_count": self.portfolio_count,
            "signal_definition_count": self.signal_definition_count,
            "bars_feed_count": self.bars_feed_count,
            "ended_portfolio_count": self.ended_portfolio_count,
        }


@dataclass(slots=True)
class AccountDeletion:
    account_uid: str
    ended_portfolio_count: int = 0
    deleted_portfolio_uids: list[str] = field(default_factory=list)
    kept_portfolio_uids: list[str] = field(default_factory=list)
    deleted_signal_uids: list[str] = field(default_factory=list)
    kept_signal_uids: list[str] = field(default_factory=list)
    markets: dict[str, Any] | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "account_uid": self.account_uid,
            "deleted": True,
            "ended_portfolio_count": self.ended_portfolio_count,
            "deleted_portfolio_uids": list(self.deleted_portfolio_uids),
            "kept_portfolio_uids": list(self.kept_portfolio_uids),
            "deleted_signal_uids": list(self.deleted_signal_uids),
            "kept_signal_uids": list(self.kept_signal_uids),
            "markets": self.markets,
        }


def _delete_instance_rows(model: Any, column: str, values: list[str]) -> None:
    """Delete this application's instance rows whose ``column`` is one of ``values``."""
    from msm.bootstrap import resolve_runtime
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import delete

    if not values:
        return
    runtime = resolve_runtime(models=[model], row_model_name="alpaca_connectors.account_deletion")
    operation = compile_markets_statement(
        delete(model).where(getattr(model, column).in_(values)),
        context=runtime.context,
        operation="delete",
    )
    execute_markets_operation(operation, context=runtime.context)


def delete_account_orders_and_trades(account_identifier: str) -> None:
    """Delete the account's orders and trades from this application's instances (ADR 0016)."""
    from alpaca_connectors.instances import AlpacaOrdersStorage, AlpacaTradesStorage

    for model in (AlpacaTradesStorage, AlpacaOrdersStorage):
        _delete_instance_rows(model, "account_identifier", [account_identifier])


def delete_portfolio_series(portfolio_identifiers: list[str]) -> None:
    """Delete portfolios' values, weights and rebalance state from this application's instances.

    They reference the ``Portfolio`` with ``RESTRICT``, so they go before it: one statement per
    table for all the portfolios (issue #46).
    """
    from alpaca_connectors.instances import (
        AlpacaPortfolioRebalanceStateStorage,
        AlpacaPortfoliosStorage,
        AlpacaPortfolioWeightsStorage,
    )
    from alpaca_connectors.markets_api import write_in_batches

    for model in (
        AlpacaPortfolioRebalanceStateStorage,
        AlpacaPortfolioWeightsStorage,
        AlpacaPortfoliosStorage,
    ):
        write_in_batches(
            lambda batch, model=model: _delete_instance_rows(model, "portfolio_identifier", batch),
            portfolio_identifiers,
        )


def _account_definitions(account_uid: str) -> list[Any]:
    from alpaca_connectors.markets_api import MARKETS_BATCH_SIZE
    from alpaca_connectors.portfolios.signal_definitions import list_signal_definitions

    rows: list[Any] = []
    while True:
        page, total = list_signal_definitions(
            account_uids=[account_uid], limit=MARKETS_BATCH_SIZE, offset=len(rows)
        )
        rows.extend(page)
        if not page or len(rows) >= total:
            return rows


def _account_portfolios(account_uid: str, feeds: list[Any]) -> list[Any]:
    """The account's portfolios, and those not yet adopted that its ``portfolios`` feeds price.

    ``feeds`` are the account's bars feeds; the portfolios naming them are read in one request.
    """
    from alpaca_connectors.portfolios.configurations import (
        portfolio_configurations_naming_any_bars,
        portfolio_configurations_of_account,
    )

    rows = {str(row.uid): row for row in portfolio_configurations_of_account(account_uid)}
    priced = [feed.uid for feed in feeds if feed.asset_source == "portfolios"]
    if priced:
        for row in portfolio_configurations_naming_any_bars(priced):
            rows.setdefault(str(row.uid), row)
    return list(rows.values())


@dataclass(frozen=True, slots=True)
class _AccountContents:
    """What the preflight read, which the delete then uses instead of reading it again."""

    preflight: AccountDeletePreflight
    portfolios: list[Any]
    definitions: list[Any]


def _preflight(normalized: str) -> _AccountContents:
    from alpaca_connectors.market_data.configurations import bar_configurations_for_account
    from alpaca_connectors.markets_api import account_delete_preflight, current_target_account_uids
    from alpaca_connectors.portfolios.configurations import ended_portfolios_of_account

    markets = account_delete_preflight(normalized) or {}
    blockers = [str(blocker) for blocker in markets.get("blockers") or []]
    warnings = [str(warning) for warning in markets.get("warnings") or []]
    feeds = bar_configurations_for_account(normalized)
    portfolios = _account_portfolios(normalized, feeds)
    # ms-markets lists a portfolio's account targets one portfolio at a time
    # (``listPortfolioAccountTargets``); it has no form for several portfolios.
    held = sum(
        1
        for configuration in portfolios
        if configuration.portfolio_uid is not None
        and set(current_target_account_uids(configuration.portfolio_uid)) - {normalized}
    )
    if held:
        # ms-markets' rule (its ADR 0049): never names the other accounts (ADR 0019).
        blockers.append(
            f"{held} of the account's portfolios are held by another account's current target; "
            "that account's owner must change its target first."
        )
    definitions = _account_definitions(normalized)
    return _AccountContents(
        preflight=AccountDeletePreflight(
            account_uid=normalized,
            blockers=blockers,
            warnings=warnings,
            portfolio_count=len(portfolios),
            signal_definition_count=len(definitions),
            bars_feed_count=len(feeds),
            ended_portfolio_count=len(ended_portfolios_of_account(normalized)),
        ),
        portfolios=portfolios,
        definitions=definitions,
    )


def preflight_account_delete(account_uid: Any) -> AccountDeletePreflight:
    """What deleting the account would delete and what refuses it; nothing changes."""
    return _preflight(str(uuid.UUID(str(account_uid)))).preflight


def _clear_own_target(account_uid: str) -> None:
    from alpaca_connectors.markets_api import account_target_positions, clear_account_target

    target = account_target_positions(account_uid)
    if getattr(target, "positions", None):
        clear_account_target(account_uid, at=dt.datetime.now(UTC).replace(microsecond=0))


def _finish_portfolios(account_uid: str, deletion: AccountDeletion) -> list[str]:
    """Step 6: delete the recorded ``Portfolio`` rows nothing else names; answer their signals.

    One ms-markets preflight tells which are referenced; the others' series go in one statement
    per table, then one bulk delete of the `Portfolio` rows, then one delete of their records.
    """
    from alpaca_connectors.markets_api import delete_portfolios, portfolios_delete_blockers
    from alpaca_connectors.portfolios.configurations import (
        ended_portfolios_of_account,
        forget_ended_portfolios,
    )
    from alpaca_connectors.portfolios.provided_weights import (
        provided_portfolio_unique_identifier,
    )

    records = ended_portfolios_of_account(account_uid)
    blockers = portfolios_delete_blockers([record.portfolio_uid for record in records])
    free = []
    for record in records:
        portfolio_uid = str(record.portfolio_uid)
        if blockers[str(uuid.UUID(portfolio_uid))]:
            deletion.kept_portfolio_uids.append(portfolio_uid)
        else:
            free.append(record)
    if free:
        delete_portfolio_series(
            [provided_portfolio_unique_identifier(r.portfolio_configuration_uid) for r in free]
        )
        delete_portfolios([str(record.portfolio_uid) for record in free])
        forget_ended_portfolios(free)
        deletion.deleted_portfolio_uids.extend(str(record.portfolio_uid) for record in free)
    return [record.signal_uid for record in records]


def _finish_signals(signal_uids: list[str], deletion: AccountDeletion) -> None:
    """Step 7: delete the signals no retired ``Portfolio`` kept for history follows, in one pass."""
    from alpaca_connectors.portfolios.signal_definitions import delete_signals_rows

    for signal_uid, state in delete_signals_rows(sorted(set(signal_uids))).items():
        if state == "deleted":
            deletion.deleted_signal_uids.append(signal_uid)
        else:
            deletion.kept_signal_uids.append(signal_uid)


def delete_account(account_uid: Any) -> AccountDeletion:
    """Delete the account and everything that belongs to it, in the order ms-markets allows.

    The caller has checked the account's owner. Raises ``AccountDeleteRefused`` before anything
    changes when the preflight finds a blocker.
    """
    from msm.api.remote import MarketsConflictError

    from alpaca_connectors.account.services import get_account_registration
    from alpaca_connectors.markets_api import delete_account as delete_markets_account
    from alpaca_connectors.portfolios.provided_weights import end_portfolio_configurations
    from alpaca_connectors.portfolios.signal_definitions import (
        definition_signal_uid,
        delete_signals_rows,
    )
    from alpaca_connectors.runtime import account_runtime_models, start_markets_engine

    normalized = str(uuid.UUID(str(account_uid)))
    start_markets_engine(models=account_runtime_models())
    registration = get_account_registration(normalized)
    deletion = AccountDeletion(account_uid=normalized)
    if registration is not None:
        contents = _preflight(normalized)
        if not contents.preflight.allowed:
            raise AccountDeleteRefused(contents.preflight.blockers)
        _clear_own_target(normalized)
        removals = end_portfolio_configurations(contents.portfolios)
        deletion.ended_portfolio_count += len(removals)
        ended_definitions: set[str] = set()
        for removal in removals:
            # Its definition went with it, and its signal too when nothing kept it (issue #38).
            if removal.signal_definition_state == "deleted":
                ended_definitions.add(str(removal.signal_definition_uid))
            if removal.signal is not None and removal.signal.get("state") == "deleted":
                signal_uid = str(removal.signal["uid"])
                if signal_uid not in deletion.deleted_signal_uids:
                    deletion.deleted_signal_uids.append(signal_uid)
        # The account's other definitions, as the preflight read them: their signals' rows go
        # now unless a retired Portfolio follows them; the definitions go with the account.
        remaining = [
            definition_signal_uid(definition)
            for definition in contents.definitions
            if str(definition.uid) not in ended_definitions
        ]
        for signal_uid, state in delete_signals_rows(remaining).items():
            if state == "deleted" and signal_uid not in deletion.deleted_signal_uids:
                deletion.deleted_signal_uids.append(signal_uid)
        delete_account_orders_and_trades(str(registration["account_unique_identifier"]))
        try:
            deletion.markets = delete_markets_account(normalized)
        except MarketsConflictError as exc:
            raise AccountDeleteRefused([str(exc)]) from exc
    # Steps 6 and 7 also finish what an earlier, interrupted attempt left.
    signals = _finish_portfolios(normalized, deletion)
    _finish_signals(signals, deletion)
    return deletion


__all__ = [
    "AccountDeletePreflight",
    "AccountDeleteRefused",
    "AccountDeletion",
    "delete_account",
    "delete_account_orders_and_trades",
    "delete_portfolio_series",
    "preflight_account_delete",
]
