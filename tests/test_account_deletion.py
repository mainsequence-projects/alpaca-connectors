"""Deleting an account deletes everything that belongs to it (ADR 0028, decision 8).

ms-markets is replaced at the ``markets_api`` composites: what this connector controls is the order
of its calls, which the ``RESTRICT`` keys of ms-markets impose, and how few of them it makes
(issue #46): the preflight's reads in one step, and each group of its own writes in one
transaction (``OwnTableWrites``).
"""

from __future__ import annotations

import threading
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
REGISTRATION = {"account_unique_identifier": "ACCOUNT__ALPACA"}


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
    registration=REGISTRATION,
    priced=(),
    ended_signal=None,
    stored=None,
    together=None,
):
    """Delete the account with Markets and storage replaced; return the result and the recorder.

    ``registration`` is what the API's owner check read and passes on; ``None`` is a retry after
    the account is gone. ``stored`` are the account's records before the delete; by default the
    one of KEPT_PORTFOLIO, which another account's history keeps. Ending the account's portfolio
    records PORTFOLIO with ``signal-followed``.
    """
    calls = MagicMock()
    calls.end_portfolios.side_effect = lambda rows, **kwargs: [
        SimpleNamespace(
            signal=ended_signal,
            signal_definition_uid=None,
            signal_definition_state=None,
            portfolio_uid=str(row.portfolio_uid) if row.portfolio_uid else None,
            configuration_uid=str(row.uid),
            recorded_signal_uid="signal-followed" if row.portfolio_uid else None,
        )
        for row in rows
    ]
    portfolio = SimpleNamespace(uid=CONFIGURATION, portfolio_uid=PORTFOLIO)
    definitions = [
        SimpleNamespace(uid="definition-followed", signal_key="followed"),
        SimpleNamespace(uid="definition-free", signal_key="free"),
    ]
    stored = (
        [_record(KEPT_PORTFOLIO, KEPT_CONFIGURATION, "signal-kept")] if stored is None else stored
    )
    calls.markets_delete.side_effect = (
        MarketsConflictError("refused by a RESTRICT key") if refuse_markets else None
    )
    calls.blockers.side_effect = lambda uids: {
        str(uid): ["held"] if uid == str(KEPT_PORTFOLIO) else [] for uid in uids
    }
    calls.elsewhere.return_value = set()
    calls.ended.return_value = stored

    def signal_states(signals, kept=()):
        calls.delete_signals(list(signals))
        # A retired Portfolio follows signal-followed and another account's history keeps
        # signal-kept until the account's delete deletes them.
        return {
            signal: "kept" if signal in {"signal-kept", "signal-followed"} else "deleted"
            for signal in signals
        }

    def run(writes):
        calls.transaction(list(writes.entries))

    def waits(answer):
        # With ``together``, a preflight read lets go only once all of them wait at once.
        def read(*args, **kwargs):
            if together is not None:
                together.wait()
            return answer

        return read

    # More patches than one `with` statement may nest: an ExitStack enters them.
    patches = [
        patch("alpaca_connectors.runtime.start_markets_engine", return_value=SimpleNamespace()),
        patch(
            "alpaca_connectors.account.services.get_account_registration",
            calls.read_registration,
        ),
        patch(
            f"{MARKETS}.account_delete_preflight",
            side_effect=waits({"blockers": list(preflight_blockers), "warnings": []}),
        ),
        patch(f"{MARKETS}.current_target_account_uids", return_value=list(held_by)),
        patch(
            f"{MARKETS}.account_target_positions",
            side_effect=waits(SimpleNamespace(positions=[object()])),
        ),
        patch(f"{MARKETS}.clear_account_target", calls.clear_target),
        patch(f"{MARKETS}.delete_account", calls.markets_delete),
        patch(f"{MARKETS}.portfolios_delete_blockers", calls.blockers),
        patch(f"{MARKETS}.delete_portfolios", calls.delete_portfolios),
        patch(f"{MARKETS}.end_signals", calls.end_signals),
        patch(
            f"{CONFIGURATIONS}.portfolio_configurations_of_account",
            side_effect=waits([portfolio]),
        ),
        patch(f"{CONFIGURATIONS}.ended_portfolios_of_account", calls.ended),
        patch(f"{CONFIGURATIONS}.ended_signal_uids", calls.elsewhere),
        patch(f"{CONFIGURATIONS}.OwnTableWrites.run", run),
        patch(
            "alpaca_connectors.market_data.configurations.bar_configurations_for_account",
            side_effect=waits(
                [
                    SimpleNamespace(uid=uuid.UUID(int=30), asset_source="account_holdings"),
                    SimpleNamespace(uid=uuid.UUID(int=31), asset_source="portfolios"),
                ]
            ),
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
        patch.object(deletion, "delete_portfolio_series", calls.series),
    ]
    calls.naming.return_value = list(priced)
    calls.read_registration.return_value = registration
    if together is not None:
        calls.definitions.side_effect = waits(definitions)
        calls.ended.side_effect = waits(stored)
    else:
        calls.definitions.return_value = definitions
    with ExitStack() as stack:
        for item in patches:
            stack.enter_context(item)
        if registration is None:
            result = delete_account(ACCOUNT_UID)
        else:
            result = delete_account(ACCOUNT_UID, registration=registration)
    return result, calls


def _tables(entries):
    return [(kind, table, list(values)) for kind, table, _, values in entries]


def test_the_account_and_all_it_owns_go_in_the_order_markets_keys_impose() -> None:
    result, calls = _run()
    order = [name for name, *_ in calls.mock_calls]

    # Its own target first, so retiring its portfolios is not refused; then they end together,
    # with the definitions the preflight read.
    assert order.index("clear_target") < order.index("end_portfolios")
    [(rows,), kwargs] = calls.end_portfolios.call_args
    assert kwargs["definitions"] == calls.definitions.return_value
    # Orders and trades in this application's instances refuse the account delete: they go in
    # the transaction that ends the portfolios.
    assert _tables(kwargs["writes"].entries) == [
        ("delete", "TradesStorage_main_alpaca_connectors", ["ACCOUNT__ALPACA"]),
        ("delete", "OrdersStorage_main_alpaca_connectors", ["ACCOUNT__ALPACA"]),
    ]
    # A signal no retired Portfolio follows goes before the account; the followed one waits.
    assert call.delete_signals(["signal-followed", "signal-free"]) in calls.mock_calls
    assert order.index("delete_signals") < order.index("markets_delete")
    calls.markets_delete.assert_called_once_with(ACCOUNT_UID)
    # One preflight for the recorded Portfolios: the one stored before and the one just ended,
    # known without reading them back. For those nothing else names, their series, then one
    # bulk delete of the Portfolios.
    calls.blockers.assert_called_once_with([str(KEPT_PORTFOLIO), str(PORTFOLIO)])
    calls.series.assert_called_once_with(["ALPACA_PORTFOLIO__" + CONFIGURATION.hex.upper()])
    calls.delete_portfolios.assert_called_once_with([str(PORTFOLIO)])
    assert order.index("markets_delete") < order.index("series") < order.index("delete_portfolios")
    # Then one transaction forgets their records and the freed signal's asset rows, and
    # Markets ends that signal.
    [finish] = [made.args[0] for made in calls.transaction.call_args_list]
    assert _tables(finish) == [
        ("delete", "EndedPortfolioTable", [PORTFOLIO]),
        ("delete", "SignalAssetTable", ["signal-followed"]),
    ]
    calls.end_signals.assert_called_once_with(["signal-followed"])
    assert (
        order.index("delete_portfolios") < order.index("transaction") < order.index("end_signals")
    )
    # Another account's history keeps KEPT_PORTFOLIO, and so its signal.
    assert result.kept_portfolio_uids == [str(KEPT_PORTFOLIO)]
    assert result.deleted_portfolio_uids == [str(PORTFOLIO)]
    assert "signal-kept" in result.kept_signal_uids
    assert {"signal-free", "signal-followed"} <= set(result.deleted_signal_uids)


def test_the_delete_reads_each_kind_once() -> None:
    # The preflight's reads serve the delete (issue #46): the registration the owner check read,
    # the definitions, the records stored before and the portfolios the `portfolios` feeds price,
    # in one request for all the feeds; the records are not read back after the account delete.
    _, calls = _run()
    calls.read_registration.assert_not_called()
    calls.definitions.assert_called_once_with(ACCOUNT_UID)
    calls.ended.assert_called_once_with(ACCOUNT_UID)
    calls.naming.assert_called_once_with([uuid.UUID(int=31)])
    # Whether another record follows the signals is read once, without this account's records.
    calls.elsewhere.assert_called_once_with(
        ["signal-kept", "signal-followed"],
        excluding_portfolio_uids=[str(KEPT_PORTFOLIO), str(PORTFOLIO)],
    )


def test_the_preflight_reads_run_together() -> None:
    # Six reads that need only the account, in one step: one after another, the barrier times out.
    _run(together=threading.Barrier(6, timeout=5))


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
    stored = [
        _record(PORTFOLIO, CONFIGURATION, "signal-followed"),
        _record(KEPT_PORTFOLIO, KEPT_CONFIGURATION, "signal-kept"),
    ]
    result, calls = _run(registration=None, stored=stored)
    # The registration is read, as nothing passed it; with none, the records are read back.
    calls.read_registration.assert_called_once_with(ACCOUNT_UID)
    calls.end_portfolios.assert_not_called()
    calls.markets_delete.assert_not_called()
    calls.ended.assert_called_once_with(ACCOUNT_UID)
    calls.delete_portfolios.assert_called_once_with([str(PORTFOLIO)])
    assert "signal-followed" in result.deleted_signal_uids


def test_an_account_without_portfolios_still_deletes_its_orders_and_trades() -> None:
    with (
        patch(f"{CONFIGURATIONS}.portfolio_configurations_of_account", return_value=[]),
        patch.object(deletion, "_with_priced_portfolios", return_value=[]),
    ):
        _, calls = _run(stored=[])
    calls.end_portfolios.assert_not_called()
    [entries] = [c.args[0] for c in calls.transaction.call_args_list]
    assert [table for _, table, _ in _tables(entries)] == [
        "TradesStorage_main_alpaca_connectors",
        "OrdersStorage_main_alpaca_connectors",
    ]


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
def test_the_preflight_and_the_signal_delete_call_their_ms_markets_routes(recipes) -> None:
    from alpaca_connectors import markets_api

    client = MagicMock()
    client.request.return_value = {"blockers": []}
    rows = [
        SimpleNamespace(uid="metadata-a", signal_uid="signal-a"),
        SimpleNamespace(uid="metadata-b", signal_uid="signal-b"),
    ]
    recipes.answer("end_signal", lambda signal_uid: {"deleted": signal_uid})
    with (
        patch(
            "alpaca_connectors.runtime.use_markets_api", return_value=SimpleNamespace(client=client)
        ),
        patch(
            "msm_portfolios.api.market_metadata.SignalMetadata.filter", return_value=rows
        ) as read,
    ):
        markets_api.account_delete_preflight(ACCOUNT_UID)
        ended = markets_api.end_signals(["signal-a", "signal-b", "signal-c"])

    selection = {"selection": {"mode": "explicit", "uids": [ACCOUNT_UID]}, "options": {}}
    # One read of the signals' metadata; ending a signal is a one-signal recipe, and a signal
    # Markets never registered costs no call.
    read.assert_called_once_with(signal_uid__in=["signal-a", "signal-b", "signal-c"], limit=3)
    assert client.request.call_args_list == [
        call("POST", "/api/v1/account/bulk-delete/preflight/", json=selection),
    ]
    # The two run together, so in no particular order.
    assert sorted(recipes.calls, key=lambda made: made[1]["signal_uid"]) == [
        ("end_signal", {"signal_uid": "signal-a"}),
        ("end_signal", {"signal_uid": "signal-b"}),
    ]
    assert ended == {"signal-a": {"deleted": "signal-a"}, "signal-b": {"deleted": "signal-b"}}


def test_several_portfolios_are_preflighted_and_deleted_in_one_request_each() -> None:
    from alpaca_connectors import markets_api

    held = "Account target rows name this portfolio (count=2)."
    missing = uuid.UUID(int=77)
    client = MagicMock()
    # ms-markets 4.2.0's preflight: each portfolio with its own blockers, and the request's
    # blockers labelled by unique identifier, or by uid for one it did not find.
    client.request.side_effect = [
        {
            "allowed": False,
            "blockers": [
                f"Portfolio 'ALPACA_PORTFOLIO__KEPT': {held}",
                f"Portfolio {missing} was not found.",
            ],
            "portfolios": [
                {"uid": str(PORTFOLIO), "unique_identifier": "ALPACA_PORTFOLIO__P", "blockers": []},
                {
                    "uid": str(KEPT_PORTFOLIO),
                    "unique_identifier": "ALPACA_PORTFOLIO__KEPT",
                    "blockers": [held],
                },
            ],
        },
        {"deleted_count": 1, "deleted": [], "failed": []},
    ]
    with patch(
        "alpaca_connectors.runtime.use_markets_api", return_value=SimpleNamespace(client=client)
    ):
        blockers = markets_api.portfolios_delete_blockers([PORTFOLIO, KEPT_PORTFOLIO, missing])
        markets_api.delete_portfolios([str(PORTFOLIO)])

    # Each blocker goes to its portfolio; the others are not blocked by it.
    assert blockers[str(PORTFOLIO)] == []
    assert blockers[str(KEPT_PORTFOLIO)] == [held]
    assert blockers[str(missing)] == [f"Portfolio {missing} was not found."]
    both = [str(PORTFOLIO), str(KEPT_PORTFOLIO), str(missing)]
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
    client.request.return_value = {
        "blockers": ["You may not write the table it reaches."],
        "portfolios": [
            {"uid": str(uid), "unique_identifier": f"P{index}", "blockers": []}
            for index, uid in enumerate((PORTFOLIO, KEPT_PORTFOLIO))
        ],
    }
    with patch(
        "alpaca_connectors.runtime.use_markets_api", return_value=SimpleNamespace(client=client)
    ):
        blockers = markets_api.portfolios_delete_blockers([PORTFOLIO, KEPT_PORTFOLIO])
    assert all(blockers.values())


def test_one_portfolio_is_deleted_through_the_route() -> None:
    # Portfolio.delete deletes nothing since ms-markets 4.2.0; the route deletes its datasets.
    from alpaca_connectors import markets_api

    client = MagicMock()
    with patch(
        "alpaca_connectors.runtime.use_markets_api", return_value=SimpleNamespace(client=client)
    ):
        markets_api.delete_portfolio(str(PORTFOLIO))
    client.request.assert_called_once_with("DELETE", f"/api/v1/portfolio/{PORTFOLIO}/")


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
