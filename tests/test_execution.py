"""Order execution on a registered paper account against a fake Alpaca client (ADR 0016).

The fake client behaves like ``alpaca.trading.client.TradingClient`` for the calls the execution
makes; the fake ledger keeps the ``OrderManager`` rows in memory and validates every ``OrdersTS``
and ``TradesTS`` frame against the real ms-markets storage contracts.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pandas as pd
import pytest
from alpaca.common.exceptions import APIError
from msm.data_nodes.execution import Orders, Trades

from alpaca_connectors.account.credentials import AlpacaCredentialsRejectedError
from alpaca_connectors.execution import (
    ExecutionDependencies,
    OrderBatchSubmission,
    OrderLegSpec,
    PreTradeCheckError,
    cancel_order_batch,
    list_account_orders,
    map_alpaca_order_status,
    preflight_order_batch,
    submit_order_batch,
    sync_order_batch,
)
from alpaca_connectors.execution.orders import OrderRecord, batch_status

NOW = dt.datetime(2026, 10, 26, 15, 0, tzinfo=dt.timezone.utc)
ACCOUNT_UID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
OTHER_ACCOUNT_UID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
ALPACA_ACCOUNT_ID = "alpaca-account-1"
AAPL_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
MSFT_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
ZZZ_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
NEW_ID = uuid.UUID("44444444-4444-4444-8444-444444444444")
AAPL = f"ALPACA::{AAPL_ID}"
MSFT = f"ALPACA::{MSFT_ID}"
ZZZ = f"ALPACA::{ZZZ_ID}"
NEW = f"ALPACA::{NEW_ID}"
REGISTRATION = {
    "uid": ACCOUNT_UID,
    "account_uid": ACCOUNT_UID,
    "unique_identifier": "PA3ABC__ALPACA_PAPER",
    "alpaca_account_id": ALPACA_ACCOUNT_ID,
    "is_paper": True,
    "account_is_active": True,
}
LOAD_REGISTRATION = "alpaca_connectors.execution.services.load_account_registration"


def alpaca_error(status_code: int, message: str, code: int = 40010001) -> APIError:
    return APIError(
        json.dumps({"code": code, "message": message}),
        http_error=SimpleNamespace(response=SimpleNamespace(status_code=status_code), request=None),
    )


def asset(asset_id: uuid.UUID, symbol: str, *, tradable=True, fractionable=True, status="active"):
    return SimpleNamespace(
        id=asset_id,
        symbol=symbol,
        tradable=tradable,
        fractionable=fractionable,
        status=SimpleNamespace(value=status),
    )


def account(**changes: Any) -> SimpleNamespace:
    values = {
        "id": ALPACA_ACCOUNT_ID,
        "status": SimpleNamespace(value="ACTIVE"),
        "buying_power": "10000",
        "cash": "5000",
        "trading_blocked": False,
        "account_blocked": False,
        "trade_suspended_by_user": False,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def clock(is_open: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        is_open=is_open,
        next_open=NOW + dt.timedelta(hours=18),
        next_close=NOW + dt.timedelta(hours=5),
    )


class FakeTradingClient:
    """The slice of ``TradingClient`` the execution uses, over in-memory orders."""

    def __init__(self, *, account=None, clock_state=None, assets=None):
        self.account = account or globals()["account"]()
        self.clock = clock_state or clock()
        self.assets = {}
        for item in assets or [
            asset(AAPL_ID, "AAPL"),
            asset(MSFT_ID, "MSFT", fractionable=False),
            asset(ZZZ_ID, "ZZZ", tradable=False),
            asset(NEW_ID, "NEWC"),
        ]:
            self.assets[str(item.id)] = item
            self.assets[item.symbol] = item
        self.orders: dict[str, SimpleNamespace] = {}
        self.submitted: list[str] = []
        self.cancelled: list[str] = []
        self.reject: dict[str, APIError] = {}
        self.fill_on_lookup: dict[str, float] = {}
        self.lookups = 0

    def get_account(self):
        return self.account

    def get_clock(self):
        return self.clock

    def get_asset(self, key):
        item = self.assets.get(str(key))
        if item is None:
            raise alpaca_error(404, "asset not found", 40410000)
        return item

    def get_order_by_client_id(self, client_order_id):
        self.lookups += 1
        order = self.orders.get(client_order_id)
        if order is None:
            raise alpaca_error(404, "order not found", 40410000)
        if client_order_id in self.fill_on_lookup:
            self.fill(client_order_id, self.fill_on_lookup.pop(client_order_id))
        return order

    def submit_order(self, request):
        client_order_id = request.client_order_id
        if client_order_id in self.reject:
            raise self.reject[client_order_id]
        if client_order_id in self.orders:
            raise alpaca_error(422, "client_order_id must be unique")
        item = self.assets[request.symbol]
        order = SimpleNamespace(
            id=uuid.uuid4(),
            client_order_id=client_order_id,
            status="accepted",
            qty=str(request.qty),
            filled_qty="0",
            filled_avg_price=None,
            submitted_at=NOW,
            updated_at=NOW,
            filled_at=None,
            canceled_at=None,
            expired_at=None,
            failed_at=None,
            symbol=request.symbol,
            asset_id=item.id,
            side=request.side,
            time_in_force=request.time_in_force,
            requested_qty=request.qty,
        )
        self.orders[client_order_id] = order
        self.submitted.append(client_order_id)
        return order

    def fill(self, client_order_id, price, quantity=None):
        order = self.orders[client_order_id]
        filled = float(quantity if quantity is not None else order.qty)
        order.filled_qty = str(filled)
        order.filled_avg_price = str(price)
        order.status = "filled" if filled >= float(order.qty) else "partially_filled"
        order.filled_at = NOW + dt.timedelta(seconds=2)
        order.updated_at = NOW + dt.timedelta(seconds=2)

    def cancel_order_by_id(self, order_id):
        for order in self.orders.values():
            if str(order.id) == str(order_id):
                order.status = "canceled"
                order.canceled_at = NOW + dt.timedelta(seconds=3)
                order.updated_at = NOW + dt.timedelta(seconds=3)
                self.cancelled.append(str(order_id))
                return
        raise alpaca_error(404, "order not found", 40410000)


class FakeLedger:
    """In-memory ``OrderManager`` rows; every frame is validated against the storage contract."""

    def __init__(self, registered: set[str] | None = None):
        self.batches: dict[str, SimpleNamespace] = {}
        self.order_rows: list[dict[str, Any]] = []
        self.trade_rows: list[dict[str, Any]] = []
        self.registered = registered if registered is not None else {AAPL, MSFT, ZZZ}
        self.cash_ensured = 0

    def get_batch(self, batch_id):
        return self.batches.get(batch_id)

    def list_batches(self, account_uid, *, limit=500):
        return [
            row for row in self.batches.values() if str(row.target_account_uid) == str(account_uid)
        ][:limit]

    def create_batch(self, *, batch_id, account_uid, target_time, status, metadata):
        row = SimpleNamespace(
            uid=uuid.uuid4(),
            unique_identifier=batch_id,
            target_account_uid=account_uid,
            target_time=target_time,
            status=status,
            execution_end=None,
            metadata_json=metadata,
        )
        self.batches[batch_id] = row
        return row

    def update_batch(self, batch_uid, **values):
        row = next(row for row in self.batches.values() if row.uid == batch_uid)
        for key, value in values.items():
            setattr(row, key, value)

    def registered_asset_identifiers(self, identifiers):
        return set(identifiers) & self.registered

    def ensure_cash_asset(self):
        self.cash_ensured += 1
        return "USD"

    def write_orders(self, rows):
        Orders.validate_execution_frame(pd.DataFrame(rows))
        self.order_rows.extend(rows)

    def write_trades(self, rows):
        Trades.validate_execution_frame(pd.DataFrame(rows))
        self.trade_rows.extend(rows)


class Harness:
    def __init__(self, *, client=None, ledger=None, prices=None):
        self.client = client or FakeTradingClient()
        self.ledger = ledger or FakeLedger()
        self.prices = {"AAPL": 200.0, "MSFT": 400.0, "ZZZ": 1.0, "NEWC": 10.0}
        if prices is not None:
            self.prices = prices
        self.captures = 0
        self.sleeps: list[float] = []
        self.deps = ExecutionDependencies(
            client=self.client,
            ledger=self.ledger,
            price_reader=lambda symbols: {s: self.prices[s] for s in symbols if s in self.prices},
            capture_holdings=self._capture,
            now=lambda: NOW,
            sleep=self.sleeps.append,
            settle_timeout_seconds=2.0,
            poll_interval_seconds=1.0,
        )

    def _capture(self):
        self.captures += 1
        return SimpleNamespace(holdings_set_uid=f"set-{self.captures}")


def leg(client_order_id, identifier, symbol, side, quantity) -> OrderLegSpec:
    return OrderLegSpec(client_order_id, identifier, symbol, side, quantity)


def submission(*legs: OrderLegSpec, batch_id="HUNCH_BATCH__1", **kwargs) -> OrderBatchSubmission:
    return OrderBatchSubmission(batch_id=batch_id, legs=tuple(legs), **kwargs)


@pytest.fixture
def registration():
    with patch(LOAD_REGISTRATION, return_value=dict(REGISTRATION)) as loader:
        yield loader


def test_submit_and_sync_round_trip_on_a_paper_account(registration) -> None:
    harness = Harness()
    harness.client.fill_on_lookup["c-sell"] = 401.0
    request = submission(
        leg("c-buy", AAPL, "AAPL", "buy", 1.5),
        leg("c-sell", MSFT, "MSFT", "sell", 2),
    )

    result = submit_order_batch(ACCOUNT_UID, request, deps=harness.deps)

    # Sells are placed first; the buy waits for them to settle (one poll filled the sell).
    assert harness.client.submitted == ["c-sell", "c-buy"]
    assert result.accepted is True and result.batch_id == "HUNCH_BATCH__1"
    by_id = {order.client_order_id: order for order in result.orders}
    assert [order.client_order_id for order in result.orders] == ["c-buy", "c-sell"]
    assert by_id["c-sell"].status == "filled"
    assert by_id["c-sell"].filled_quantity == 2.0 and by_id["c-sell"].average_fill_price == 401.0
    assert by_id["c-buy"].status == "submitted" and by_id["c-buy"].broker_order_id
    assert by_id["c-buy"].filled_quantity == 0.0 and by_id["c-buy"].average_fill_price is None
    assert result.status == "partially_filled"
    assert harness.client.orders["c-sell"].requested_qty == 2.0
    assert harness.client.orders["c-buy"].requested_qty == 1.5
    # The ledger: one batch, one OrdersTS row per order, one TradesTS row for the fill.
    batch = harness.ledger.batches["HUNCH_BATCH__1"]
    assert str(batch.target_account_uid) == ACCOUNT_UID and batch.status == "partially_filled"
    assert batch.execution_end is None
    assert {row["order_identifier"] for row in harness.ledger.order_rows} == {"c-buy", "c-sell"}
    assert all(
        row["order_manager_identifier"] == "HUNCH_BATCH__1" for row in harness.ledger.order_rows
    )
    assert all(
        row["account_identifier"] == "PA3ABC__ALPACA_PAPER" for row in harness.ledger.order_rows
    )
    sell_row = next(row for row in harness.ledger.order_rows if row["order_identifier"] == "c-sell")
    assert sell_row["order_side"] == -1 and sell_row["filled_price"] == 401.0
    assert [row["trade_identifier"] for row in harness.ledger.trade_rows] == ["c-sell:fill"]
    assert harness.ledger.trade_rows[0]["settlement_asset_identifier"] == "USD"
    assert harness.ledger.cash_ensured == 1
    # A holdings snapshot follows the fill, once.
    assert harness.captures == 1 and result.holdings_set_uid == "set-1"
    assert json.dumps(batch.metadata_json)  # JSON-safe
    assert [o["client_order_id"] for o in batch.metadata_json["orders"]] == ["c-buy", "c-sell"]

    harness.client.fill("c-buy", 199.5)
    synced = sync_order_batch(ACCOUNT_UID, "HUNCH_BATCH__1", deps=harness.deps)

    assert synced.status == "filled"
    assert {o.client_order_id: o.status for o in synced.orders} == {
        "c-buy": "filled",
        "c-sell": "filled",
    }
    assert batch.status == "filled" and batch.execution_end == NOW
    assert {row["trade_identifier"] for row in harness.ledger.trade_rows} == {
        "c-sell:fill",
        "c-buy:fill",
    }
    assert harness.captures == 2 and synced.holdings_set_uid == "set-2"

    again = sync_order_batch(ACCOUNT_UID, "HUNCH_BATCH__1", deps=harness.deps)
    assert again.status == "filled" and harness.captures == 2
    assert again.holdings_set_uid == "set-2"

    rows, total = list_account_orders(ACCOUNT_UID, batch_id="HUNCH_BATCH__1", ledger=harness.ledger)
    assert total == 2 and {row["status"] for row in rows} == {"filled"}
    assert rows[0]["batch_id"] == "HUNCH_BATCH__1" and rows[0]["batch_status"] == "filled"
    rows, total = list_account_orders(ACCOUNT_UID, limit=1, offset=0, ledger=harness.ledger)
    assert total == 2 and len(rows) == 1
    assert list_account_orders(
        OTHER_ACCOUNT_UID, batch_id="HUNCH_BATCH__1", ledger=harness.ledger
    ) == ([], 0)


def test_resubmitting_a_batch_never_places_an_order_twice(registration) -> None:
    harness = Harness()
    request = submission(leg("c-buy", AAPL, "AAPL", "buy", 1))

    first = submit_order_batch(ACCOUNT_UID, request, deps=harness.deps)
    second = submit_order_batch(ACCOUNT_UID, request, deps=harness.deps)

    assert harness.client.submitted == ["c-buy"]
    assert second.orders[0].broker_order_id == first.orders[0].broker_order_id
    assert len(harness.ledger.batches) == 1


def test_an_order_alpaca_refuses_is_recorded_as_rejected(registration) -> None:
    harness = Harness()
    harness.client.reject["c-buy"] = alpaca_error(403, "insufficient buying power", 40310000)
    request = submission(
        leg("c-buy", AAPL, "AAPL", "buy", 1),
        leg("c-buy-2", AAPL, "AAPL", "buy", 1),
    )

    result = submit_order_batch(ACCOUNT_UID, request, deps=harness.deps)

    by_id = {order.client_order_id: order for order in result.orders}
    assert by_id["c-buy"].status == "rejected" and by_id["c-buy"].broker_order_id is None
    assert by_id["c-buy"].message == "insufficient buying power"
    assert by_id["c-buy-2"].status == "submitted"
    assert result.message == "1 of 2 orders were refused by Alpaca at submission."
    rejected_row = next(r for r in harness.ledger.order_rows if r["order_identifier"] == "c-buy")
    assert rejected_row["status"] == "rejected" and rejected_row["order_remote_id"] == ""
    assert harness.captures == 0

    # A retry leaves the refused order to Alpaca again and keeps the accepted one.
    harness.client.reject.clear()
    retried = submit_order_batch(ACCOUNT_UID, request, deps=harness.deps)
    assert {o.status for o in retried.orders} == {"submitted"}
    assert harness.client.submitted == ["c-buy-2", "c-buy"]


def test_cancel_requests_cancellation_of_open_orders(registration) -> None:
    harness = Harness()
    harness.client.fill_on_lookup["c-sell"] = 401.0
    submit_order_batch(
        ACCOUNT_UID,
        submission(leg("c-sell", MSFT, "MSFT", "sell", 1), leg("c-buy", AAPL, "AAPL", "buy", 1)),
        deps=harness.deps,
    )

    result = cancel_order_batch(ACCOUNT_UID, "HUNCH_BATCH__1", deps=harness.deps)

    buy = harness.client.orders["c-buy"]
    assert harness.client.cancelled == [str(buy.id)]
    by_id = {order.client_order_id: order for order in result.orders}
    assert by_id["c-buy"].status == "cancelled" and by_id["c-sell"].status == "filled"
    assert (
        result.status == "partially_filled"
        and result.message == "Cancellation requested for 1 orders."
    )
    assert harness.ledger.batches["HUNCH_BATCH__1"].execution_end == NOW


def test_pre_trade_checks_refuse_the_batch_before_any_order(registration) -> None:
    harness = Harness()

    def refused(*legs, **kwargs):
        placed_before = list(harness.client.submitted)
        with pytest.raises(PreTradeCheckError) as info:
            submit_order_batch(ACCOUNT_UID, submission(*legs, **kwargs), deps=harness.deps)
        assert harness.client.submitted == placed_before
        assert "HUNCH_BATCH__1" not in harness.ledger.batches or kwargs.get("queue_when_closed")
        return info.value

    error = refused(leg("a", MSFT, "MSFT", "buy", 1.5))
    assert error.code == "fractional_quantity_not_allowed"
    assert error.checks[0].client_order_id == "a"

    error = refused(leg("a", ZZZ, "ZZZ", "buy", 1))
    assert error.code == "asset_not_tradable"

    error = refused(leg("a", "BBG000B9XRY4", "AAPL", "buy", 1))
    assert error.code == "unknown_asset"

    error = refused(leg("a", NEW, "NEWC", "buy", 1))
    assert error.code == "asset_not_registered"

    error = refused(leg("a", AAPL, "AAPL", "buy", 100))
    assert error.code == "insufficient_buying_power"
    assert "20,000.00" in str(error) and "10,000.00" in str(error)

    error = refused(leg("a", AAPL, "AAPL", "buy", 1), leg("a", AAPL, "AAPL", "sell", 1))
    assert error.code == "duplicate_client_order_id"

    error = refused(leg("a", MSFT, "MSFT", "buy", 1.5), leg("b", ZZZ, "ZZZ", "buy", 1))
    assert error.code == "pre_trade_checks_failed"
    assert [check.code for check in error.checks] == [
        "fractional_quantity_not_allowed",
        "asset_not_tradable",
    ]

    harness.client.clock = clock(is_open=False)
    error = refused(leg("a", AAPL, "AAPL", "buy", 1))
    assert error.code == "market_closed" and "queue_when_closed" in str(error)
    queued = submit_order_batch(
        ACCOUNT_UID,
        submission(leg("a", AAPL, "AAPL", "buy", 1), batch_id="QUEUED", queue_when_closed=True),
        deps=harness.deps,
    )
    assert queued.orders[0].status == "submitted" and harness.client.submitted == ["a"]
    harness.client.clock = clock(is_open=True)

    harness.client.account = account(trading_blocked=True)
    error = refused(leg("b", AAPL, "AAPL", "buy", 1))
    assert error.code == "account_not_tradable"
    harness.client.account = account(status=SimpleNamespace(value="INACTIVE"))
    error = refused(leg("b", AAPL, "AAPL", "buy", 1))
    assert error.code == "account_not_tradable"

    harness.client.account = account()
    with patch(LOAD_REGISTRATION, return_value={**REGISTRATION, "account_is_active": False}):
        error = refused(leg("b", AAPL, "AAPL", "buy", 1))
    assert error.code == "account_inactive"

    harness.client.account = account(id="someone-else")
    with pytest.raises(ValueError, match="different Alpaca account"):
        submit_order_batch(
            ACCOUNT_UID, submission(leg("b", AAPL, "AAPL", "buy", 1)), deps=harness.deps
        )


def test_buying_power_nets_sells_and_warns_when_unpriced(registration) -> None:
    harness = Harness(prices={"AAPL": 200.0, "MSFT": 400.0})
    # 100 AAPL (20,000) net of 30 MSFT (12,000) needs 8,000 of the 10,000 buying power.
    request = submission(leg("s", MSFT, "MSFT", "sell", 30), leg("b", AAPL, "AAPL", "buy", 100))
    preflight = preflight_order_batch(ACCOUNT_UID, request, deps=harness.deps)
    assert preflight.allowed is True and preflight.warnings == []
    payload = preflight.as_payload()
    assert payload["contract"] == "command-center.bulk_action_preflight@v1"
    assert payload["allowed"] is True and payload["matched_count"] == 2 and payload["checks"] == []

    harness.prices = {"MSFT": 400.0}
    preflight = preflight_order_batch(ACCOUNT_UID, request, deps=harness.deps)
    assert preflight.allowed is True
    assert preflight.warnings == [
        "Buying power was not estimated for b: no latest trade price. Alpaca decides at submission."
    ]

    blocked = preflight_order_batch(
        ACCOUNT_UID, submission(leg("a", MSFT, "MSFT", "buy", 1.5)), deps=harness.deps
    )
    assert blocked.allowed is False
    assert blocked.as_payload()["checks"] == [
        {
            "code": "fractional_quantity_not_allowed",
            "message": "MSFT trades in whole shares only.",
            "client_order_id": "a",
        }
    ]
    assert harness.client.submitted == [] and harness.ledger.batches == {}


def test_a_symbol_that_differs_at_alpaca_is_placed_under_the_venue_symbol(registration) -> None:
    harness = Harness()
    result = submit_order_batch(
        ACCOUNT_UID, submission(leg("a", AAPL, "APPL", "buy", 1)), deps=harness.deps
    )
    assert harness.client.orders["a"].symbol == "AAPL"
    assert result.orders[0].symbol == "APPL" and result.orders[0].venue_symbol == "AAPL"
    assert result.warnings == [
        "a: Alpaca lists the asset as AAPL; the order is placed under that symbol."
    ]


def test_a_batch_id_of_another_account_is_refused(registration) -> None:
    harness = Harness()
    harness.ledger.create_batch(
        batch_id="HUNCH_BATCH__1",
        account_uid=OTHER_ACCOUNT_UID,
        target_time=NOW,
        status="submitted",
        metadata={"orders": []},
    )
    with pytest.raises(PreTradeCheckError) as info:
        submit_order_batch(
            ACCOUNT_UID, submission(leg("a", AAPL, "AAPL", "buy", 1)), deps=harness.deps
        )
    assert info.value.code == "batch_id_in_use"
    with pytest.raises(LookupError):
        sync_order_batch(ACCOUNT_UID, "HUNCH_BATCH__1", deps=harness.deps)
    with pytest.raises(LookupError):
        cancel_order_batch(ACCOUNT_UID, "missing", deps=harness.deps)


def test_rejected_credentials_surface_as_the_fixed_error(registration) -> None:
    harness = Harness()

    def unauthorized():
        raise alpaca_error(401, "unauthorized", 40110000)

    harness.client.get_account = unauthorized
    with pytest.raises(AlpacaCredentialsRejectedError):
        submit_order_batch(
            ACCOUNT_UID, submission(leg("a", AAPL, "AAPL", "buy", 1)), deps=harness.deps
        )


def test_an_unknown_account_is_not_found() -> None:
    with (
        patch("alpaca_connectors.account.services.get_account_registration", return_value=None),
        patch("alpaca_connectors.runtime.start_markets_engine"),
    ):
        with pytest.raises(LookupError):
            list_account_orders(ACCOUNT_UID, ledger=FakeLedger())


@pytest.mark.parametrize(
    ("alpaca_status", "filled", "quantity", "expected"),
    [
        ("new", 0, 1, "submitted"),
        ("accepted", 0, 1, "submitted"),
        ("pending_new", 0, 1, "submitted"),
        ("pending_cancel", 0.5, 1, "submitted"),
        ("partially_filled", 0.5, 1, "partially_filled"),
        ("filled", 1, 1, "filled"),
        ("canceled", 0.5, 1, "cancelled"),
        ("expired", 0, 1, "expired"),
        ("done_for_day", 1, 1, "filled"),
        ("done_for_day", 0.5, 1, "expired"),
        ("rejected", 0, 1, "rejected"),
        ("suspended", 0, 1, "failed"),
        ("something_new", 0, 1, "submitted"),
    ],
)
def test_alpaca_statuses_map_to_the_contract(alpaca_status, filled, quantity, expected) -> None:
    status = SimpleNamespace(value=alpaca_status)
    assert map_alpaca_order_status(status, filled_quantity=filled, quantity=quantity) == expected


def test_batch_status_summarises_the_orders() -> None:
    def record(status, filled=0.0):
        return OrderRecord("c", AAPL, "AAPL", "buy", 1.0, status=status, filled_quantity=filled)

    assert batch_status([]) == "failed"
    assert batch_status([record("submitted")]) == "submitted"
    assert batch_status([record("submitted"), record("filled", 1.0)]) == "partially_filled"
    assert batch_status([record("filled", 1.0)]) == "filled"
    assert batch_status([record("filled", 1.0), record("rejected")]) == "partially_filled"
    assert batch_status([record("rejected"), record("rejected")]) == "rejected"
    assert batch_status([record("cancelled"), record("expired")]) == "failed"
