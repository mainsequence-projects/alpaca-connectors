"""The account life cycle end to end, on a real Alpaca paper account and a local stack (ADR 0023).

Nothing here runs unless ``ALPACA_E2E=1``. The stages run in file order and share one state:

1. register the account with the paper keys of ``.env``;
2. sync it with no target: only the capture runs;
3. create two fixed-weight portfolios, AAPL and NVDA, and JPM and NVDA;
4. state the account's exposure to both: the target write creates the virtual funds;
5. sync: every exposure is sized into units and the snapshot is allocated;
6. rebalance: trade the deltas, and see the fills allocated to the funds;
7. a portfolio with no weights fails the sizing; new weights and a lower exposure are sized again
   and traded;
8. tear down: clear the target and delete the account. Positions the test bought are left.

The market may be closed. Orders are then queued for the next session, checked as accepted and
cancelled again, and the checks that need fills are left out. Everything else runs the same. The
account may already hold positions from an earlier run; no check assumes it holds cash alone.

It needs, all on this machine: the Markets API on the ms-markets release of its ADR 0060
(``MARKETS_API_URL``), the local MetaTables runtime with both migrations applied, this API under
uvicorn with ``ALPACA_CONNECTORS_DATA_KEY``, and a signed-in ``mainsequence`` CLI.

Run it with ``ALPACA_E2E=1 .venv/bin/python -m pytest tests/test_live_account_lifecycle.py -x -v``.
"""

from __future__ import annotations

import datetime as dt
import math
import os
import subprocess
import time
import uuid
import warnings
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("ALPACA_E2E") != "1",
    reason="The live end-to-end test runs only with ALPACA_E2E=1 (see the module docstring).",
)

ROOT = Path(__file__).resolve().parents[1]
API_URL = os.environ.get("ALPACA_E2E_API_URL", "http://127.0.0.1:8321").rstrip("/")
ACCOUNT_NAME = "Alpaca end-to-end paper account"
SYMBOLS = ("AAPL", "NVDA", "JPM")
PORTFOLIO_1 = "ALPACA_E2E_FIXED_AAPL_NVDA"
PORTFOLIO_2 = "ALPACA_E2E_FIXED_JPM_NVDA"
PORTFOLIO_WITHOUT_WEIGHTS = "ALPACA_E2E_NO_WEIGHTS"
CALENDAR = "ALPACA_E2E_NYSE"
WEIGHTS = {
    PORTFOLIO_1: {"AAPL": 0.5, "NVDA": 0.5},
    PORTFOLIO_2: {"JPM": 0.5, "NVDA": 0.5},
}
REWEIGHTED = {PORTFOLIO_1: {"AAPL": 0.7, "NVDA": 0.3}}
# Portfolio 1 is a share of NAV and portfolio 2 an amount, so both kinds of exposure are sized.
SHARE_OF_NAV = 0.30
LOWER_SHARE_OF_NAV = 0.15
AMOUNT = 20_000.0
# A sized target is checked against this test's own read of equity and prices, taken seconds
# apart from the sync's, so the comparison allows for that drift.
SIZING_TOLERANCE = 0.03
FILL_TIMEOUT_SECONDS = 120.0

STATE: dict[str, Any] = {}


# ------------------------------------------------------------------------------------------------
# Plumbing
# ------------------------------------------------------------------------------------------------
def _env_file() -> dict[str, str]:
    values: dict[str, str] = {}
    path = ROOT / ".env"
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        name, separator, value = line.partition("=")
        if separator and not name.lstrip().startswith("#"):
            values[name.strip()] = value.strip().strip("'\"")
    return values


def _setting(name: str) -> str:
    value = os.environ.get(name) or _env_file().get(name) or ""
    if not value:
        pytest.fail(f"{name} is not set in the environment or in .env.")
    return value


def need(*names: str) -> None:
    """Skip a stage whose earlier stages did not leave what it needs."""
    missing = [name for name in names if name not in STATE]
    if missing:
        pytest.skip(f"An earlier stage did not complete (missing: {', '.join(missing)}).")


def delete_account(api, account_uid: str):
    """Delete an account with the exact confirmation the route takes (ADR 0028, decision 8)."""
    return api.delete(f"/v1/accounts/{account_uid}", params={"confirm": f"DELETE {account_uid}"})


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(microsecond=0)


def next_second() -> dt.datetime:
    """A time strictly after every target, weight and snapshot written so far."""
    time.sleep(1.1)
    return now()


@pytest.fixture(scope="module")
def api():
    """The local API, called over HTTP as the signed-in person."""
    import httpx

    for name, value in _env_file().items():
        os.environ.setdefault(name, value)

    class SignedIn(httpx.Auth):
        """The signed-in person's access token, asked from the CLI again before it goes stale."""

        def __init__(self) -> None:
            self.token, self.read_at = "", 0.0

        def auth_flow(self, request):
            fixed = os.environ.get("ALPACA_E2E_TOKEN")
            if fixed:
                self.token = fixed
            elif not self.token or time.monotonic() - self.read_at > 20:
                self.token = subprocess.run(
                    [str(ROOT / ".venv/bin/mainsequence"), "auth", "token"],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout.strip()
                self.read_at = time.monotonic()
            request.headers["Authorization"] = f"Bearer {self.token}"
            yield request

    with httpx.Client(base_url=API_URL, auth=SignedIn(), timeout=180.0) as client:
        try:
            health = client.get("/health")
        except httpx.HTTPError as exc:
            pytest.fail(f"The API is not reachable at {API_URL}: {type(exc).__name__}.")
        assert health.status_code == 200, f"GET /health answered {health.status_code}."
        yield client


@pytest.fixture
def markets():
    """The Markets API client this process's row calls use.

    Built for each stage: the suite's fixtures reset the process's Markets backend between tests.
    """
    if not os.environ.get("MARKETS_API_URL") and not _env_file().get("MARKETS_API_URL"):
        pytest.fail("MARKETS_API_URL is not set: point it at the local Markets API on 3.7.")
    from alpaca_connectors.runtime import start_markets_engine, use_markets_api
    from metatables import configure_local_client

    # The laptop's local MetaTables API and its token, as the API process uses them.
    configure_local_client()
    start_markets_engine()
    return use_markets_api().client


@pytest.fixture(scope="module")
def alpaca():
    """Alpaca read directly with the paper keys: the test's own source of truth."""
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockLatestTradeRequest
    from alpaca.trading.client import TradingClient

    key, secret = _setting("ALPACA_PAPER_API_KEY"), _setting("ALPACA_PAPER_SECRET_KEY")
    trading = TradingClient(key, secret, paper=True)
    data = StockHistoricalDataClient(key, secret)

    class Alpaca:
        def equity(self) -> float:
            return float(trading.get_account().equity)

        def market_open(self) -> bool:
            return bool(trading.get_clock().is_open)

        def cancel_open_orders(self, client_order_ids: set[str]) -> int:
            """Cancel the open orders with these client order ids; how many were cancelled."""
            from alpaca.trading.enums import QueryOrderStatus
            from alpaca.trading.requests import GetOrdersRequest

            open_orders = trading.get_orders(
                GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=500)
            )
            mine = [order for order in open_orders if order.client_order_id in client_order_ids]
            for order in mine:
                trading.cancel_order_by_id(order.id)
            return len(mine)

        def positions(self) -> dict[str, float]:
            return {str(p.symbol): float(p.qty) for p in trading.get_all_positions()}

        def prices(self) -> dict[str, float]:
            trades = data.get_stock_latest_trade(
                StockLatestTradeRequest(symbol_or_symbols=list(SYMBOLS))
            )
            return {str(symbol): float(trade.price) for symbol, trade in dict(trades).items()}

    return Alpaca()


def ok(response: Any, *statuses: int) -> dict[str, Any]:
    """The JSON body of an answer with an expected status; a failure shows the public error."""
    expected = statuses or (200,)
    if response.status_code not in expected:
        try:
            detail = response.json().get("detail")
        except Exception:
            detail = None
        code = detail.get("code") if isinstance(detail, dict) else None
        message = detail.get("message") if isinstance(detail, dict) else detail
        pytest.fail(
            f"{response.request.method} {response.request.url.path} answered "
            f"{response.status_code} (code: {code}): {message}"
        )
    return response.json()


def sync(api: Any) -> dict[str, Any]:
    return ok(api.post(f"/v1/accounts/{STATE['account_uid']}/actions/sync"))


def statuses(operation: dict[str, Any]) -> dict[str, str]:
    return {step["key"]: step["status"] for step in operation["steps"]}


def write_weights(weights: dict[str, dict[str, float]], *, at: dt.datetime) -> None:
    """Publish fixed weights as a portfolio run would: Markets' portfolio-weights series at ``at``.

    Markets keeps the series (ADR 0031); this publishes it with ms-markets' series row class
    (ms-markets ADR 0060, decision 3), one portfolio at a time.
    """
    from msm_portfolios.api import PortfolioWeightsSeries

    for portfolio, legs in weights.items():
        PortfolioWeightsSeries.publish(
            portfolio,
            [
                {
                    "time_index": at,
                    "portfolio_identifier": portfolio,
                    "asset_identifier": STATE["identifiers"][symbol],
                    "weight": weight,
                }
                for symbol, weight in legs.items()
            ],
        )


def write_target(markets: Any, exposures: list[dict[str, Any]]) -> Any:
    """State the account's whole exposure at a new time, as an application does."""
    target = markets.add_account_target_positions(
        STATE["account_uid"], target_positions_date=next_second(), positions=exposures
    )
    assert target.position_set_uid
    time.sleep(1.1)  # A snapshot must not be earlier than the target it is allocated against.
    return target


def exposure(portfolio: str, **value: float) -> dict[str, Any]:
    uid = STATE["portfolios"][portfolio]
    return {"target_type": "portfolio", "target_uid": uid, "portfolio_uid": uid, **value}


def fund_lines(operation: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    """The sync's fund lines by (portfolio identifier, symbol)."""
    portfolio_by_uid = {uid: name for name, uid in STATE["portfolios"].items()}
    symbol_by_identifier = {identifier: s for s, identifier in STATE["identifiers"].items()}
    return {
        (
            portfolio_by_uid[line["target_portfolio_uid"]],
            symbol_by_identifier[line["asset_identifier"]],
        ): line
        for line in operation["result"]["lines"]
        if line["owner"] == "fund"
    }


def trade(api: Any, alpaca: Any, quantities: dict[str, float]) -> dict[str, Any] | None:
    """Trade signed quantities per symbol in one batch; the filled batch, or None if none filled.

    With the market open the batch is polled until every order is filled. With the market closed
    the orders are queued for the next session, checked as accepted, and cancelled again so that
    nothing of this run fills later.
    """
    market_open = alpaca.market_open()
    orders = [
        {
            "client_order_id": f"{STATE['run']}:{symbol}:{uuid.uuid4().hex[:6]}",
            "asset_identifier": STATE["identifiers"][symbol],
            "symbol": symbol,
            "side": "buy" if quantity > 0 else "sell",
            "quantity": round(abs(quantity), 9),
            "order_type": "market",
            "time_in_force": "day",
        }
        # Sells first only in the request; the API orders them itself.
        for symbol, quantity in sorted(quantities.items())
        if abs(quantity) >= 1e-9
    ]
    assert orders, "There is nothing to trade."
    batch = {
        "batch_id": f"ALPACA_E2E__{uuid.uuid4().hex[:10]}",
        "queue_when_closed": not market_open,
        "orders": orders,
    }
    base = f"/v1/accounts/{STATE['account_uid']}/orders/actions"
    if not market_open:
        # Without queue_when_closed the pre-trade checks refuse the batch while the market is closed.
        refused = ok(
            api.post(f"{base}/submit/preflight", json={**batch, "queue_when_closed": False})
        )
        assert not refused["allowed"]
        assert "market_closed" in {check["code"] for check in refused["checks"]}
    preflight = ok(api.post(f"{base}/submit/preflight", json=batch))
    assert preflight["allowed"], f"Pre-trade checks refused the batch: {preflight['blockers']}"
    ids = {order["client_order_id"] for order in orders}
    try:
        # The API places the orders at Alpaca before it records the batch, so whatever happens
        # from here on, the orders of this batch that are still open are cancelled at the end.
        result = ok(api.post(f"{base}/submit", json=batch))
        assert result["accepted"], result["message"]
        # An order Alpaca refuses is recorded as rejected, with Alpaca's reason. That is a valid
        # outcome, for example a buy in an asset the account is short. It is reported, not failed.
        refused = {
            order["client_order_id"].split(":")[1]: order.get("message")
            for order in result["orders"]
            if order["status"] == "rejected"
        }
        for symbol, reason in refused.items():
            warnings.warn(f"Alpaca refused the {symbol} order: {reason}", stacklevel=2)
        assert len(refused) < len(orders), f"Alpaca refused every order: {refused}"

        if not market_open:
            assert {order["status"] for order in result["orders"]} <= {"submitted", "rejected"}
            # No fill, so no snapshot was published for this batch.
            assert result["holdings_set_uid"] is None
            result = ok(api.post(f"{base}/cancel", json={"batch_id": batch["batch_id"]}))
            deadline = time.monotonic() + 30
            while (
                any(order["status"] == "submitted" for order in result["orders"])
                and time.monotonic() < deadline
            ):
                time.sleep(1)
                result = ok(api.post(f"{base}/sync", json={"batch_id": batch["batch_id"]}))
            assert {order["status"] for order in result["orders"]} <= {"cancelled", "rejected"}
            return None

        done = {"filled", "rejected", "cancelled", "expired", "failed"}
        deadline = time.monotonic() + FILL_TIMEOUT_SECONDS
        while (
            any(order["status"] not in done for order in result["orders"])
            and time.monotonic() < deadline
        ):
            time.sleep(2)
            result = ok(api.post(f"{base}/sync", json={"batch_id": batch["batch_id"]}))
        assert {order["status"] for order in result["orders"]} <= {"filled", "rejected"}, (
            f"The batch ended {result['status']}."
        )
        return {**result, "refused_symbols": set(refused)}
    finally:
        alpaca.cancel_open_orders(ids)


# ------------------------------------------------------------------------------------------------
# 1. Register
# ------------------------------------------------------------------------------------------------
def test_01_the_account_is_registered_with_the_paper_keys(api, markets) -> None:
    STATE["run"] = uuid.uuid4().hex[:8]
    request = {
        "account_name": ACCOUNT_NAME,
        "environment": "paper",
        "credentials": {
            "source": "managed",
            "api_key": _setting("ALPACA_PAPER_API_KEY"),
            "secret_key": _setting("ALPACA_PAPER_SECRET_KEY"),
        },
    }
    # An earlier run that stopped half way leaves its account; this run starts from none.
    for leftover in ok(api.get("/v1/accounts", params={"limit": 100}))["items"]:
        if leftover.get("account_name") == ACCOUNT_NAME:
            ok(delete_account(api, leftover["uid"]))
    preflight = ok(api.post("/v1/accounts/registration/preflight", json=request))
    assert not preflight.get("blockers"), f"Registration is blocked: {preflight.get('blockers')}"
    created = api.post("/v1/accounts", json=request)
    account = ok(created, 201)
    # Neither key value comes back in any field of the answer.
    assert request["credentials"]["secret_key"] not in created.text
    assert request["credentials"]["api_key"] not in created.text
    STATE["account_uid"] = str(account["uid"])

    again = ok(api.get(f"/v1/accounts/{STATE['account_uid']}"))
    assert str(again["uid"]) == STATE["account_uid"]

    # Registering the same Alpaca account again gives the same Markets account, and Markets keeps
    # that account's target when the connector's registration is removed. Start from no target.
    if markets.account_target_positions(STATE["account_uid"]).positions:
        markets.clear_account_target_positions(
            STATE["account_uid"], target_positions_date=next_second()
        )
        time.sleep(1.1)


# ------------------------------------------------------------------------------------------------
# 2. Sync with no target
# ------------------------------------------------------------------------------------------------
def test_02_a_sync_with_no_target_only_captures(api) -> None:
    need("account_uid")
    operation = sync(api)

    assert operation["status"] == "succeeded"
    assert statuses(operation) == {
        "capture_holdings": "succeeded",
        "size_targets": "skipped",
        "allocate_funds": "skipped",
    }
    result = operation["result"]
    assert result["holdings_set_uid"] and result["holdings_rows"] >= 1  # cash, as USD
    assert (result["position_set_uid"], result["allocation_applied"], result["lines"]) == (
        None,
        False,
        [],
    )
    again = ok(api.get(f"/v1/operations/{operation['operation_uid']}"))
    assert again == operation


# ------------------------------------------------------------------------------------------------
# 3. Two fixed-weight portfolios
# ------------------------------------------------------------------------------------------------
def test_03_two_fixed_weight_portfolios_are_published(api, markets) -> None:
    need("account_uid")
    from msm.api.calendars import Calendar
    from msm.api.portfolios import Portfolio

    from alpaca_connectors.markets_api import assets_by_uid

    registered = ok(
        api.post(
            "/v1/assets/registration/execute",
            json={"account_uid": STATE["account_uid"], "symbols": list(SYMBOLS)},
        )
    )
    uids = registered["assets_by_symbol"]
    assert set(uids) == set(SYMBOLS), f"Not every symbol was registered: {sorted(uids)}"
    assets = assets_by_uid(uids.values())
    STATE["identifiers"] = {
        symbol: str(assets[str(uid)].unique_identifier) for symbol, uid in uids.items()
    }

    today = dt.date.today()
    calendar = Calendar.get_by_unique_identifier(CALENDAR) or Calendar.create_from_pandas_calendar(
        source_identifier="NYSE",
        unique_identifier=CALENDAR,
        display_name="Alpaca end-to-end NYSE",
        # A range with sessions in it: a single weekend day has no schedule to store.
        valid_from=today - dt.timedelta(days=30),
        valid_to=today + dt.timedelta(days=30),
        timezone="America/New_York",
    )
    STATE["portfolios"] = {
        name: str(Portfolio.upsert(unique_identifier=name, calendar_uid=calendar.uid).uid)
        for name in (PORTFOLIO_1, PORTFOLIO_2, PORTFOLIO_WITHOUT_WEIGHTS)
    }
    write_weights(WEIGHTS, at=now())

    from alpaca_connectors.holdings.allocation import read_portfolio_weights

    published = read_portfolio_weights([*WEIGHTS, PORTFOLIO_WITHOUT_WEIGHTS], at=next_second())
    assert published == {
        portfolio: {STATE["identifiers"][symbol]: weight for symbol, weight in legs.items()}
        for portfolio, legs in WEIGHTS.items()
    }


# ------------------------------------------------------------------------------------------------
# 4. Exposure
# ------------------------------------------------------------------------------------------------
def test_04_stating_the_exposure_creates_the_funds(markets) -> None:
    need("account_uid", "portfolios")
    target = write_target(
        markets,
        [
            exposure(PORTFOLIO_1, weight_notional_exposure=SHARE_OF_NAV),
            exposure(PORTFOLIO_2, constant_notional_exposure=AMOUNT),
        ],
    )
    STATE["position_set_uid"] = str(target.position_set_uid)

    read = markets.account_target_positions(STATE["account_uid"])
    assert str(read.position_set_uid) == STATE["position_set_uid"]
    assert {row.portfolio.unique_identifier for row in read.positions} == {PORTFOLIO_1, PORTFOLIO_2}
    # The target write creates each fund (ms-markets ADR 0054). Stage 5 proves it: a fund that is
    # assigned nothing still has its lines there.


# ------------------------------------------------------------------------------------------------
# 5. Sync: size and allocate
# ------------------------------------------------------------------------------------------------
def test_05_a_sync_sizes_the_exposure_and_allocates_the_snapshot(api, markets, alpaca) -> None:
    need("position_set_uid")
    equity, prices = alpaca.equity(), alpaca.prices()
    operation = sync(api)

    assert operation["status"] == "succeeded", operation["error"]
    assert set(statuses(operation).values()) == {"succeeded"}
    result = operation["result"]
    assert result["position_set_uid"] == STATE["position_set_uid"]
    assert result["allocation_applied"] is True

    lines = fund_lines(operation)
    sizes = {PORTFOLIO_1: SHARE_OF_NAV * equity, PORTFOLIO_2: AMOUNT}
    assert set(lines) == {(p, s) for p, legs in WEIGHTS.items() for s in legs}
    for (portfolio, symbol), line in lines.items():
        expected = sizes[portfolio] * WEIGHTS[portfolio][symbol] / prices[symbol]
        assert math.isclose(line["target"], expected, rel_tol=SIZING_TOLERANCE), (
            f"{portfolio} {symbol}: sized {line['target']}, expected about {expected:.4f}"
        )
        assert line["delta"] == pytest.approx(line["target"] - line["assigned"])
        assert 0 <= line["assigned"] <= line["target"] + 1e-6
    # Cash belongs to no fund: it is a direct line with no target.
    direct = [line for line in result["lines"] if line["owner"] == "direct"]
    assert any(line["assigned"] > 0 and line["target"] == 0 for line in direct)

    # Markets recorded the same lines for this snapshot.
    recorded = markets.account_virtual_fund_allocation(
        STATE["account_uid"], valuation_time=dt.datetime.fromisoformat(result["time_index"])
    )
    assert len(recorded.lines) == len(result["lines"])
    STATE["sized"] = operation


def test_05b_every_unit_the_account_holds_is_assigned_once(alpaca) -> None:
    """Per asset, the fund lines and the direct line add up to what the account holds.

    A fill keeps its claim's side even against a holding on the other side (ms-markets ADR 0053,
    rule 4): an account short 2 of an asset a fund targets long gives the fund +2 and the direct
    line -4. So a fund's assigned units are not capped by the holding; only the sum is fixed.
    """
    need("sized")
    held = alpaca.positions()
    symbol_by_identifier = {identifier: s for s, identifier in STATE["identifiers"].items()}
    totals: dict[str, float] = {}
    for line in STATE["sized"]["result"]["lines"]:
        symbol = symbol_by_identifier.get(line["asset_identifier"])
        if symbol is not None:
            totals[symbol] = totals.get(symbol, 0.0) + line["assigned"]
    for symbol in SYMBOLS:
        assert totals.get(symbol, 0.0) == pytest.approx(held.get(symbol, 0.0), abs=1e-6), (
            f"{symbol}: the account holds {held.get(symbol, 0.0)} and its lines add up to "
            f"{totals.get(symbol, 0.0)}"
        )


# ------------------------------------------------------------------------------------------------
# 6. Rebalance
# ------------------------------------------------------------------------------------------------
def test_06_trading_the_deltas_fills_the_funds(api, markets, alpaca) -> None:
    need("sized")
    deltas: dict[str, float] = {}
    for (_, symbol), line in fund_lines(STATE["sized"]).items():
        deltas[symbol] = deltas.get(symbol, 0.0) + line["delta"]
    batch = trade(api, alpaca, deltas)
    if batch is None:
        return  # The market is closed: the orders were queued and cancelled, and nothing filled.

    # The fills publish a snapshot, and that snapshot is allocated like a sync's.
    assert batch["holdings_set_uid"] and batch["holdings_time"]
    assert not [text for text in batch["warnings"] if "allocated" in text]
    recorded = markets.account_virtual_fund_allocation(
        STATE["account_uid"], valuation_time=dt.datetime.fromisoformat(batch["holdings_time"])
    )
    assert recorded.lines, "The snapshot after the fills has no allocation lines."

    operation = sync(api)
    assert operation["status"] == "succeeded", operation["error"]
    lines = fund_lines(operation)
    for (portfolio, symbol), line in lines.items():
        if symbol in batch["refused_symbols"]:
            continue  # Alpaca refused that order, so its delta stays.
        # Every sync sizes again with new prices, so a delta is small, not zero.
        assert abs(line["delta"]) <= SIZING_TOLERANCE * line["target"] + 1e-6, (
            f"{portfolio} {symbol}: delta {line['delta']} of target {line['target']}"
        )
    # NVDA is in both funds: what the account holds of it is split between them.
    held = alpaca.positions()["NVDA"]
    shared = lines[(PORTFOLIO_1, "NVDA")]["assigned"] + lines[(PORTFOLIO_2, "NVDA")]["assigned"]
    assert shared == pytest.approx(held, rel=1e-6)
    STATE["funded"] = True


# ------------------------------------------------------------------------------------------------
# 7. Change and repeat
# ------------------------------------------------------------------------------------------------
def test_07_a_portfolio_without_weights_fails_the_sizing_and_keeps_the_snapshot(
    api, markets
) -> None:
    need("position_set_uid")
    write_target(
        markets,
        [
            exposure(PORTFOLIO_1, weight_notional_exposure=SHARE_OF_NAV),
            exposure(PORTFOLIO_2, constant_notional_exposure=AMOUNT),
            exposure(PORTFOLIO_WITHOUT_WEIGHTS, constant_notional_exposure=1_000.0),
        ],
    )
    operation = sync(api)

    assert (operation["status"], operation["current_step"]) == ("failed", "size_targets")
    assert operation["error"]["code"] == "portfolio_weights_missing"
    assert statuses(operation) == {
        "capture_holdings": "succeeded",
        "size_targets": "failed",
        "allocate_funds": "skipped",
    }
    # The message names no portfolio.
    assert PORTFOLIO_WITHOUT_WEIGHTS not in operation["error"]["message"]
    latest = ok(api.get(f"/v1/accounts/{STATE['account_uid']}/holdings/latest"))
    assert latest["items"], "The snapshot published before the failure should stay."


def test_08_new_weights_and_a_lower_exposure_are_sized_and_traded(api, markets, alpaca) -> None:
    need("sized")
    write_weights(REWEIGHTED, at=next_second())
    target = write_target(
        markets,
        [
            exposure(PORTFOLIO_1, weight_notional_exposure=LOWER_SHARE_OF_NAV),
            exposure(PORTFOLIO_2, constant_notional_exposure=AMOUNT),
        ],
    )
    equity, prices = alpaca.equity(), alpaca.prices()
    operation = sync(api)

    assert operation["status"] == "succeeded", operation["error"]
    assert operation["result"]["position_set_uid"] == str(target.position_set_uid)
    lines = fund_lines(operation)
    for symbol, weight in REWEIGHTED[PORTFOLIO_1].items():
        expected = LOWER_SHARE_OF_NAV * equity * weight / prices[symbol]
        assert math.isclose(
            lines[(PORTFOLIO_1, symbol)]["target"], expected, rel_tol=SIZING_TOLERANCE
        )
    if STATE.get("funded"):
        # Half the exposure with less NVDA: portfolio 1 holds more NVDA than it now targets.
        assert lines[(PORTFOLIO_1, "NVDA")]["delta"] < 0

    deltas: dict[str, float] = {}
    for (_, symbol), line in lines.items():
        deltas[symbol] = deltas.get(symbol, 0.0) + line["delta"]
    batch = trade(api, alpaca, deltas)
    if batch is None:
        return

    after = fund_lines(sync(api))
    for (_, symbol), line in after.items():
        if symbol in batch["refused_symbols"]:
            continue
        assert abs(line["delta"]) <= SIZING_TOLERANCE * line["target"] + 1e-6


# ------------------------------------------------------------------------------------------------
# 8. Tear down
# ------------------------------------------------------------------------------------------------
def test_09_the_target_is_cleared_and_the_account_deleted(api, markets) -> None:
    need("account_uid")
    if "position_set_uid" in STATE:
        markets.clear_account_target_positions(
            STATE["account_uid"], target_positions_date=next_second()
        )
        time.sleep(1.1)
        operation = sync(api)
        assert operation["status"] == "succeeded", operation["error"]
        assert statuses(operation)["size_targets"] == "skipped"

    account_uid = STATE.pop("account_uid")
    ok(delete_account(api, account_uid))
    assert api.get(f"/v1/accounts/{account_uid}").status_code == 404
