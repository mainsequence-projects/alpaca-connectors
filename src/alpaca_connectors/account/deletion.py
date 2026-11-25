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
   account's keys, so it is the account's too and ends with it. The account's orders and trades
   in this application's instances go in the same transaction: their ``RESTRICT`` keys would
   refuse step 4. A signal definition whose signal no retired ``Portfolio`` follows has its
   signal deleted now.
4. ``Account.delete`` deletes the account with its whole history in one transaction;
   ``ProviderAccountDetails``, the account's bars feeds and its signal definitions go with it.
   A registration made before revision 0018 still names the Main Sequence Secrets its keys were
   in; those this application created are deleted next, as removing the keys deletes them.
5. Each retired ``Portfolio`` of the account that no other account's history names is deleted,
   its ledger and rebalance state in this application's instances first
   (``delete_private_series``); Markets
   deletes its weights and values with it (ADR 0031). One another account's history names stays
   retired, recorded.
6. Each signal no ``Portfolio`` follows any more is deleted, with the records of the deleted
   ``Portfolio`` rows.

Every step can run again: a retry after a partial failure finds the records of step 3 and
finishes steps 5 and 6.

The delete works in bulk (issue #46):

- **Reads in one step.** The preflight's independent reads run together
  (``markets_api.all_together``); the delete reuses what they read, and the registration its
  caller read for the owner check.
- **Writes in one transaction each.** This application's own writes go as MetaTables statement
  lists (``OwnTableWrites``, MetaTables ADR 0026): ending the portfolios with the orders and
  trades is one transaction, and forgetting the deleted ``Portfolio`` rows' records with their
  signals' asset rows is another.
- **One Markets call per kind.** The portfolios are retired in one call, the retired
  ``Portfolio`` rows go through one preflight and one bulk delete, and the signals end in one
  call (ms-markets 5.3.0, MainSequenceMarkets#124, #126, #127). Listing the accounts whose
  current target names a portfolio stays one call per portfolio, made together
  (``markets_api.each_together``; #125 points to the account delete's own
  ``delete_portfolios`` option instead).
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace
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


def delete_portfolio_series(portfolio_identifiers: list[str]) -> None:
    """Delete portfolios' private rows: their ledger and rebalance state in this application's
    instances.

    Their weights and values are Markets' own rows and go with the ``Portfolio`` (ADR 0031,
    decision 7). The ledger and the rebalance state reference the ``Portfolio`` with
    ``RESTRICT``, so they go before it, the ledger first, through the ms-markets recipe
    ``delete_private_series``: one call per ``MARKETS_BATCH_SIZE`` portfolios (issue #46).
    """
    from alpaca_connectors.instances import (
        AlpacaPortfolioEventLedgerStorage,
        AlpacaPortfolioRebalanceStateStorage,
    )
    from alpaca_connectors.markets_api import delete_private_series

    delete_private_series(
        tables=[AlpacaPortfolioEventLedgerStorage, AlpacaPortfolioRebalanceStateStorage],
        portfolio_identifiers=portfolio_identifiers,
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


def _with_priced_portfolios(own: list[Any], feeds: list[Any]) -> list[Any]:
    """The account's portfolios, and those not yet adopted that its ``portfolios`` feeds price.

    ``own`` are the account's configurations and ``feeds`` its bars feeds; the portfolios naming
    the feeds are read in one request, only when there is such a feed.
    """
    from alpaca_connectors.portfolios.configurations import (
        portfolio_configurations_naming_any_bars,
    )

    rows = {str(row.uid): row for row in own}
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
    # The account's records of portfolios ended before this delete (ADR 0028).
    ended: list[Any]
    # The account's current target and its Markets ``Order`` uids, read only for a delete.
    target: Any = None
    order_uids: list[Any] = field(default_factory=list)


def _preflight(
    normalized: str, *, for_delete: bool = False, account_identifier: str | None = None
) -> _AccountContents:
    """Read everything the delete needs, the independent reads in one step.

    Markets' delete preflight, the bars feeds, the account's portfolio configurations, its
    signal definitions, its ended portfolios and, for a delete, its current target and its
    orders (``account_identifier``) need only the account, so they run together
    (``all_together``). Then the portfolios its ``portfolios``
    feeds price, when it has one, and the accounts whose current target names each portfolio.
    """
    from alpaca_connectors.market_data.configurations import bar_configurations_for_account
    from alpaca_connectors.markets_api import (
        account_delete_preflight,
        account_order_uids,
        account_target_positions,
        all_together,
        current_target_account_uids,
        each_together,
    )
    from alpaca_connectors.portfolios.configurations import (
        ended_portfolios_of_account,
        portfolio_configurations_of_account,
    )

    reads = [
        lambda: account_delete_preflight(normalized) or {},
        lambda: bar_configurations_for_account(normalized),
        lambda: portfolio_configurations_of_account(normalized),
        lambda: _account_definitions(normalized),
        lambda: ended_portfolios_of_account(normalized),
    ]
    if for_delete:
        reads.append(lambda: account_target_positions(normalized))
        reads.append(lambda: account_order_uids(account_identifier) if account_identifier else [])
    markets, feeds, own, definitions, ended, *for_delete_reads = all_together(*reads)
    target, order_uids = for_delete_reads or (None, [])
    blockers = [str(blocker) for blocker in markets.get("blockers") or []]
    warnings = [str(warning) for warning in markets.get("warnings") or []]
    portfolios = _with_priced_portfolios(own, feeds)
    # ms-markets lists a portfolio's account targets one portfolio at a time
    # (``listPortfolioAccountTargets``); it has no form for several portfolios
    # (MainSequenceMarkets#125), so the lists are read together.
    targeted = [row.portfolio_uid for row in portfolios if row.portfolio_uid is not None]
    held = sum(
        1
        for accounts in each_together(current_target_account_uids, targeted)
        if set(accounts) - {normalized}
    )
    if held:
        # ms-markets' rule (its ADR 0049): never names the other accounts (ADR 0019).
        blockers.append(
            f"{held} of the account's portfolios are held by another account's current target; "
            "that account's owner must change its target first."
        )
    return _AccountContents(
        preflight=AccountDeletePreflight(
            account_uid=normalized,
            blockers=blockers,
            warnings=warnings,
            portfolio_count=len(portfolios),
            signal_definition_count=len(definitions),
            bars_feed_count=len(feeds),
            ended_portfolio_count=len(ended),
        ),
        portfolios=portfolios,
        definitions=definitions,
        ended=ended,
        target=target,
        order_uids=order_uids,
    )


def preflight_account_delete(account_uid: Any) -> AccountDeletePreflight:
    """What deleting the account would delete and what refuses it; nothing changes."""
    return _preflight(str(uuid.UUID(str(account_uid)))).preflight


def _clear_own_target(account_uid: str, target: Any) -> None:
    """Clear the account's current target, as the preflight read it, when it names positions."""
    from alpaca_connectors.markets_api import clear_account_target

    if getattr(target, "positions", None):
        clear_account_target(account_uid, at=dt.datetime.now(UTC).replace(microsecond=0))


def _known_records(stored: list[Any], removals: list[Any]) -> list[Any]:
    """The account's records: those stored before the delete and the ones it just wrote.

    Ending the portfolios recorded each retired ``Portfolio`` in its transaction; the delete
    knows those records already and does not read them back. A portfolio recorded before keeps
    its stored record.
    """
    records = {str(uuid.UUID(str(record.portfolio_uid))): record for record in stored}
    for removal in removals:
        if removal.recorded_signal_uid is None or removal.portfolio_uid is None:
            continue
        records.setdefault(
            str(uuid.UUID(removal.portfolio_uid)),
            SimpleNamespace(
                portfolio_uid=uuid.UUID(removal.portfolio_uid),
                portfolio_configuration_uid=uuid.UUID(removal.configuration_uid),
                signal_uid=removal.recorded_signal_uid,
            ),
        )
    return list(records.values())


def _finish(account_uid: str, deletion: AccountDeletion, records: list[Any] | None) -> None:
    """Steps 5 and 6: delete the recorded ``Portfolio`` rows nothing else names, then the
    signals no ``Portfolio`` follows any more.

    ``records`` are the account's records when the delete knows them; a retry, with the account
    gone, reads them. Then, in one step, ms-markets' preflight of the ``Portfolio`` rows and the
    read of which of their signals another record still follows. Then the free ``Portfolio``
    rows: their rebalance state (one ``delete_private_series`` call) and one bulk delete, which
    takes their weights and values; one Markets reports in ``failed`` stays, recorded, with its
    signal. Then one transaction forgets the deleted ones' records and deletes the free signals'
    asset rows, and Markets ends those signals in one call.
    """
    from alpaca_connectors.markets_api import (
        all_together,
        delete_portfolios,
        end_signals,
        portfolios_delete_blockers,
    )
    from alpaca_connectors.portfolios.configurations import (
        EndedPortfolioTable,
        OwnTableWrites,
        ended_portfolios_of_account,
        ended_signal_uids,
    )
    from alpaca_connectors.portfolios.provided_weights import (
        provided_portfolio_unique_identifier,
    )
    from alpaca_connectors.portfolios.signal_assets import SignalAssetTable

    if records is None:
        records = ended_portfolios_of_account(account_uid)
    if not records:
        return
    portfolio_uids = [str(uuid.UUID(str(record.portfolio_uid))) for record in records]
    signal_uids = list(dict.fromkeys(str(record.signal_uid) for record in records))
    blockers, followed_elsewhere = all_together(
        lambda: portfolios_delete_blockers(portfolio_uids),
        lambda: ended_signal_uids(signal_uids, excluding_portfolio_uids=portfolio_uids),
    )
    free = [
        record for record, uid in zip(records, portfolio_uids, strict=True) if not blockers[uid]
    ]
    free_uids: set[str] = set()
    if free:
        delete_portfolio_series(
            [provided_portfolio_unique_identifier(r.portfolio_configuration_uid) for r in free]
        )
        # Markets deletes what may go and reports the rest (MainSequenceMarkets#127): only those
        # it deleted lose their record and free their signal.
        free_uids = set(delete_portfolios([str(record.portfolio_uid) for record in free]).deleted)
    deletion.deleted_portfolio_uids.extend(uid for uid in portfolio_uids if uid in free_uids)
    deletion.kept_portfolio_uids.extend(uid for uid in portfolio_uids if uid not in free_uids)
    # A signal stays while a Portfolio that is not deleted, recorded here or elsewhere, follows it.
    kept = set(followed_elsewhere) | {
        str(record.signal_uid)
        for record, uid in zip(records, portfolio_uids, strict=True)
        if uid not in free_uids
    }
    free_signals = [uid for uid in signal_uids if uid not in kept]
    writes = OwnTableWrites()
    writes.delete_in(EndedPortfolioTable, "portfolio_uid", [uuid.UUID(uid) for uid in free_uids])
    writes.delete_in(SignalAssetTable, "signal_uid", free_signals)
    writes.run()
    if free_signals:
        end_signals(free_signals)
    for signal_uid in signal_uids:
        target = deletion.kept_signal_uids if signal_uid in kept else deletion.deleted_signal_uids
        if signal_uid not in target:
            target.append(signal_uid)


_UNREAD = object()


def delete_account(account_uid: Any, *, registration: Any = _UNREAD) -> AccountDeletion:
    """Delete the account and everything that belongs to it, in the order ms-markets allows.

    The caller has checked the account's owner; ``registration`` is the registration it read for
    that check, which is not read again. Raises ``AccountDeleteRefused`` before anything changes
    when the preflight finds a blocker.
    """
    from msm.api.remote import MarketsConflictError

    from alpaca_connectors.account.services import get_account_registration
    from alpaca_connectors.instances import (
        AlpacaOrderEventsStorage,
        AlpacaOrdersStorage,
        AlpacaTradesStorage,
    )
    from alpaca_connectors.markets_api import delete_account as delete_markets_account
    from alpaca_connectors.portfolios.configurations import OwnTableWrites
    from alpaca_connectors.portfolios.provided_weights import end_portfolio_configurations
    from alpaca_connectors.portfolios.signal_definitions import (
        definition_signal_uid,
        delete_signals_rows,
    )
    from alpaca_connectors.runtime import account_runtime_models, start_markets_engine

    normalized = str(uuid.UUID(str(account_uid)))
    start_markets_engine(models=account_runtime_models())
    if registration is _UNREAD:
        registration = get_account_registration(normalized)
    deletion = AccountDeletion(account_uid=normalized)
    records: list[Any] | None = None
    if registration is not None:
        identifier = str(registration["account_unique_identifier"])
        contents = _preflight(normalized, for_delete=True, account_identifier=identifier)
        if not contents.preflight.allowed:
            raise AccountDeleteRefused(contents.preflight.blockers)
        _clear_own_target(normalized, contents.target)
        # The account's trades, and its orders' states and events, found by their Markets
        # ``Order`` uids (ms-markets ADR 0064): their RESTRICT keys would refuse Markets' account
        # delete, so they go in the transaction that ends its portfolios.
        own = OwnTableWrites()
        own.delete_in(AlpacaTradesStorage, "account_identifier", [identifier])
        own.delete_in(AlpacaOrderEventsStorage, "order_uid", contents.order_uids)
        own.delete_in(AlpacaOrdersStorage, "order_uid", contents.order_uids)
        removals = []
        if contents.portfolios:
            removals = end_portfolio_configurations(
                contents.portfolios, definitions=contents.definitions, writes=own
            )
        else:
            own.run()
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
        try:
            deletion.markets = delete_markets_account(normalized)
        except MarketsConflictError as exc:
            raise AccountDeleteRefused([str(exc)]) from exc
        records = _known_records(contents.ended, removals)
    # Steps 5 and 6 also finish what an earlier, interrupted attempt left.
    _finish(normalized, deletion, records)
    return deletion


__all__ = [
    "AccountDeletePreflight",
    "AccountDeleteRefused",
    "AccountDeletion",
    "delete_account",
    "delete_portfolio_series",
    "preflight_account_delete",
]
