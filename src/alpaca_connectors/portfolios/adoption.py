"""The ``portfolio adopt`` rollout: every existing portfolio gets one owning account (ADR 0028).

Revision 0029 adopts, in the database, every portfolio its own owner can own. This command does
the same for a portfolio written after it, by the release that served during the rollout, and
ends the rest, which retires their ``Portfolio`` through Markets and so cannot run in a revision
(decision 9):

- **Adopt.** A portfolio whose bars configuration's account its owner owns belongs to that
  account. It gets a signal definition on the account that keeps its signal (the key is its
  configuration UID, as before) and a bars configuration of its own with the profile of the one
  it named.
- **End.** A portfolio whose bars run on an account without an owner, or on another owner's
  account, cannot be given one owning account and ends as every portfolio does (ADR 0027). One
  that an account's current target names is refused, changed nothing, and is reported.
- **Feeds.** A ``portfolios`` bars configuration that no portfolio names any more is deleted;
  every other one stays as its account's feed.

Without ``execute`` it only reports what it would do.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class AdoptionReport:
    execute: bool
    adopt: list[str] = field(default_factory=list)
    end: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    deleted_feeds: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "execute": self.execute,
            "adopt": list(self.adopt),
            "end": list(self.end),
            "refused": list(self.refused),
            "deleted_portfolios_feeds": list(self.deleted_feeds),
        }


def _adoptable_account(configuration: Any, bars: Any) -> Any | None:
    """The account the portfolio can belong to: its bars' account, when its owner owns it."""
    from alpaca_connectors.account.ownership import AccountOwner, registered_owner
    from alpaca_connectors.account.services import get_account_registration

    if bars is None or bars.account_uid is None:
        return None
    owner = registered_owner(get_account_registration(str(bars.account_uid)))
    portfolio_owner = AccountOwner(
        gateway_uid=configuration.owner_gateway_uid, user_uid=configuration.owner_user_uid
    )
    return bars.account_uid if owner == portfolio_owner else None


def adopt_portfolio(configuration: Any, bars: Any, account_uid: Any) -> None:
    """Give the portfolio its account, a definition that keeps its signal, and its own bars.

    Each step finds what an interrupted earlier attempt already made, so a retry is safe.
    """
    from alpaca_connectors.market_data.configurations import (
        create_portfolio_bar_configuration,
        portfolio_bar_configuration,
    )
    from alpaca_connectors.portfolios.configurations import update_portfolio_configuration_row
    from alpaca_connectors.portfolios.provided_weights import OWN_BARS_SUFFIX
    from alpaca_connectors.portfolios.signal_definitions import (
        account_signal_definition,
        create_signal_definition,
        legacy_signal_key,
    )

    key = legacy_signal_key(configuration.uid)
    definition = account_signal_definition(account_uid, key) or create_signal_definition(
        account_uid=account_uid,
        key=key,
        name=configuration.name,
        signal_key=key,
    )
    if portfolio_bar_configuration(configuration.uid) is None:
        create_portfolio_bar_configuration(
            portfolio_configuration_uid=configuration.uid,
            name=(configuration.name + OWN_BARS_SUFFIX)[:255],
            description=bars.description,
            frequency_id=bars.frequency_id,
            feed=bars.feed,
            adjustment=bars.adjustment,
            enabled=bars.enabled,
            scheduled=bars.scheduled,
        )
    update_portfolio_configuration_row(
        configuration.uid,
        values={
            "account_uid": account_uid,
            "signal_definition_uid": definition.uid,
            "bars_configuration_uid": None,
        },
    )


def _delete_unnamed_portfolios_feeds(report: AdoptionReport) -> None:
    from alpaca_connectors.market_data.configurations import (
        delete_bar_configuration,
        list_bar_configurations,
    )
    from alpaca_connectors.markets_api import MARKETS_BATCH_SIZE
    from alpaca_connectors.portfolios.configurations import unadopted_portfolio_configurations

    # After a run, what is still unadopted (refused) still names its feed; a dry run counts
    # every portfolio it would adopt or end as gone.
    remaining = unadopted_portfolio_configurations() if report.execute else []
    named = {
        str(configuration.bars_configuration_uid)
        for configuration in remaining
        if configuration.bars_configuration_uid is not None
    }
    feeds: list[Any] = []
    while True:
        page, total = list_bar_configurations(
            limit=MARKETS_BATCH_SIZE,
            offset=len(feeds),
            asset_source="portfolios",
            portfolio_owned=False,
        )
        feeds.extend(page)
        if not page or len(feeds) >= total:
            break
    for feed in feeds:
        if str(feed.uid) in named:
            continue
        report.deleted_feeds.append(str(feed.uid))
        if report.execute:
            delete_bar_configuration(feed.uid)


def run_adoption(*, execute: bool) -> AdoptionReport:
    """Adopt or end every portfolio not yet adopted; without ``execute``, only report."""
    from alpaca_connectors.market_data.configurations import get_bar_configuration
    from alpaca_connectors.portfolios.configurations import unadopted_portfolio_configurations
    from alpaca_connectors.portfolios.provided_weights import (
        ProvidedWeightsError,
        end_portfolio_configuration,
    )

    report = AdoptionReport(execute=execute)
    for configuration in unadopted_portfolio_configurations():
        bars = (
            get_bar_configuration(configuration.bars_configuration_uid)
            if configuration.bars_configuration_uid is not None
            else None
        )
        account_uid = _adoptable_account(configuration, bars)
        uid = str(configuration.uid)
        if account_uid is not None:
            report.adopt.append(uid)
            if execute:
                adopt_portfolio(configuration, bars, account_uid)
            continue
        if not execute:
            report.end.append(uid)
            continue
        try:
            end_portfolio_configuration(configuration)
        except ProvidedWeightsError as exc:
            if exc.code != "portfolio_in_account_target":
                raise
            report.refused.append(uid)
        else:
            report.end.append(uid)
    _delete_unnamed_portfolios_feeds(report)
    return report


__all__ = ["AdoptionReport", "adopt_portfolio", "run_adoption"]
