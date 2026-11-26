"""An account's portfolios share one price chain (ADR 0033).

``AccountStockBars`` is configured by the account and the bars profile and resolves its assets when
it runs; the account's ``InterpolatedPrices`` take their assets from it (ms-markets 6.2.1). Every
portfolio of the account on that profile is valued by that one chain, and every node of the graph
rebuilds in a node Job run. Nothing here leaves the process.
"""

from __future__ import annotations

import datetime as dt
import socket
import uuid
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

import metatables.updaters.job_mode as job_mode
from alpaca_connectors.market_data.account_bars import AccountStockBars, AccountStockBarsConfig
from alpaca_connectors.market_data.alpaca_bars import AlpacaStockBarsFetch
from alpaca_connectors.market_data.storage import storage_for
from alpaca_connectors.portfolios import bars_scope, execution
from alpaca_connectors.portfolios.execution import ResolvedPortfolioConfiguration
from metatables import TimeIndexMetaTable, TimeIndexTableUpdater

ACCOUNT_UID = uuid.UUID("66666666-6666-4666-8666-666666666666")
OTHER_ACCOUNT_UID = uuid.UUID("88888888-8888-4888-8888-888888888888")
PROFILE = ("1d", "iex", "raw")
START = dt.datetime(2026, 11, 16, 14, tzinfo=dt.UTC)
AAPL = f"ALPACA::{uuid.UUID(int=1)}"
NVDA = f"ALPACA::{uuid.UUID(int=3)}"
SCOPE = "alpaca_connectors.portfolios.bars_scope.resolve_account_bars_scope"
CURRENT = "alpaca_connectors.market_data.services.daily_bars_current_through"


def _bars(account_uid=ACCOUNT_UID, profile=PROFILE) -> AccountStockBars:
    frequency_id, feed, adjustment = profile
    return AccountStockBars(
        AccountStockBarsConfig(
            account_uid=account_uid, frequency_id=frequency_id, feed=feed, adjustment=adjustment
        )
    )


def _scope(*assets: str) -> bars_scope.AccountBarsScope:
    return bars_scope.AccountBarsScope(
        account_uid=str(ACCOUNT_UID),
        profile=PROFILE,
        configuration_uids=("configuration",),
        retired_configuration_uids=(),
        signal_uids=("signal",) if assets else (),
        asset_identifiers=assets,
    )


@contextmanager
def _bound(*storages: type):
    """Bind fake backend metadata to storage classes, as the engine does, and restore them."""
    with ExitStack() as stack:
        for position, storage in enumerate(storages, start=1):
            before = dict(vars(storage))
            storage._bind_meta_table(
                TimeIndexMetaTable.model_construct(
                    uid=f"a0000000-0000-4000-8000-{position:012d}",
                    data_source_uid="d0000000-0000-4000-8000-000000000001",
                    identifier=storage.__metatable_identifier__,
                    physical_table_name=storage.__table__.name,
                    cadence="1d",
                )
            )

            def restore(storage=storage, before=before) -> None:
                for name in set(vars(storage)) - set(before):
                    delattr(storage, name)
                for name, value in before.items():
                    if vars(storage).get(name) is not value:
                        setattr(storage, name, value)

            stack.callback(restore)
        yield


@pytest.fixture
def offline(monkeypatch):
    """Refuse every connection: building, checking and rebuilding make no request."""

    def refuse(*_args, **_kwargs):
        raise AssertionError("A node's build, check or rebuild made a request.")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def _price_tables():
    from alpaca_connectors.instances import INTERPOLATED_PRICES_BY_PROFILE

    return storage_for(*PROFILE), INTERPOLATED_PRICES_BY_PROFILE[PROFILE]


# ------------------------------------------------------------------------------------------------
# No asset enters an update hash (decision 1)
# ------------------------------------------------------------------------------------------------
def test_the_accounts_bars_hash_their_account_and_profile_only() -> None:
    bars = _bars()

    # Only the configuration is saved: no client, no list of assets.
    assert set(bars.build_configuration) == {
        "config",
        "hash_namespace",
        "table_updater_class_import_path",
    }
    assert bars.config.asset_list is None
    assert bars.output_table is storage_for(*PROFILE)
    assert bars.update_hash == _bars(profile=("1d", "IEX", "raw")).update_hash
    assert bars.update_hash != _bars(account_uid=OTHER_ACCOUNT_UID).update_hash
    assert bars.update_hash != _bars(profile=("1d", "sip", "all")).update_hash


def test_the_accounts_prices_take_their_assets_from_its_bars(offline) -> None:
    from alpaca_connectors.portfolios.interpolated_prices import build_account_interpolated_prices

    with _bound(*_price_tables()):
        prices = build_account_interpolated_prices(account_uid=ACCOUNT_UID, profile=PROFILE)
        again = build_account_interpolated_prices(account_uid=ACCOUNT_UID, profile=PROFILE)
        other = build_account_interpolated_prices(account_uid=OTHER_ACCOUNT_UID, profile=PROFILE)

        assert prices.interpolation_config.asset_list is None
        assert isinstance(prices.bars_ts, AccountStockBars)
        assert prices.dependencies() == {"bars_ts": prices.bars_ts}
        assert prices.update_hash == again.update_hash != other.update_hash
        # The assets are resolved when it runs, from its source, and kept on the source.
        with patch(SCOPE, return_value=_scope(AAPL, NVDA)) as resolve:
            assert prices.get_asset_list() == [AAPL, NVDA]
            assert prices.bars_ts.get_asset_list() == [AAPL, NVDA]
        resolve.assert_called_once_with(ACCOUNT_UID, PROFILE)


# ------------------------------------------------------------------------------------------------
# AccountStockBars resolves everything when it runs (decision 2)
# ------------------------------------------------------------------------------------------------
def test_the_bars_fetch_nothing_when_there_is_nothing_to_fetch() -> None:
    close = dt.datetime(2026, 11, 20, 21, tzinfo=dt.UTC)

    # Every asset already holds its last final bar: no keys, no Alpaca request.
    bars = _bars()
    with (
        patch(SCOPE, return_value=_scope(AAPL, NVDA)),
        patch(CURRENT, return_value=close) as current,
        patch.object(AccountStockBars, "_connect") as connect,
        patch.object(AlpacaStockBarsFetch, "update") as fetch,
    ):
        assert bars.update().empty
    current.assert_called_once_with(PROFILE, [AAPL, NVDA])
    connect.assert_not_called()
    fetch.assert_not_called()

    # An account with no live portfolio on the profile: not even the check.
    bars = _bars()
    with (
        patch(SCOPE, return_value=_scope()),
        patch(CURRENT) as current,
        patch.object(AccountStockBars, "_connect") as connect,
    ):
        assert bars.update().empty
    current.assert_not_called()
    connect.assert_not_called()


def test_the_bars_fetch_with_the_accounts_keys_and_a_failure_is_the_nodes() -> None:
    frame = pd.DataFrame({"close": [1.0]})
    bars = _bars()
    with (
        patch(SCOPE, return_value=_scope(AAPL)),
        patch(CURRENT, return_value=None),
        patch.object(AccountStockBars, "_connect") as connect,
        patch.object(AlpacaStockBarsFetch, "update", return_value=frame) as fetch,
    ):
        assert bars.update() is frame
    connect.assert_called_once_with()
    fetch.assert_called_once_with()

    # A failed fetch is the node's failure: it blocks what needs the bars (ADR 0033).
    bars = _bars()
    with (
        patch(SCOPE, return_value=_scope(AAPL)),
        patch(CURRENT, return_value=None),
        patch.object(AccountStockBars, "_connect"),
        patch.object(AlpacaStockBarsFetch, "update", side_effect=RuntimeError("Alpaca is down")),
        pytest.raises(RuntimeError, match="Alpaca is down"),
    ):
        bars.update()


def test_the_bars_resolve_the_accounts_keys_themselves() -> None:
    registration = {"account_uid": str(ACCOUNT_UID), "is_paper": True}
    bars = _bars()
    with (
        patch(
            "alpaca_connectors.account.services.get_account_registration",
            return_value=registration,
        ) as registered,
        patch(
            "alpaca_connectors.account.credentials.resolve_registered_alpaca_credentials",
            return_value="credentials",
        ) as credentials,
        patch(
            "alpaca_connectors.account.credentials.build_alpaca_historical_data_client",
            return_value="historical",
        ),
        patch(
            "alpaca_connectors.account.credentials.build_alpaca_trading_client",
            return_value="trading",
        ) as trading,
    ):
        bars._connect()
        bars._connect()
    registered.assert_called_once_with(str(ACCOUNT_UID))
    credentials.assert_called_once_with(registration)
    trading.assert_called_once_with(credentials="credentials", paper=True)
    assert (bars._historical_client, bars._trading_client) == ("historical", "trading")

    with (
        patch("alpaca_connectors.account.services.get_account_registration", return_value=None),
        pytest.raises(LookupError, match="does not exist"),
    ):
        _bars()._connect()


# ------------------------------------------------------------------------------------------------
# A portfolio's run uses its account's chain (decision 4)
# ------------------------------------------------------------------------------------------------
def _resolved(name: str, *, account_uid=ACCOUNT_UID, feed_account=None, profile=PROFILE):
    frequency_id, feed, adjustment = profile
    return ResolvedPortfolioConfiguration(
        configuration=SimpleNamespace(
            uid=uuid.UUID(name * 32),
            account_uid=account_uid,
            upsample_frequency_id="1d",
            intraday_bar_interpolation_rule="ffill",
        ),
        bars_configuration=SimpleNamespace(
            uid=f"bars-{name}",
            account_uid=feed_account,
            frequency_id=frequency_id,
            feed=feed,
            adjustment=adjustment,
        ),
        rebalance_configuration=SimpleNamespace(),
        signal_uid=f"signal-{name}",
        signal_start_time=START,
        asset_identifiers=(),
        source_time_index_meta_table_uid="bars-table",
    )


def test_a_run_builds_one_chain_per_account_and_profile() -> None:
    built = []

    def build(**kwargs):
        built.append(kwargs)
        return object()

    chains: dict = {}
    with patch.object(execution, "build_account_interpolated_prices", build):
        own = execution.account_price_chain(_resolved("a"), chains)
        # A portfolio not yet adopted, priced by a feed of the same account.
        named = execution.account_price_chain(
            _resolved("b", account_uid=None, feed_account=ACCOUNT_UID), chains
        )
        other_profile = execution.account_price_chain(
            _resolved("c", profile=("1d", "sip", "all")), chains
        )
        other_account = execution.account_price_chain(
            _resolved("d", account_uid=OTHER_ACCOUNT_UID), chains
        )
        # A run of one configuration builds its own, the same way.
        alone = execution.account_price_chain(_resolved("e"))

    assert own is named
    assert len({id(own), id(other_profile), id(other_account), id(alone)}) == 4
    assert [(call["account_uid"], call["profile"]) for call in built] == [
        (str(ACCOUNT_UID), PROFILE),
        (str(ACCOUNT_UID), ("1d", "sip", "all")),
        (str(OTHER_ACCOUNT_UID), PROFILE),
        (str(ACCOUNT_UID), PROFILE),
    ]
    with pytest.raises(LookupError, match="without an account"):
        execution.account_price_chain(_resolved("f", account_uid=None))


# ------------------------------------------------------------------------------------------------
# Every updater rebuilds in a node Job run (decision 6)
# ------------------------------------------------------------------------------------------------
def _plan(resolved, chains):
    from alpaca_connectors.portfolios.signal_definitions import signal_uid_for_key

    with (
        patch.object(
            execution,
            "ensure_trading_calendar",
            return_value=SimpleNamespace(uid="calendar-uid", unique_identifier="NYSE"),
        ),
        patch(
            "alpaca_connectors.portfolios.provided_weights.portfolio_signal_key",
            return_value=f"key-{resolved.signal_uid}",
        ),
    ):
        resolved = ResolvedPortfolioConfiguration(
            **{
                **{name: getattr(resolved, name) for name in resolved.__slots__},
                "signal_uid": signal_uid_for_key(f"key-{resolved.signal_uid}"),
            }
        )
        return execution.build_portfolio_run(resolved, price_chains=chains)


def _ledger(plan, portfolio: str):
    from msm_portfolios.time_index import PortfolioValues

    from alpaca_connectors.instances import (
        AlpacaPortfolioEventLedgerStorage,
        AlpacaPortfolioRebalanceStateStorage,
    )

    values = PortfolioValues(
        portfolio_configuration=plan.portfolio_configuration,
        rebalance_output_table=AlpacaPortfolioRebalanceStateStorage,
        event_ledger_output_table=AlpacaPortfolioEventLedgerStorage,
    )
    values.portfolio_weights.set_portfolio_configuration(
        plan.portfolio_configuration, portfolio_identifier=portfolio
    )
    return values.portfolio_weights.ensure_portfolio_engine()


def _nodes(*heads: TimeIndexTableUpdater) -> list[TimeIndexTableUpdater]:
    """Every updater of the heads' joined graph, as MetaTables plans it: by update hash."""
    found: dict[str, TimeIndexTableUpdater] = {}
    pending = list(heads)
    while pending:
        node = pending.pop()
        if node.update_hash in found:
            continue
        found[node.update_hash] = node
        pending.extend(
            dependency
            for dependency in node.dependencies().values()
            if isinstance(dependency, TimeIndexTableUpdater)
        )
    return list(found.values())


def _records(monkeypatch, nodes: list[TimeIndexTableUpdater]) -> None:
    """MetaTables' update records of ``nodes``, as a run registers and saves them."""

    def record(node: TimeIndexTableUpdater) -> SimpleNamespace:
        return SimpleNamespace(
            uid=f"update-{node.update_hash}",
            update_hash=node.update_hash,
            build_configuration=dict(node.local_initial_configuration),
            output_table=node.output_table_uid,
        )

    class Records:
        @staticmethod
        def get(uid: str) -> SimpleNamespace:
            return next(record(node) for node in nodes if f"update-{node.update_hash}" == uid)

        @staticmethod
        def filter(update_hash: str, output_table__uid: str) -> list[SimpleNamespace]:
            return [
                record(node)
                for node in nodes
                if node.update_hash == update_hash and node.output_table_uid == output_table__uid
            ]

    monkeypatch.setattr(job_mode, "TimeIndexTableUpdate", Records)


def test_two_portfolios_of_an_account_make_seven_nodes_and_each_rebuilds(
    offline, monkeypatch
) -> None:
    from alpaca_connectors.instances import (
        AlpacaPortfolioCalendarEventsStorage,
        AlpacaPortfolioEventLedgerStorage,
        AlpacaPortfolioRebalanceStateStorage,
    )
    from alpaca_connectors.portfolios.configurations import AlpacaPortfolioConfiguration

    def configuration(name: str) -> AlpacaPortfolioConfiguration:
        return AlpacaPortfolioConfiguration.model_validate(
            {
                "uid": uuid.UUID(name * 32),
                "name": f"Portfolio {name}",
                "description": None,
                "owner_gateway_uid": "owner",
                "owner_user_uid": "owner",
                "account_uid": ACCOUNT_UID,
                "signal_definition_uid": uuid.UUID(int=7),
                "bars_configuration_uid": None,
                "rebalance_configuration_uid": uuid.UUID(int=4),
                "portfolio_uid": None,
                "scheduled": True,
                "upsample_frequency_id": "1d",
                "intraday_bar_interpolation_rule": "ffill",
                "valuation_column": "close",
                "valuation_maximum_staleness_seconds": 86_400,
                "fail_on_missing_prices": True,
                "commission_fee": 0.00018,
                "created_at": START,
                "updated_at": START,
            }
        )

    rebalance = SimpleNamespace(
        uid=uuid.UUID(int=4),
        strategy="immediate_signal",
        calendar_identifier="NYSE",
        session_label="regular",
    )
    with _bound(
        *_price_tables(),
        AlpacaPortfolioCalendarEventsStorage,
        AlpacaPortfolioRebalanceStateStorage,
        AlpacaPortfolioEventLedgerStorage,
    ):
        chains: dict = {}
        heads = []
        for name in "ab":
            resolved = ResolvedPortfolioConfiguration(
                configuration=configuration(name),
                bars_configuration=SimpleNamespace(
                    uid=f"bars-{name}",
                    account_uid=None,
                    frequency_id="1d",
                    feed="iex",
                    adjustment="raw",
                ),
                rebalance_configuration=rebalance,
                signal_uid=f"signal-{name}",
                signal_start_time=START,
                asset_identifiers=(),
                source_time_index_meta_table_uid="bars-table",
            )
            heads.append(_ledger(_plan(resolved, chains), f"ALPACA_PORTFOLIO__{name}"))
        nodes = _nodes(*heads)
        _records(monkeypatch, nodes)

        kinds = sorted(type(node).__name__ for node in nodes)
        # 2N + 3: the account's bars, its prices and the calendar's events once, then each
        # portfolio's rebalance and ledger.
        assert kinds == [
            "AccountStockBars",
            "InterpolatedPrices",
            "PortfolioCalendarEvents",
            "PortfolioEngine",
            "PortfolioEngine",
            "PortfolioRebalance",
            "PortfolioRebalance",
        ]
        for node in nodes:
            arguments = job_mode.node_arguments(node)
            rebuilt = job_mode.rebuild_updater(
                f"update-{node.update_hash}",
                arguments["output_table"],
                arguments["argument_tables"],
            )
            assert type(rebuilt) is type(node)
            assert rebuilt.update_hash == node.update_hash
            assert rebuilt.output_table is node.output_table
