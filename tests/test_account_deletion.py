"""Deleting an account deletes everything that belongs to it (ADR 0028, decision 8).

ms-markets is replaced at the ``markets_api`` composites: what this connector controls is the order
of its calls, which the ``RESTRICT`` keys of ms-markets impose.
"""

from __future__ import annotations

import uuid
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest
from msm.api.remote import MarketsConflictError

from alpaca_connectors.account import deletion
from alpaca_connectors.account.deletion import AccountDeleteRefused, delete_account

ACCOUNT_UID = str(uuid.UUID("55555555-5555-4555-8555-555555555555"))
OTHER_ACCOUNT = str(uuid.UUID("66666666-6666-4666-8666-666666666666"))
PORTFOLIO = uuid.UUID(int=10)
KEPT_PORTFOLIO = uuid.UUID(int=11)
CONFIGURATION = uuid.UUID(int=20)
KEPT_CONFIGURATION = uuid.UUID(int=21)
MARKETS = "alpaca_connectors.markets_api"
CONFIGURATIONS = "alpaca_connectors.portfolios.configurations"
DEFINITIONS = "alpaca_connectors.portfolios.signal_definitions"


def _record(portfolio, configuration, signal):
    return SimpleNamespace(
        uid=uuid.uuid4(),
        portfolio_uid=portfolio,
        portfolio_configuration_uid=configuration,
        signal_uid=signal,
    )


def _run(
    *,
    preflight_blockers=(),
    held_by=(),
    refuse_markets=False,
    registration=True,
    registration_values=None,
    priced=(),
    ended_signal=None,
):
    """Delete the account with Markets and storage replaced; return the recorder."""
    calls = MagicMock()
    calls.end_portfolios.side_effect = lambda rows: [
        SimpleNamespace(
            signal=ended_signal, signal_definition_uid=None, signal_definition_state=None
        )
        for _ in rows
    ]
    portfolio = SimpleNamespace(uid=CONFIGURATION, portfolio_uid=PORTFOLIO)
    definitions = [
        SimpleNamespace(uid="definition-followed", signal_key="followed"),
        SimpleNamespace(uid="definition-free", signal_key="free"),
    ]
    records = [
        _record(PORTFOLIO, CONFIGURATION, "signal-followed"),
        _record(KEPT_PORTFOLIO, KEPT_CONFIGURATION, "signal-kept"),
    ]
    calls.markets_delete.side_effect = (
        MarketsConflictError("refused by a RESTRICT key") if refuse_markets else None
    )
    calls.blockers.side_effect = lambda uids: {
        str(uid): ["held"] if uid == KEPT_PORTFOLIO else [] for uid in uids
    }

    def signal_states(signals):
        calls.delete_signals(list(signals))
        # A retired Portfolio follows signal-followed until the account's delete deletes it;
        # another account's history keeps signal-kept.
        kept = {"signal-kept"} | (set() if calls.delete_portfolios.called else {"signal-followed"})
        return {signal: "kept" if signal in kept else "deleted" for signal in signals}

    # More patches than one `with` statement may nest: an ExitStack enters them.
    patches = [
        patch("alpaca_connectors.runtime.start_markets_engine", return_value=SimpleNamespace()),
        patch(
            "alpaca_connectors.account.services.get_account_registration",
            return_value=(
                {"account_unique_identifier": "ACCOUNT__ALPACA", **(registration_values or {})}
                if registration
                else None
            ),
        ),
        patch(
            f"{MARKETS}.account_delete_preflight",
            return_value={"blockers": list(preflight_blockers), "warnings": []},
        ),
        patch(f"{MARKETS}.current_target_account_uids", return_value=list(held_by)),
        patch(
            f"{MARKETS}.account_target_positions",
            return_value=SimpleNamespace(positions=[object()]),
        ),
        patch(f"{MARKETS}.clear_account_target", calls.clear_target),
        patch(f"{MARKETS}.delete_account", calls.markets_delete),
        patch(f"{MARKETS}.portfolios_delete_blockers", calls.blockers),
        patch(f"{MARKETS}.delete_portfolios", calls.delete_portfolios),
        patch(f"{CONFIGURATIONS}.portfolio_configurations_of_account", return_value=[portfolio]),
        patch(f"{CONFIGURATIONS}.ended_portfolios_of_account", return_value=records),
        patch(f"{CONFIGURATIONS}.forget_ended_portfolios", calls.forget_records),
        patch(
            "alpaca_connectors.market_data.configurations.bar_configurations_for_account",
            return_value=[
                SimpleNamespace(uid=uuid.UUID(int=30), asset_source="account_holdings"),
                SimpleNamespace(uid=uuid.UUID(int=31), asset_source="portfolios"),
            ],
        ),
        patch(
            f"{CONFIGURATIONS}.portfolio_configurations_naming_any_bars",
            calls.naming,
        ),
        patch.object(deletion, "_account_definitions", calls.definitions),
        patch(
            f"{DEFINITIONS}.definition_signal_uid",
            side_effect=lambda definition: f"signal-{definition.signal_key}",
        ),
        patch(f"{DEFINITIONS}.delete_signals_rows", side_effect=signal_states),
        patch(
            "alpaca_connectors.portfolios.provided_weights.end_portfolio_configurations",
            calls.end_portfolios,
        ),
        patch.object(deletion, "delete_account_orders_and_trades", calls.orders),
        patch.object(deletion, "delete_portfolio_series", calls.series),
    ]
    calls.naming.return_value = list(priced)
    calls.definitions.return_value = definitions
    with ExitStack() as stack:
        for item in patches:
            stack.enter_context(item)
        result = delete_account(ACCOUNT_UID)
    return result, calls


def test_the_account_and_all_it_owns_go_in_the_order_markets_keys_impose() -> None:
    result, calls = _run()
    order = [name for name, *_ in calls.mock_calls]

    # Its own target first, so retiring its portfolios is not refused; then they end together.
    assert order.index("clear_target") < order.index("end_portfolios")
    calls.end_portfolios.assert_called_once()
    # A signal no retired Portfolio follows goes before the account; the followed one waits.
    assert call.delete_signals(["signal-followed", "signal-free"]) in calls.mock_calls
    assert order.index("delete_signals") < order.index("markets_delete")
    # Orders and trades in this application's instances refuse the account delete: first.
    calls.orders.assert_called_once_with("ACCOUNT__ALPACA")
    assert order.index("orders") < order.index("markets_delete")
    calls.markets_delete.assert_called_once_with(ACCOUNT_UID)
    # One preflight for the recorded Portfolios; for the ones nothing else names, their series,
    # then one bulk delete of the Portfolios, then one delete of their records.
    calls.blockers.assert_called_once_with([PORTFOLIO, KEPT_PORTFOLIO])
    calls.series.assert_called_once_with(["ALPACA_PORTFOLIO__" + CONFIGURATION.hex.upper()])
    calls.delete_portfolios.assert_called_once_with([str(PORTFOLIO)])
    [forgotten] = calls.forget_records.call_args.args[0]
    assert forgotten.portfolio_uid == PORTFOLIO
    assert order.index("series") < order.index("delete_portfolios") < order.index("forget_records")
    assert order.index("markets_delete") < order.index("series")
    # Another account's history keeps KEPT_PORTFOLIO, and so its signal.
    assert result.kept_portfolio_uids == [str(KEPT_PORTFOLIO)]
    assert result.deleted_portfolio_uids == [str(PORTFOLIO)]
    assert "signal-kept" in result.kept_signal_uids
    assert {"signal-free", "signal-followed"} <= set(result.deleted_signal_uids)


def test_the_delete_reads_each_kind_once() -> None:
    # The preflight's reads serve the delete (issue #46): the definitions are read once, and the
    # portfolios its `portfolios` feeds price in one request for all the feeds.
    _, calls = _run()
    calls.definitions.assert_called_once_with(ACCOUNT_UID)
    calls.naming.assert_called_once_with([uuid.UUID(int=31)])
    # The account's signals go in two passes: before the account delete and after it.
    assert calls.delete_signals.call_count == 2


@pytest.mark.parametrize(("state", "reported"), [("deleted", True), ("kept", False)])
def test_a_signal_its_portfolio_delete_deleted_is_reported(state, reported) -> None:
    # Ending a portfolio deletes its definition, and the signal once nothing keeps it (#38).
    result, _ = _run(ended_signal={"uid": "signal-ended", "state": state})
    assert ("signal-ended" in result.deleted_signal_uids) is reported


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"preflight_blockers": ["Other accounts' orders use its order managers."]}, "orders"),
        ({"held_by": [OTHER_ACCOUNT]}, "another account's current target"),
    ],
)
def test_a_refused_delete_changes_nothing(kwargs, message) -> None:
    with pytest.raises(AccountDeleteRefused) as refused:
        _run(**kwargs)
    assert message in str(refused.value)
    # Another account is never named (ADR 0019).
    assert OTHER_ACCOUNT not in str(refused.value)


def test_the_accounts_own_target_never_blocks_its_delete() -> None:
    # Its own current target names its portfolios; only another account's refuses.
    result, calls = _run(held_by=[ACCOUNT_UID])
    calls.markets_delete.assert_called_once()
    assert result.deleted_portfolio_uids


def test_a_retry_after_the_account_is_gone_finishes_its_portfolios_and_signals() -> None:
    result, calls = _run(registration=False)
    calls.end_portfolios.assert_not_called()
    calls.markets_delete.assert_not_called()
    calls.delete_portfolios.assert_called_once_with([str(PORTFOLIO)])
    assert "signal-followed" in result.deleted_signal_uids


def test_a_portfolio_not_yet_adopted_its_portfolios_feed_prices_ends_with_the_account() -> None:
    # It runs with the account's keys: the account's too, delete means delete.
    unadopted = SimpleNamespace(uid=uuid.UUID(int=22), portfolio_uid=None)
    result, calls = _run(priced=[unadopted])
    [ended] = calls.end_portfolios.call_args.args
    assert [row.uid for row in ended] == [CONFIGURATION, unadopted.uid]
    assert result.ended_portfolio_count == 2


def test_markets_refusing_the_account_delete_is_reported() -> None:
    with pytest.raises(AccountDeleteRefused):
        _run(refuse_markets=True)


# ------------------------------------------------------------------------------------------------
# The Markets calls
# ------------------------------------------------------------------------------------------------
def test_the_preflight_and_the_signal_delete_call_their_ms_markets_routes() -> None:
    from alpaca_connectors import markets_api

    client = MagicMock()
    client.request.return_value = {"blockers": []}
    rows = [
        SimpleNamespace(uid="metadata-a", signal_uid="signal-a"),
        SimpleNamespace(uid="metadata-b", signal_uid="signal-b"),
    ]
    with (
        patch(
            "alpaca_connectors.runtime.use_markets_api", return_value=SimpleNamespace(client=client)
        ),
        patch(
            "msm_portfolios.api.market_metadata.SignalMetadata.filter", return_value=rows
        ) as read,
        patch("msm_portfolios.api.datasets.PortfolioDatasets.unregister_signal") as unbind,
    ):
        markets_api.account_delete_preflight(ACCOUNT_UID)
        markets_api.delete_signals_metadata(["signal-a", "signal-b", "signal-c"])

    selection = {"selection": {"mode": "explicit", "uids": [ACCOUNT_UID]}, "options": {}}
    # One read of the signals' metadata; unbinding and deleting are one-signal routes.
    read.assert_called_once_with(signal_uid__in=["signal-a", "signal-b", "signal-c"], limit=3)
    assert client.request.call_args_list == [
        call("POST", "/api/v1/account/bulk-delete/preflight/", json=selection),
        call("DELETE", "/api/v1/portfolio-signal/metadata-a/"),
        call("DELETE", "/api/v1/portfolio-signal/metadata-b/"),
    ]
    assert unbind.call_args_list == [call(signal_uid="signal-a"), call(signal_uid="signal-b")]


def test_several_portfolios_are_preflighted_and_deleted_in_one_request_each() -> None:
    from alpaca_connectors import markets_api

    client = MagicMock()
    client.request.side_effect = [
        {
            "allowed": False,
            "blockers": [
                f"Portfolio {KEPT_PORTFOLIO}: TargetPositionsStorage.portfolio_uid references "
                "this portfolio (count=2).",
            ],
        },
        {"deleted_count": 1},
    ]
    with patch(
        "alpaca_connectors.runtime.use_markets_api", return_value=SimpleNamespace(client=client)
    ):
        blockers = markets_api.portfolios_delete_blockers([PORTFOLIO, KEPT_PORTFOLIO])
        markets_api.delete_portfolios([str(PORTFOLIO)])

    # Each blocker goes to the portfolio ms-markets names in it.
    assert blockers[str(PORTFOLIO)] == []
    assert len(blockers[str(KEPT_PORTFOLIO)]) == 1
    both = [str(PORTFOLIO), str(KEPT_PORTFOLIO)]
    assert client.request.call_args_list == [
        call(
            "POST",
            "/api/v1/portfolio/bulk-delete/preflight/",
            json={"selection": {"mode": "explicit", "uids": both}, "options": {}},
        ),
        call(
            "POST",
            "/api/v1/portfolio/bulk-delete/",
            json={"selection": {"mode": "explicit", "uids": [str(PORTFOLIO)]}, "options": {}},
        ),
    ]


def test_a_blocker_that_names_no_portfolio_blocks_them_all() -> None:
    from alpaca_connectors import markets_api

    client = MagicMock()
    client.request.return_value = {"blockers": ["You may not write the table it reaches."]}
    with patch(
        "alpaca_connectors.runtime.use_markets_api", return_value=SimpleNamespace(client=client)
    ):
        blockers = markets_api.portfolios_delete_blockers([PORTFOLIO, KEPT_PORTFOLIO])
    assert all(blockers.values())


def test_current_targets_are_read_in_pages() -> None:
    from alpaca_connectors import markets_api

    def page(items, more):
        return SimpleNamespace(
            items=[SimpleNamespace(account_uid=uid) for uid in items],
            page_info=SimpleNamespace(has_next_page=more),
        )

    client = MagicMock()
    client.portfolio_account_targets.side_effect = [
        page([ACCOUNT_UID], True),
        page([OTHER_ACCOUNT], False),
    ]
    with patch(
        "alpaca_connectors.runtime.use_markets_api", return_value=SimpleNamespace(client=client)
    ):
        assert markets_api.current_target_account_uids("p") == [ACCOUNT_UID, OTHER_ACCOUNT]
    assert client.portfolio_account_targets.call_args_list == [
        call("p", limit=markets_api.MARKETS_BATCH_SIZE, offset=0),
        call("p", limit=markets_api.MARKETS_BATCH_SIZE, offset=markets_api.MARKETS_BATCH_SIZE),
    ]
