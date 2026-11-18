"""The ``portfolio adopt`` command: give existing portfolios their owning account (ADR 0028).

Revision 0029 adopts, in the database, every portfolio its own owner can own. This command does
the same for a portfolio written after it. It never ends a portfolio and never deletes a bars
configuration (ADR 0028, as amended 2026-11-18):

- **Adopt.** A portfolio whose bars configuration's account its owner owns belongs to that
  account. It gets a signal definition on the account that keeps its signal (the key is its
  configuration UID, as before) and a bars configuration of its own with the profile of the one
  it named.
- **Leave.** A portfolio whose bars run on an account without an owner, or on another owner's
  account, stays as it is. It keeps working as before ADR 0028: it follows its own signal and runs
  on the feed it names, which keeps pricing it.

Without ``execute`` it only reports what it would do.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class AdoptionReport:
    execute: bool
    adopt: list[str] = field(default_factory=list)
    left: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {"execute": self.execute, "adopt": list(self.adopt), "left": list(self.left)}


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


def run_adoption(*, execute: bool) -> AdoptionReport:
    """Adopt every portfolio its owner can own and leave the rest as they are."""
    from alpaca_connectors.market_data.configurations import get_bar_configuration
    from alpaca_connectors.portfolios.configurations import unadopted_portfolio_configurations

    report = AdoptionReport(execute=execute)
    for configuration in unadopted_portfolio_configurations():
        bars = (
            get_bar_configuration(configuration.bars_configuration_uid)
            if configuration.bars_configuration_uid is not None
            else None
        )
        account_uid = _adoptable_account(configuration, bars)
        uid = str(configuration.uid)
        if account_uid is None:
            report.left.append(uid)
            continue
        report.adopt.append(uid)
        if execute:
            adopt_portfolio(configuration, bars, account_uid)
    return report


__all__ = ["AdoptionReport", "adopt_portfolio", "run_adoption"]
