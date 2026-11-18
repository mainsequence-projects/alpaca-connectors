"""The ``portfolio adopt`` rollout gives every portfolio one owning account (ADR 0028, decision 9)."""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from alpaca_connectors.portfolios.adoption import adopt_portfolio, run_adoption
from alpaca_connectors.portfolios.configurations import AlpacaPortfolioConfiguration
from alpaca_connectors.portfolios.provided_weights import ProvidedWeightsError

NOW = dt.datetime(2026, 11, 18, 9, tzinfo=dt.UTC)
OWNED, FOREIGN, HELD = (uuid.UUID(int=n) for n in (1, 2, 3))
ACCOUNT, OTHER_ACCOUNT = uuid.UUID(int=101), uuid.UUID(int=102)
FEED, OTHER_FEED, UNNAMED_FEED = uuid.UUID(int=11), uuid.UUID(int=12), uuid.UUID(int=13)
CONFIGURATIONS = "alpaca_connectors.portfolios.configurations"
BARS = "alpaca_connectors.market_data.configurations"


def configuration(uid: uuid.UUID, bars: uuid.UUID) -> AlpacaPortfolioConfiguration:
    return AlpacaPortfolioConfiguration.model_validate(
        {
            "uid": uid,
            "name": f"Portfolio {uid.int}",
            "description": None,
            "owner_gateway_uid": "owner",
            "owner_user_uid": "owner",
            "account_uid": None,
            "signal_definition_uid": None,
            "bars_configuration_uid": bars,
            "rebalance_configuration_uid": uuid.uuid4(),
            "portfolio_uid": uuid.uuid4(),
            "scheduled": True,
            "upsample_frequency_id": "1d",
            "intraday_bar_interpolation_rule": "ffill",
            "valuation_column": "close",
            "valuation_maximum_staleness_seconds": 86_400,
            "fail_on_missing_prices": True,
            "commission_fee": 0.00018,
            "created_at": NOW,
            "updated_at": NOW,
        }
    )


def feed(uid: uuid.UUID, account: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(
        uid=uid,
        account_uid=account,
        description=None,
        frequency_id="1d",
        feed="sip",
        adjustment="all",
        enabled=True,
        scheduled=True,
    )


FEEDS = {FEED: feed(FEED, ACCOUNT), OTHER_FEED: feed(OTHER_FEED, OTHER_ACCOUNT)}
OWNERS = {
    str(ACCOUNT): {"owner_gateway_uid": "owner", "owner_user_uid": "owner"},
    str(OTHER_ACCOUNT): {"owner_gateway_uid": "other", "owner_user_uid": "other"},
}


def _run(*, execute: bool, remaining=()):
    unadopted = [
        configuration(OWNED, FEED),
        configuration(FOREIGN, OTHER_FEED),
        configuration(HELD, OTHER_FEED),
    ]

    def end(row):
        if row.uid == HELD:
            raise ProvidedWeightsError("portfolio_in_account_target", "held", status_code=409)

    listings = [unadopted, list(remaining)]
    with (
        patch(
            f"{CONFIGURATIONS}.unadopted_portfolio_configurations",
            side_effect=lambda: listings.pop(0),
        ),
        patch(f"{BARS}.get_bar_configuration", side_effect=lambda uid: FEEDS.get(uid)),
        patch(
            "alpaca_connectors.account.services.get_account_registration",
            side_effect=lambda uid: OWNERS.get(uid),
        ),
        patch("alpaca_connectors.portfolios.adoption.adopt_portfolio") as adopt,
        patch(
            "alpaca_connectors.portfolios.provided_weights.end_portfolio_configuration",
            side_effect=end,
        ) as end_portfolio,
        patch(
            f"{BARS}.list_bar_configurations",
            return_value=([SimpleNamespace(uid=OTHER_FEED), SimpleNamespace(uid=UNNAMED_FEED)], 2),
        ) as feeds,
        patch(f"{BARS}.delete_bar_configuration") as delete_feed,
    ):
        report = run_adoption(execute=execute)
    return report, adopt, end_portfolio, delete_feed, feeds


def test_a_dry_run_reports_and_changes_nothing() -> None:
    report, adopt, end_portfolio, delete_feed, _ = _run(execute=False)

    assert report.adopt == [str(OWNED)]
    assert report.end == [str(FOREIGN), str(HELD)]
    adopt.assert_not_called()
    end_portfolio.assert_not_called()
    delete_feed.assert_not_called()


def test_a_run_adopts_ends_and_reports_what_a_target_holds() -> None:
    held = configuration(HELD, OTHER_FEED)
    report, adopt, end_portfolio, delete_feed, feeds = _run(execute=True, remaining=[held])

    # Its owner owns the bars' account: the portfolio belongs to that account.
    [(adopted, bars, account)] = [entry.args for entry in adopt.call_args_list]
    assert (adopted.uid, bars.uid, account) == (OWNED, FEED, ACCOUNT)
    # Another owner's account: the portfolio ends; one a current target names is refused.
    assert [entry.args[0].uid for entry in end_portfolio.call_args_list] == [FOREIGN, HELD]
    assert (report.end, report.refused) == ([str(FOREIGN)], [str(HELD)])
    # The `portfolios` feed the held portfolio still names stays; the unnamed one goes.
    assert feeds.call_args.kwargs["portfolio_owned"] is False
    delete_feed.assert_called_once_with(UNNAMED_FEED)


def test_adopting_keeps_the_signal_and_retries_safely() -> None:
    row = configuration(OWNED, FEED)
    definition = SimpleNamespace(uid=uuid.UUID(int=55))
    with (
        patch(
            "alpaca_connectors.portfolios.signal_definitions.account_signal_definition",
            return_value=None,
        ),
        patch(
            "alpaca_connectors.portfolios.signal_definitions.create_signal_definition",
            return_value=definition,
        ) as create_definition,
        patch(f"{BARS}.portfolio_bar_configuration", return_value=None),
        patch(f"{BARS}.create_portfolio_bar_configuration") as create_bars,
        patch(f"{CONFIGURATIONS}.update_portfolio_configuration_row") as update,
    ):
        adopt_portfolio(row, FEEDS[FEED], ACCOUNT)

    kwargs = create_definition.call_args.kwargs
    assert kwargs["key"] == kwargs["signal_key"] == str(OWNED)
    assert create_bars.call_args.kwargs["portfolio_configuration_uid"] == OWNED
    update.assert_called_once_with(
        OWNED,
        values={
            "account_uid": ACCOUNT,
            "signal_definition_uid": definition.uid,
            "bars_configuration_uid": None,
        },
    )

    # A retry finds what the first attempt made instead of making it again.
    with (
        patch(
            "alpaca_connectors.portfolios.signal_definitions.account_signal_definition",
            return_value=definition,
        ),
        patch(
            "alpaca_connectors.portfolios.signal_definitions.create_signal_definition"
        ) as create_again,
        patch(f"{BARS}.portfolio_bar_configuration", return_value=object()),
        patch(f"{BARS}.create_portfolio_bar_configuration") as create_bars_again,
        patch(f"{CONFIGURATIONS}.update_portfolio_configuration_row"),
    ):
        adopt_portfolio(row, FEEDS[FEED], ACCOUNT)
    create_again.assert_not_called()
    create_bars_again.assert_not_called()
