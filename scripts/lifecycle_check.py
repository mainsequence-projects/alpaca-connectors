"""Run the account and portfolio life cycle end to end, time every step, and clean up after itself.

    .venv/bin/python scripts/lifecycle_check.py [--keep] [--json PATH]

It drives a real Alpaca paper account through the local stack, the way an application does
(ADR 0023, ADR 0031):

 1. register     the account with the paper keys of ``.env``;
 2. capture      a sync with no target: only the holdings capture runs;
 3. portfolios   two fixed-weight portfolios, AAPL and NVDA, and JPM and NVDA, published in Markets;
 4. exposure     the account's target names both: the write creates the virtual funds;
 5. size         a sync sizes every exposure into units and allocates the snapshot;
 6. assignment   every unit the account holds is assigned to exactly one line;
 7. trade        the deltas are traded and the fills allocated to the funds;
 8. no weights   a portfolio with no weights fails the sizing and keeps the snapshot;
 9. reweight     new weights and a lower exposure are sized and traded again;
10. portfolio Job three portfolios of the account, two first observed at one time and one earlier,
                 each run by the portfolio Job: every one must record weights and values from its
                 own first rebalance, though they share the price and rebalance-state tables.

Locally the portfolio Job runs as its launcher in a subprocess, with this machine's MetaTables and
Markets; with ``--release-uid`` the API starts the deployed Job and the run is polled until it ends.

Cleanup always runs, also after a failure, unless ``--keep``: it cancels this run's open orders,
trades the three symbols back to the positions the account held when the run started (only while
the market is open: a closed market fills nothing), clears the target, deletes the account with its
exact confirmation (its portfolios go with it), deletes stage 10's rebalance configuration, and
deletes the three Markets portfolios and their calendar. Before it starts, it removes what an
earlier run left: the account by name and the three portfolios.

With the market closed the orders are queued, checked as accepted and cancelled, and the checks
that need fills are left out. Everything else runs the same.

It needs, all on this machine: the local MetaTables runtime with the ms-markets and this
application's migrations applied, the Markets API (``MARKETS_API_URL``), this API under uvicorn
with ``ALPACA_CONNECTORS_DATA_KEY`` (``ALPACA_LIFECYCLE_API_URL``, default
``http://127.0.0.1:8321``), a signed-in ``mainsequence`` CLI, and ``ALPACA_PAPER_API_KEY`` and
``ALPACA_PAPER_SECRET_KEY`` in the environment or ``.env``. The keys go only to the account create
route and are never printed.

``--release-uid <uid>`` runs it against that deployed FastAPI release instead, as the signed-in
person: the platform hands over the release's endpoint and token, and Markets calls go to the
Environment's Markets API. It writes in that Environment (an account, a target, three Markets
portfolios and paper orders) and its cleanup removes them.

Every step is timed on this side of the wire: each stage, each call to this API and each Markets
call. The sync's own steps run inside one request, so the request's time is theirs. The report
prints the stages with their calls, the slowest calls, and the total; ``--json`` also writes it.
Exit status 0 means every stage passed and the cleanup completed.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import math
import os
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ACCOUNT_NAME = "Alpaca end-to-end paper account"
SYMBOLS = ("AAPL", "NVDA", "JPM")
PORTFOLIO_1 = "ALPACA_E2E_FIXED_AAPL_NVDA"
PORTFOLIO_2 = "ALPACA_E2E_FIXED_JPM_NVDA"
PORTFOLIO_WITHOUT_WEIGHTS = "ALPACA_E2E_NO_WEIGHTS"
PORTFOLIOS = (PORTFOLIO_1, PORTFOLIO_2, PORTFOLIO_WITHOUT_WEIGHTS)
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
# A sized target is checked against this run's own read of equity and prices, taken seconds
# apart from the sync's, so the comparison allows for that drift.
SIZING_TOLERANCE = 0.03
FILL_TIMEOUT_SECONDS = 120.0
JOB_TIMEOUT_SECONDS = 1800.0
JOB_DONE = {"SUCCEEDED", "FAILED", "ABORTED"}
# Runs the portfolio Job's launcher as the deployed Job does, with the local API's keyring.
LOCAL_JOB = """
import os, runpy, sys
ring = os.environ.get("ALPACA_E2E_LOCAL_KEYRING")
if ring:
    from metatables.encryption import set_keyring_source
    set_keyring_source(lambda name: ring)
launcher = sys.argv[1]
sys.argv = [launcher, "--configuration-uid", sys.argv[2]]
runpy.run_path(launcher, run_name="__main__")
"""
UNITS = 1e-6


class CheckFailed(AssertionError):
    """A stage's check did not hold."""


class Skipped(Exception):
    """A stage whose earlier stages did not leave what it needs."""


def check(condition: Any, message: str) -> None:
    if not condition:
        raise CheckFailed(message)


# ------------------------------------------------------------------------------------------------
# Timing
# ------------------------------------------------------------------------------------------------
@dataclass
class Timed:
    name: str
    seconds: float
    status: str = "passed"
    detail: str | None = None
    calls: list[Timed] = field(default_factory=list)


class Clock:
    """Stages, each with the calls it made, in the order they ran."""

    def __init__(self) -> None:
        self.stages: list[Timed] = []
        self._current: Timed | None = None
        self.started = time.perf_counter()

    @contextlib.contextmanager
    def stage(self, name: str) -> Iterator[Timed]:
        record = Timed(name, 0.0)
        self.stages.append(record)
        self._current, started = record, time.perf_counter()
        try:
            yield record
        except Skipped as exc:
            record.status, record.detail = "skipped", str(exc)
        except Exception as exc:  # noqa: BLE001 - a failed stage is reported, the run goes on
            record.status, record.detail = "failed", f"{type(exc).__name__}: {exc}"
        finally:
            record.seconds = time.perf_counter() - started
            self._current = None

    @contextlib.contextmanager
    def call(self, name: str) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            if self._current is not None:
                self._current.calls.append(Timed(name, time.perf_counter() - started))

    def timed(self, name: str, function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        with self.call(name):
            return function(*args, **kwargs)

    @property
    def total(self) -> float:
        return time.perf_counter() - self.started

    @property
    def ok(self) -> bool:
        return all(stage.status == "passed" for stage in self.stages)


def render(clock: Clock) -> str:
    lines = [f"{'step':<64}{'seconds':>9}  status", "-" * 82]
    for stage in clock.stages:
        lines.append(f"{stage.name:<64}{stage.seconds:>9.2f}  {stage.status}")
        for call in stage.calls:
            lines.append(f"    {call.name[:58]:<60}{call.seconds:>9.2f}")
        if stage.detail:
            lines.append(f"    -> {stage.detail[:300]}")
    calls = sorted(
        ((stage.name, call) for stage in clock.stages for call in stage.calls),
        key=lambda item: item[1].seconds,
        reverse=True,
    )
    lines += ["-" * 82, "slowest calls:"]
    for stage_name, call in calls[:8]:
        lines.append(f"    {call.seconds:>7.2f}  {call.name[:50]}  ({stage_name})")
    in_calls = sum(call.seconds for _, call in calls)
    lines += [
        "-" * 82,
        f"total {clock.total:.2f} s, of which {in_calls:.2f} s in {len(calls)} calls; "
        f"{sum(s.status == 'passed' for s in clock.stages)}/{len(clock.stages)} steps passed",
    ]
    return "\n".join(lines)


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
        raise SystemExit(f"{name} is not set in the environment or in .env.")
    return value


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(microsecond=0)


def next_second() -> dt.datetime:
    """A time strictly after every target, weight and snapshot written so far."""
    time.sleep(1.1)
    return now()


class Api:
    """This API over HTTP; every call is timed under the current stage.

    Local: ``base_url`` with the signed-in person's token from the ``mainsequence`` CLI. Deployed:
    the release's endpoint and token, as the platform hands them to the signed-in person
    (``ResourceRelease.resolve_runtime_access``), asked again before the token goes stale.
    """

    def __init__(self, base_url: str | None, clock: Clock, release_uid: str | None = None) -> None:
        import httpx

        self.clock = clock

        def cli_token() -> str:
            return subprocess.run(
                [str(ROOT / ".venv/bin/mainsequence"), "auth", "token"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()

        def release_access() -> tuple[str, dict[str, str]]:
            from mainsequence.client import ResourceRelease
            from mainsequence.client.utils import SessionJWTAuthProvider

            with clock.call("platform: resolve the API's runtime access"):
                access = ResourceRelease(uid=release_uid, release_kind="fastapi")
                access = access.wait_for_runtime_access(wait_timeout_seconds=300).access or {}
            check(access.get("rpc_url") and access.get("token"), "The release gave no endpoint.")
            headers = SessionJWTAuthProvider(access_token=access["token"], refresh_token="")
            return str(access["rpc_url"]).rstrip("/"), dict(headers.get_headers())

        if release_uid:
            base_url, first_headers = release_access()

        class SignedIn(httpx.Auth):
            def __init__(self) -> None:
                self.headers: dict[str, str] = first_headers if release_uid else {}
                self.read_at = time.monotonic() if release_uid else 0.0

            def auth_flow(self, request):
                if not self.headers or time.monotonic() - self.read_at > 20:
                    if release_uid:
                        self.headers = release_access()[1]
                    else:
                        token = os.environ.get("ALPACA_E2E_TOKEN") or cli_token()
                        self.headers = {"Authorization": f"Bearer {token}"}
                    self.read_at = time.monotonic()
                request.headers.update(self.headers)
                yield request

        self.base_url = base_url
        self.client = httpx.Client(base_url=base_url, auth=SignedIn(), timeout=180.0)

    def request(self, method: str, path: str, label: str | None = None, **kwargs: Any) -> Any:
        name = label or f"{method} {_template(path)}"
        with self.clock.call(name):
            return self.client.request(method, path, **kwargs)

    def get(self, path: str, **kwargs: Any) -> Any:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> Any:
        return self.request("POST", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Any:
        return self.request("DELETE", path, **kwargs)


def _template(path: str) -> str:
    """A path with its UIDs shortened, so the report reads as routes."""
    parts = []
    for part in path.split("/"):
        try:
            uuid.UUID(part)
            parts.append("{uid}")
        except ValueError:
            parts.append(part)
    return "/".join(parts)


def ok(response: Any, *statuses: int) -> dict[str, Any]:
    """The JSON body of an answer with an expected status; a failure shows the public error."""
    expected = statuses or (200,)
    if response.status_code not in expected:
        try:
            detail = response.json().get("detail")
        except Exception:  # noqa: BLE001 - an answer without JSON is reported by its status
            detail = None
        code = detail.get("code") if isinstance(detail, dict) else None
        message = detail.get("message") if isinstance(detail, dict) else detail
        raise CheckFailed(
            f"{response.request.method} {_template(response.request.url.path)} answered "
            f"{response.status_code} (code: {code}): {message}"
        )
    return response.json()


def statuses(operation: dict[str, Any]) -> dict[str, str]:
    return {step["key"]: step["status"] for step in operation["steps"]}


class Alpaca:
    """Alpaca read directly with the paper keys: the run's own source of truth."""

    def __init__(self, clock: Clock) -> None:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.trading.client import TradingClient

        key, secret = _setting("ALPACA_PAPER_API_KEY"), _setting("ALPACA_PAPER_SECRET_KEY")
        self.trading = TradingClient(key, secret, paper=True)
        self.data = StockHistoricalDataClient(key, secret)
        self.clock = clock

    def equity(self) -> float:
        return self.clock.timed("alpaca: equity", lambda: float(self.trading.get_account().equity))

    def market_open(self) -> bool:
        return self.clock.timed("alpaca: clock", lambda: bool(self.trading.get_clock().is_open))

    def positions(self) -> dict[str, float]:
        return self.clock.timed(
            "alpaca: positions",
            lambda: {str(p.symbol): float(p.qty) for p in self.trading.get_all_positions()},
        )

    def prices(self) -> dict[str, float]:
        from alpaca.data.requests import StockLatestTradeRequest

        def read() -> dict[str, float]:
            trades = self.data.get_stock_latest_trade(
                StockLatestTradeRequest(symbol_or_symbols=list(SYMBOLS))
            )
            return {str(symbol): float(trade.price) for symbol, trade in dict(trades).items()}

        return self.clock.timed("alpaca: latest prices", read)

    def cancel_open_orders(self, client_order_ids: set[str]) -> int:
        """Cancel the open orders with these client order ids; how many were cancelled."""
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        def cancel() -> int:
            orders = self.trading.get_orders(
                GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=500)
            )
            mine = [order for order in orders if order.client_order_id in client_order_ids]
            for order in mine:
                self.trading.cancel_order_by_id(order.id)
            return len(mine)

        return self.clock.timed("alpaca: cancel open orders", cancel)


# ------------------------------------------------------------------------------------------------
# The run
# ------------------------------------------------------------------------------------------------
class Lifecycle:
    def __init__(
        self, api: Api, markets: Any, alpaca: Alpaca, clock: Clock, *, deployed: bool = False
    ) -> None:
        self.api, self.markets, self.alpaca, self.clock = api, markets, alpaca, clock
        self.deployed = deployed
        self.run = uuid.uuid4().hex[:8]
        self.state: dict[str, Any] = {}
        self.order_ids: set[str] = set()
        self.warnings: list[str] = []

    # -- helpers ---------------------------------------------------------------------------------
    def need(self, *names: str) -> None:
        missing = [name for name in names if name not in self.state]
        if missing:
            raise Skipped(f"an earlier step did not complete (missing: {', '.join(missing)})")

    def sync(self) -> dict[str, Any]:
        return ok(self.api.post(f"/v1/accounts/{self.state['account_uid']}/actions/sync"))

    def delete_account(self, account_uid: str) -> dict[str, Any]:
        return ok(
            self.api.delete(
                f"/v1/accounts/{account_uid}", params={"confirm": f"DELETE {account_uid}"}
            )
        )

    def write_weights(self, weights: dict[str, dict[str, float]], *, at: dt.datetime) -> None:
        """Publish fixed weights in Markets' portfolio-weights series (ADR 0031)."""
        from msm_portfolios.api import PortfolioWeightsSeries

        for portfolio, legs in weights.items():
            rows = [
                {
                    "time_index": at,
                    "portfolio_identifier": portfolio,
                    "asset_identifier": self.state["identifiers"][symbol],
                    "weight": weight,
                }
                for symbol, weight in legs.items()
            ]
            self.clock.timed(
                f"markets: publish weights {portfolio}",
                PortfolioWeightsSeries.publish,
                portfolio,
                rows,
            )

    def write_target(self, exposures: list[dict[str, Any]]) -> Any:
        """State the account's whole exposure at a new time, as an application does."""
        at = next_second()
        target = self.clock.timed(
            "markets: write account target",
            self.markets.add_account_target_positions,
            self.state["account_uid"],
            target_positions_date=at,
            positions=exposures,
        )
        check(target.position_set_uid, "The target write returned no position set.")
        time.sleep(1.1)  # A snapshot must not be earlier than the target it is allocated against.
        return target

    def clear_target(self) -> None:
        self.clock.timed(
            "markets: clear account target",
            self.markets.clear_account_target_positions,
            self.state["account_uid"],
            target_positions_date=next_second(),
        )
        time.sleep(1.1)

    def exposure(self, portfolio: str, **value: float) -> dict[str, Any]:
        uid = self.state["portfolios"][portfolio]
        return {"target_type": "portfolio", "target_uid": uid, "portfolio_uid": uid, **value}

    def fund_lines(self, operation: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
        portfolio_by_uid = {uid: name for name, uid in self.state["portfolios"].items()}
        symbol_by_identifier = {i: s for s, i in self.state["identifiers"].items()}
        return {
            (
                portfolio_by_uid[line["target_portfolio_uid"]],
                symbol_by_identifier[line["asset_identifier"]],
            ): line
            for line in operation["result"]["lines"]
            if line["owner"] == "fund"
        }

    def deltas(self, operation: dict[str, Any]) -> dict[str, float]:
        deltas: dict[str, float] = {}
        for (_, symbol), line in self.fund_lines(operation).items():
            deltas[symbol] = deltas.get(symbol, 0.0) + line["delta"]
        return deltas

    def trade(self, quantities: dict[str, float]) -> dict[str, Any] | None:
        """Trade signed quantities per symbol in one batch; the filled batch, or None.

        With the market open the batch is polled until every order is filled. With it closed the
        orders are queued, checked as accepted and cancelled again, so nothing fills later.
        """
        market_open = self.alpaca.market_open()
        orders = [
            {
                "client_order_id": f"{self.run}:{symbol}:{uuid.uuid4().hex[:6]}",
                "asset_identifier": self.state["identifiers"][symbol],
                "symbol": symbol,
                "side": "buy" if quantity > 0 else "sell",
                "quantity": round(abs(quantity), 9),
                "order_type": "market",
                "time_in_force": "day",
            }
            for symbol, quantity in sorted(quantities.items())
            if abs(quantity) >= 1e-9
        ]
        if not orders:
            return None
        batch = {
            "batch_id": f"ALPACA_E2E__{uuid.uuid4().hex[:10]}",
            "queue_when_closed": not market_open,
            "orders": orders,
        }
        base = f"/v1/accounts/{self.state['account_uid']}/orders/actions"
        if not market_open:
            refused = ok(
                self.api.post(
                    f"{base}/submit/preflight",
                    json={**batch, "queue_when_closed": False},
                    label="POST orders/submit/preflight (closed market, not queued)",
                )
            )
            check(not refused["allowed"], "The closed market did not refuse an unqueued batch.")
            check(
                "market_closed" in {c["code"] for c in refused["checks"]},
                "The refusal does not name the closed market.",
            )
        preflight = ok(self.api.post(f"{base}/submit/preflight", json=batch))
        check(
            preflight["allowed"], f"Pre-trade checks refused the batch: {preflight.get('blockers')}"
        )
        ids = {order["client_order_id"] for order in orders}
        self.order_ids |= ids
        try:
            result = ok(self.api.post(f"{base}/submit", json=batch))
            check(result["accepted"], result["message"])
            refused = {
                order["client_order_id"].split(":")[1]: order.get("message")
                for order in result["orders"]
                if order["status"] == "rejected"
            }
            for symbol, reason in refused.items():
                self.warnings.append(f"Alpaca refused the {symbol} order: {reason}")
            check(len(refused) < len(orders), f"Alpaca refused every order: {refused}")

            if not market_open:
                check(
                    {o["status"] for o in result["orders"]} <= {"submitted", "rejected"},
                    "A queued order is neither submitted nor rejected.",
                )
                check(result["holdings_set_uid"] is None, "A queued batch published a snapshot.")
                result = ok(self.api.post(f"{base}/cancel", json={"batch_id": batch["batch_id"]}))
                deadline = time.monotonic() + 30
                while (
                    any(o["status"] == "submitted" for o in result["orders"])
                    and time.monotonic() < deadline
                ):
                    time.sleep(1)
                    result = ok(self.api.post(f"{base}/sync", json={"batch_id": batch["batch_id"]}))
                check(
                    {o["status"] for o in result["orders"]} <= {"cancelled", "rejected"},
                    "A queued order did not end cancelled.",
                )
                return None

            done = {"filled", "rejected", "cancelled", "expired", "failed"}
            deadline = time.monotonic() + FILL_TIMEOUT_SECONDS
            while (
                any(o["status"] not in done for o in result["orders"])
                and time.monotonic() < deadline
            ):
                time.sleep(2)
                result = ok(self.api.post(f"{base}/sync", json={"batch_id": batch["batch_id"]}))
            check(
                {o["status"] for o in result["orders"]} <= {"filled", "rejected"},
                f"The batch ended {result['status']}.",
            )
            return {**result, "refused_symbols": set(refused)}
        finally:
            self.alpaca.cancel_open_orders(ids)

    # -- before ----------------------------------------------------------------------------------
    def remove_leftovers(self) -> None:
        """An earlier run that stopped half way leaves its account, portfolios and calendar."""
        for leftover in ok(self.api.get("/v1/accounts", params={"limit": 100}))["items"]:
            if leftover.get("account_name") == ACCOUNT_NAME:
                self.delete_account(str(leftover["uid"]))
        self.delete_markets_rows()

    def delete_markets_rows(self) -> None:
        """Delete the three portfolios, then the calendar only they use."""
        from msm.api.calendars import Calendar
        from msm.api.portfolios import Portfolio

        for name in PORTFOLIOS:
            portfolio = self.clock.timed(
                f"markets: find portfolio {name}", Portfolio.get_by_unique_identifier, name
            )
            if portfolio is not None:
                self.clock.timed(
                    f"markets: delete portfolio {name}",
                    self.markets.delete_portfolio,
                    portfolio.uid,
                )
        calendar = self.clock.timed(
            "markets: find calendar", Calendar.get_by_unique_identifier, CALENDAR
        )
        if calendar is not None:
            self.clock.timed("markets: delete calendar", Calendar.delete, calendar.uid)

    # -- stages ----------------------------------------------------------------------------------
    def register(self) -> None:
        request = {
            "account_name": ACCOUNT_NAME,
            "environment": "paper",
            "credentials": {
                "source": "managed",
                "api_key": _setting("ALPACA_PAPER_API_KEY"),
                "secret_key": _setting("ALPACA_PAPER_SECRET_KEY"),
            },
        }
        preflight = ok(self.api.post("/v1/accounts/registration/preflight", json=request))
        check(
            not preflight.get("blockers"), f"Registration is blocked: {preflight.get('blockers')}"
        )
        created = self.api.post("/v1/accounts", json=request)
        account = ok(created, 201)
        # Neither key value comes back in any field of the answer.
        check(request["credentials"]["secret_key"] not in created.text, "The answer has a key.")
        check(request["credentials"]["api_key"] not in created.text, "The answer has a key.")
        self.state["account_uid"] = str(account["uid"])
        again = ok(self.api.get(f"/v1/accounts/{self.state['account_uid']}"))
        check(str(again["uid"]) == self.state["account_uid"], "The account reads back wrong.")
        # The same Alpaca account gives the same Markets account, which keeps its target when the
        # connector's registration is removed. Start from no target.
        held = self.clock.timed(
            "markets: read account target",
            self.markets.account_target_positions,
            self.state["account_uid"],
        )
        if held.positions:
            self.clear_target()
        self.state["start_positions"] = self.alpaca.positions()

    def capture(self) -> None:
        self.need("account_uid")
        operation = self.sync()
        check(operation["status"] == "succeeded", f"The sync ended {operation['status']}.")
        check(
            statuses(operation)
            == {
                "capture_holdings": "succeeded",
                "size_targets": "skipped",
                "allocate_funds": "skipped",
            },
            f"Unexpected steps: {statuses(operation)}",
        )
        result = operation["result"]
        check(result["holdings_set_uid"] and result["holdings_rows"] >= 1, "No snapshot.")
        check(
            (result["position_set_uid"], result["allocation_applied"], result["lines"])
            == (None, False, []),
            "A sync with no target sized or allocated something.",
        )
        again = ok(self.api.get(f"/v1/operations/{operation['operation_uid']}"))
        check(again == operation, "The operation reads back different.")

    def portfolios(self) -> None:
        self.need("account_uid")
        from msm.api.calendars import Calendar
        from msm.api.portfolios import Portfolio

        from alpaca_connectors.holdings.allocation import read_portfolio_weights
        from alpaca_connectors.markets_api import assets_by_uid

        registered = ok(
            self.api.post(
                "/v1/assets/registration/execute",
                json={"account_uid": self.state["account_uid"], "symbols": list(SYMBOLS)},
            )
        )
        uids = registered["assets_by_symbol"]
        check(set(uids) == set(SYMBOLS), f"Not every symbol was registered: {sorted(uids)}")
        assets = self.clock.timed("markets: read assets", assets_by_uid, uids.values())
        self.state["identifiers"] = {
            symbol: str(assets[str(uid)].unique_identifier) for symbol, uid in uids.items()
        }
        # ms-markets 4.2 stores a calendar's rules up to the library's horizon: no end date.
        calendar = self.clock.timed(
            "markets: find calendar", Calendar.get_by_unique_identifier, CALENDAR
        ) or self.clock.timed(
            "markets: create calendar",
            Calendar.create_from_pandas_calendar,
            source_identifier="NYSE",
            unique_identifier=CALENDAR,
            display_name="Alpaca end-to-end NYSE",
            valid_from=dt.date.today() - dt.timedelta(days=30),
            timezone="America/New_York",
        )
        self.state["portfolios"] = {
            name: str(
                self.clock.timed(
                    f"markets: upsert portfolio {name}",
                    Portfolio.upsert,
                    unique_identifier=name,
                    calendar_uid=calendar.uid,
                ).uid
            )
            for name in PORTFOLIOS
        }
        self.write_weights(WEIGHTS, at=now())
        published = self.clock.timed(
            "markets: read published weights",
            read_portfolio_weights,
            [*WEIGHTS, PORTFOLIO_WITHOUT_WEIGHTS],
            at=next_second(),
        )
        expected = {
            portfolio: {self.state["identifiers"][s]: w for s, w in legs.items()}
            for portfolio, legs in WEIGHTS.items()
        }
        check(published == expected, f"Published weights read back as {published}.")

    def exposure_target(self) -> None:
        self.need("account_uid", "portfolios")
        target = self.write_target(
            [
                self.exposure(PORTFOLIO_1, weight_notional_exposure=SHARE_OF_NAV),
                self.exposure(PORTFOLIO_2, constant_notional_exposure=AMOUNT),
            ]
        )
        self.state["position_set_uid"] = str(target.position_set_uid)
        read = self.clock.timed(
            "markets: read account target",
            self.markets.account_target_positions,
            self.state["account_uid"],
        )
        check(str(read.position_set_uid) == self.state["position_set_uid"], "Target reads back.")
        check(
            {row.portfolio.unique_identifier for row in read.positions}
            == {PORTFOLIO_1, PORTFOLIO_2},
            "The target names other portfolios.",
        )

    def size(self) -> None:
        self.need("position_set_uid")
        equity, prices = self.alpaca.equity(), self.alpaca.prices()
        operation = self.sync()
        check(operation["status"] == "succeeded", f"The sync failed: {operation['error']}")
        check(set(statuses(operation).values()) == {"succeeded"}, f"{statuses(operation)}")
        result = operation["result"]
        check(result["position_set_uid"] == self.state["position_set_uid"], "Wrong target sized.")
        check(result["allocation_applied"] is True, "The snapshot was not allocated.")
        lines = self.fund_lines(operation)
        sizes = {PORTFOLIO_1: SHARE_OF_NAV * equity, PORTFOLIO_2: AMOUNT}
        check(
            set(lines) == {(p, s) for p, legs in WEIGHTS.items() for s in legs},
            f"Unexpected fund lines: {sorted(lines)}",
        )
        for (portfolio, symbol), line in lines.items():
            expected = sizes[portfolio] * WEIGHTS[portfolio][symbol] / prices[symbol]
            check(
                math.isclose(line["target"], expected, rel_tol=SIZING_TOLERANCE),
                f"{portfolio} {symbol}: sized {line['target']}, expected about {expected:.4f}",
            )
            check(
                math.isclose(line["delta"], line["target"] - line["assigned"], abs_tol=UNITS),
                f"{portfolio} {symbol}: delta is not target minus assigned.",
            )
            check(
                0 <= line["assigned"] <= line["target"] + UNITS,
                f"{portfolio} {symbol}: assigned outside 0..target.",
            )
        direct = [line for line in result["lines"] if line["owner"] == "direct"]
        check(
            any(line["assigned"] > 0 and line["target"] == 0 for line in direct),
            "Cash is not a direct line with no target.",
        )
        recorded = self.clock.timed(
            "markets: read allocation",
            self.markets.account_virtual_fund_allocation,
            self.state["account_uid"],
            valuation_time=dt.datetime.fromisoformat(result["time_index"]),
        )
        check(len(recorded.lines) == len(result["lines"]), "Markets recorded other lines.")
        self.state["sized"] = operation

    def assignment(self) -> None:
        """Per asset, the fund lines and the direct line add up to what the account holds."""
        self.need("sized")
        held = self.alpaca.positions()
        symbol_by_identifier = {i: s for s, i in self.state["identifiers"].items()}
        totals: dict[str, float] = {}
        for line in self.state["sized"]["result"]["lines"]:
            symbol = symbol_by_identifier.get(line["asset_identifier"])
            if symbol is not None:
                totals[symbol] = totals.get(symbol, 0.0) + line["assigned"]
        for symbol in SYMBOLS:
            check(
                math.isclose(totals.get(symbol, 0.0), held.get(symbol, 0.0), abs_tol=UNITS),
                f"{symbol}: the account holds {held.get(symbol, 0.0)} and its lines add up to "
                f"{totals.get(symbol, 0.0)}",
            )

    def rebalance(self) -> None:
        self.need("sized")
        batch = self.trade(self.deltas(self.state["sized"]))
        if batch is None:
            return  # Closed market: queued and cancelled, nothing filled.
        check(batch["holdings_set_uid"] and batch["holdings_time"], "The fills published nothing.")
        check(
            not [text for text in batch["warnings"] if "allocated" in text],
            f"The fills were not allocated: {batch['warnings']}",
        )
        recorded = self.clock.timed(
            "markets: read allocation",
            self.markets.account_virtual_fund_allocation,
            self.state["account_uid"],
            valuation_time=dt.datetime.fromisoformat(batch["holdings_time"]),
        )
        check(recorded.lines, "The snapshot after the fills has no allocation lines.")
        operation = self.sync()
        check(operation["status"] == "succeeded", f"The sync failed: {operation['error']}")
        lines = self.fund_lines(operation)
        for (portfolio, symbol), line in lines.items():
            if symbol in batch["refused_symbols"]:
                continue
            check(
                abs(line["delta"]) <= SIZING_TOLERANCE * line["target"] + UNITS,
                f"{portfolio} {symbol}: delta {line['delta']} of target {line['target']}",
            )
        held = self.alpaca.positions()["NVDA"]
        shared = lines[(PORTFOLIO_1, "NVDA")]["assigned"] + lines[(PORTFOLIO_2, "NVDA")]["assigned"]
        check(math.isclose(shared, held, rel_tol=1e-6), "NVDA is not split between both funds.")
        self.state["funded"] = True

    def no_weights(self) -> None:
        self.need("position_set_uid")
        self.write_target(
            [
                self.exposure(PORTFOLIO_1, weight_notional_exposure=SHARE_OF_NAV),
                self.exposure(PORTFOLIO_2, constant_notional_exposure=AMOUNT),
                self.exposure(PORTFOLIO_WITHOUT_WEIGHTS, constant_notional_exposure=1_000.0),
            ]
        )
        operation = self.sync()
        check(
            (operation["status"], operation["current_step"]) == ("failed", "size_targets"),
            f"The sync ended {operation['status']} at {operation['current_step']}.",
        )
        check(
            operation["error"]["code"] == "portfolio_weights_missing",
            f"Wrong error: {operation['error']}",
        )
        check(
            statuses(operation)
            == {
                "capture_holdings": "succeeded",
                "size_targets": "failed",
                "allocate_funds": "skipped",
            },
            f"Unexpected steps: {statuses(operation)}",
        )
        check(
            PORTFOLIO_WITHOUT_WEIGHTS not in operation["error"]["message"],
            "The error names the portfolio.",
        )
        latest = ok(self.api.get(f"/v1/accounts/{self.state['account_uid']}/holdings/latest"))
        check(latest["items"], "The snapshot published before the failure should stay.")

    def reweight(self) -> None:
        self.need("sized")
        self.write_weights(REWEIGHTED, at=next_second())
        target = self.write_target(
            [
                self.exposure(PORTFOLIO_1, weight_notional_exposure=LOWER_SHARE_OF_NAV),
                self.exposure(PORTFOLIO_2, constant_notional_exposure=AMOUNT),
            ]
        )
        equity, prices = self.alpaca.equity(), self.alpaca.prices()
        operation = self.sync()
        check(operation["status"] == "succeeded", f"The sync failed: {operation['error']}")
        check(
            operation["result"]["position_set_uid"] == str(target.position_set_uid),
            "Wrong target sized.",
        )
        lines = self.fund_lines(operation)
        for symbol, weight in REWEIGHTED[PORTFOLIO_1].items():
            expected = LOWER_SHARE_OF_NAV * equity * weight / prices[symbol]
            check(
                math.isclose(
                    lines[(PORTFOLIO_1, symbol)]["target"], expected, rel_tol=SIZING_TOLERANCE
                ),
                f"{PORTFOLIO_1} {symbol}: sized wrong after the reweight.",
            )
        if self.state.get("funded"):
            check(lines[(PORTFOLIO_1, "NVDA")]["delta"] < 0, "Less NVDA is not sold down.")
        batch = self.trade(self.deltas(operation))
        if batch is None:
            return
        after = self.fund_lines(self.sync())
        for (_, symbol), line in after.items():
            if symbol in batch["refused_symbols"]:
                continue
            check(
                abs(line["delta"]) <= SIZING_TOLERANCE * line["target"] + UNITS,
                f"{symbol}: still a delta of {line['delta']} after the trade.",
            )

    def portfolio_jobs(self) -> None:
        """Three portfolios share the price and rebalance-state tables; each records its own run.

        Two are first observed at one time and one earlier, so a portfolio that took another's
        progress as its own (ms-markets issue 118) records nothing, or starts late.
        """
        self.need("account_uid", "identifiers")
        from msm_portfolios.api import PortfolioValuesSeries, PortfolioWeightsSeries

        from alpaca_connectors.market_data.alpaca_bars_support import stock_sessions

        account = self.state["account_uid"]
        today = now().date()
        sessions = sorted(stock_sessions(today - dt.timedelta(days=21), today).values())
        closed = [session for session in sessions if session[1] < now()]
        check(len(closed) >= 6, "Fewer than six past NYSE sessions in the last three weeks.")
        # An hour into a session: an immediate signal trades at that session's close.
        later, earlier = closed[-2], closed[-5]
        plan = {"shared_1": later, "shared_2": later, "earlier": earlier}
        rebalance = ok(
            self.api.post(
                "/v1/portfolio-rebalance-configurations",
                json={"name": f"ALPACA_E2E {self.run}", "strategy": "immediate_signal"},
            ),
            201,
        )
        self.state["rebalance_configuration_uid"] = str(rebalance["uid"])
        weights = {self.state["identifiers"]["AAPL"]: 0.5, self.state["identifiers"]["NVDA"]: 0.5}
        portfolios: dict[str, tuple[str, str, dt.datetime]] = {}
        for key, (session_open, session_close) in plan.items():
            definition = ok(
                self.api.post(
                    "/v1/signal-definitions",
                    json={"account_uid": account, "key": f"e2e-{self.run}-{key}", "name": key},
                ),
                201,
            )
            observed = session_open + dt.timedelta(hours=1)
            ok(
                self.api.post(
                    f"/v1/signal-definitions/{definition['uid']}/weights",
                    json={
                        "observations": [{"observed_at": observed.isoformat(), "weights": weights}]
                    },
                ),
                201,
            )
            created = ok(
                self.api.post(
                    "/v1/portfolio-configurations",
                    json={
                        "name": f"ALPACA_E2E {self.run} {key}",
                        "account_uid": account,
                        "signal_definition_uid": str(definition["uid"]),
                        "rebalance_configuration_uid": str(rebalance["uid"]),
                        "scheduled": False,
                        "bars": {"scheduled": False},
                    },
                ),
                201,
            )
            portfolios[key] = (
                str(created["configuration"]["uid"]),
                str(created["portfolio_unique_identifier"]),
                session_close,
            )
        for key, (configuration_uid, _, _) in portfolios.items():
            self.run_portfolio_job(key, configuration_uid)

        identifiers = [identifier for _, identifier, _ in portfolios.values()]
        recorded = {
            str(bounds.key): bounds
            for bounds in self.clock.timed(
                "markets: weights bounds", PortfolioWeightsSeries.bounds, identifiers
            )
        }
        valued = {
            str(bounds.key): bounds
            for bounds in self.clock.timed(
                "markets: values bounds", PortfolioValuesSeries.bounds, identifiers
            )
        }
        for key, (_, identifier, first_close) in portfolios.items():
            check(
                recorded[identifier].row_count and valued[identifier].row_count,
                f"Portfolio {key} recorded no weights or values: another portfolio's progress "
                "in the shared rebalance-state table was taken as its own (ms-markets issue 118).",
            )
            check(
                recorded[identifier].first_time == first_close,
                f"Portfolio {key}'s first weights are at {recorded[identifier].first_time}, not at "
                f"the close of its first observation's session, {first_close}.",
            )

    def run_portfolio_job(self, key: str, configuration_uid: str) -> None:
        """Run the portfolio Job once for the configuration and wait for it to end."""
        if self.deployed:
            accepted = ok(
                self.api.post(f"/v1/portfolio-configurations/{configuration_uid}/actions/run"),
                202,
            )
            deadline = time.monotonic() + JOB_TIMEOUT_SECONDS
            with self.clock.call(f"portfolio Job {key}: queued to ended"):
                while True:
                    state = ok(self.api.get(f"/v1/operations/job-runs/{accepted['job_run_uid']}"))
                    if state["status"] in JOB_DONE or time.monotonic() > deadline:
                        break
                    time.sleep(10)
            check(state["status"] == "SUCCEEDED", f"The {key} Job run ended {state['status']}.")
            return
        launcher = str(ROOT / "src/jobs/run_alpaca_portfolio.py")
        with self.clock.call(f"portfolio Job {key}: local launcher"):
            finished = subprocess.run(
                [sys.executable, "-c", LOCAL_JOB, launcher, configuration_uid],
                capture_output=True,
                text=True,
                timeout=JOB_TIMEOUT_SECONDS,
                check=False,
            )
        check(finished.returncode == 0, f"The {key} Job run exited {finished.returncode}.")

    # -- after -----------------------------------------------------------------------------------
    def cleanup(self) -> None:
        """Leave the paper account and the local stack as the run found them."""
        failures: list[str] = []

        def attempt(what: str, action: Callable[[], Any]) -> None:
            try:
                action()
            except Exception as exc:  # noqa: BLE001 - every cleanup step runs
                failures.append(f"{what}: {type(exc).__name__}: {exc}")

        if self.order_ids:
            attempt("cancel open orders", lambda: self.alpaca.cancel_open_orders(self.order_ids))
        if "account_uid" in self.state:
            if "start_positions" in self.state and "identifiers" in self.state:
                attempt("restore positions", self.restore_positions)
            if "position_set_uid" in self.state:
                attempt("clear target", self.clear_target)
            account_uid = self.state["account_uid"]
            attempt("delete account", lambda: self.delete_account(account_uid))
            attempt(
                "account gone",
                lambda: check(
                    self.api.get(f"/v1/accounts/{account_uid}").status_code == 404,
                    "The account still reads back after the delete.",
                ),
            )
        if "rebalance_configuration_uid" in self.state:
            rule = self.state["rebalance_configuration_uid"]
            attempt(
                "delete rebalance configuration",
                lambda: ok(self.api.delete(f"/v1/portfolio-rebalance-configurations/{rule}")),
            )
        attempt("delete portfolios and calendar", self.delete_markets_rows)
        check(not failures, "; ".join(failures))

    def restore_positions(self) -> None:
        """Trade the three symbols back to what the account held when the run started."""
        start = self.state["start_positions"]
        held = self.alpaca.positions()
        back = {s: start.get(s, 0.0) - held.get(s, 0.0) for s in SYMBOLS}
        back = {s: q for s, q in back.items() if abs(q) >= UNITS}
        if not back:
            return
        if not self.alpaca.market_open():
            self.warnings.append(f"Positions not restored, the market is closed: {back}")
            return
        self.trade(back)


def run(
    *,
    api_url: str | None = None,
    release_uid: str | None = None,
    keep: bool = False,
    clock: Clock | None = None,
) -> tuple[Clock, list]:
    """Run every stage and the cleanup; the timings and the warnings.

    With ``release_uid`` it runs against that deployed API and the Environment's Markets API, as
    the signed-in person; otherwise against the local stack at ``api_url``.
    """
    for name, value in _env_file().items():
        os.environ.setdefault(name, value)
    if release_uid:
        # The Markets API is found in the person's Environment, as a deployed workload finds it.
        os.environ.pop("MARKETS_API_URL", None)
    elif not os.environ.get("MARKETS_API_URL"):
        raise SystemExit("MARKETS_API_URL is not set: point it at the local Markets API.")
    clock = clock or Clock()

    with clock.stage("setup: connect to the API, Markets and Alpaca"):
        api = Api(api_url, clock, release_uid=release_uid)
        health = api.get("/health")
        check(health.status_code == 200, f"GET /health answered {health.status_code}.")
        from alpaca_connectors.runtime import start_markets_engine, use_markets_api

        if not release_uid:
            from metatables import configure_local_client

            configure_local_client()
            clock.timed("markets: start engine", start_markets_engine)
        markets = use_markets_api().client
        lifecycle = Lifecycle(api, markets, Alpaca(clock), clock, deployed=bool(release_uid))
    if clock.stages[-1].status != "passed":
        return clock, []

    with clock.stage("cleanup before: remove an earlier run's account and portfolios"):
        lifecycle.remove_leftovers()
    stages = [
        ("1 register the account with the paper keys", lifecycle.register),
        ("2 sync with no target: capture only", lifecycle.capture),
        ("3 publish two fixed-weight portfolios", lifecycle.portfolios),
        ("4 state the exposure: creates the funds", lifecycle.exposure_target),
        ("5 sync: size the exposure, allocate the snapshot", lifecycle.size),
        ("6 every unit held is assigned once", lifecycle.assignment),
        ("7 trade the deltas, fills go to the funds", lifecycle.rebalance),
        ("8 a portfolio with no weights fails the sizing", lifecycle.no_weights),
        ("9 new weights and lower exposure: size and trade", lifecycle.reweight),
        ("10 portfolio Job: three portfolios share the tables", lifecycle.portfolio_jobs),
    ]
    try:
        for name, stage in stages:
            with clock.stage(name):
                stage()
    finally:
        if not keep:
            with clock.stage("cleanup after: orders, positions, target, account, portfolios"):
                lifecycle.cleanup()
    return clock, lifecycle.warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--api-url",
        default=os.environ.get("ALPACA_LIFECYCLE_API_URL", "http://127.0.0.1:8321"),
        help="This API (default: ALPACA_LIFECYCLE_API_URL or http://127.0.0.1:8321).",
    )
    parser.add_argument(
        "--release-uid",
        default=os.environ.get("ALPACA_LIFECYCLE_RELEASE_UID"),
        help=(
            "Run against this deployed FastAPI release instead of the local stack, with the "
            "Environment's Markets API (default: ALPACA_LIFECYCLE_RELEASE_UID)."
        ),
    )
    parser.add_argument(
        "--keep", action="store_true", help="Skip the cleanup and leave the account."
    )
    parser.add_argument("--json", type=Path, help="Also write the timings to this file.")
    args = parser.parse_args(argv)

    clock, warnings = run(
        api_url=args.api_url.rstrip("/"), release_uid=args.release_uid, keep=args.keep
    )
    print(render(clock))
    for warning in warnings:
        print(f"warning: {warning}")
    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "ok": clock.ok,
                    "total_seconds": round(clock.total, 3),
                    "stages": [asdict(stage) for stage in clock.stages],
                    "warnings": warnings,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    return 0 if clock.ok else 1


if __name__ == "__main__":
    sys.exit(main())
