"""The lifecycle check's parallel stage 10 and its paper keys (ADR 0032)."""

from __future__ import annotations

import datetime as dt
import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from alpaca_connectors.operations import lifecycle_check
from alpaca_connectors.operations.lifecycle_check import Clock, Lifecycle, PaperKeys, at_once


class Answer:
    def __init__(self, status: int, body: dict, method: str, path: str) -> None:
        self.status_code, self._body, self.text = status, body, json.dumps(body)
        self.request = SimpleNamespace(method=method, url=SimpleNamespace(path=path))

    def json(self) -> dict:
        return self._body


class FakeApi:
    """This API for stage 10. Its barriers let a call through only once all three portfolios'
    calls of that kind are waiting, so calls made one after another time out."""

    def __init__(self, ended: dict[str, str]) -> None:
        self.ended = ended
        self.created_together = threading.Barrier(3, timeout=5)
        self.started_together = threading.Barrier(3, timeout=5)
        self.observed: dict[str, dt.datetime] = {}

    def post(self, path: str, json: dict | None = None, **_: object) -> Answer:
        if path == "/v1/portfolio-rebalance-configurations":
            return Answer(201, {"uid": "rule"}, "POST", path)
        if path == "/v1/signal-definitions":
            self.created_together.wait()
            return Answer(201, {"uid": f"definition-{json['name']}"}, "POST", path)
        if path.endswith("/weights"):
            key = path.split("/")[3].removeprefix("definition-")
            self.observed[key] = dt.datetime.fromisoformat(json["observations"][0]["observed_at"])
            return Answer(201, {}, "POST", path)
        if path == "/v1/portfolio-configurations":
            key = json["signal_definition_uid"].removeprefix("definition-")
            body = {"configuration": {"uid": f"config-{key}"}, "portfolio_unique_identifier": key}
            return Answer(201, body, "POST", path)
        if path.endswith("/actions/run"):
            self.started_together.wait()
            key = path.split("/")[3].removeprefix("config-")
            return Answer(202, {"job_run_uid": f"run-{key}"}, "POST", path)
        raise AssertionError(path)

    def get(self, path: str, **_: object) -> Answer:
        key = path.rsplit("/", 1)[1].removeprefix("run-")
        return Answer(200, {"status": self.ended[key]}, "GET", path)

    def bounds(self, identifiers: list[str]) -> tuple[dict, dict]:
        """Each portfolio's first weights at the close of its first observation's session."""
        from alpaca_connectors.market_data.alpaca_bars_support import stock_sessions

        first = {}
        for key in identifiers:
            day = self.observed[key].date()
            _, close = stock_sessions(day, day)[day]
            first[key] = SimpleNamespace(row_count=1, first_time=close)
        return first, first


def stage_10(api: FakeApi) -> lifecycle_check.Timed:
    clock = Clock()
    lifecycle = Lifecycle(api, None, None, clock, PaperKeys("k", "s"), deployed=True)
    lifecycle.state = {"account_uid": "account", "identifiers": {"AAPL": "A", "NVDA": "N"}}
    with (
        patch("alpaca_connectors.markets_api.portfolio_series_bounds", api.bounds),
        clock.stage("10 portfolio Job"),
    ):
        lifecycle.portfolio_jobs()
    return clock.stages[0]


def test_stage_10_creates_and_runs_the_three_portfolios_together() -> None:
    api = FakeApi({"shared_1": "SUCCEEDED", "shared_2": "SUCCEEDED", "earlier": "SUCCEEDED"})

    stage = stage_10(api)

    assert stage.status == "passed", stage.detail
    assert sorted(call.name for call in stage.calls if call.name.startswith("portfolio Job")) == [
        "portfolio Job earlier: queued to ended",
        "portfolio Job shared_1: queued to ended",
        "portfolio Job shared_2: queued to ended",
    ]


def test_stage_10_names_every_job_run_that_did_not_succeed() -> None:
    api = FakeApi({"shared_1": "SUCCEEDED", "shared_2": "FAILED", "earlier": "ABORTED"})

    stage = stage_10(api)

    assert stage.status == "failed"
    assert "The shared_2 Job run ended FAILED." in stage.detail
    assert "The earlier Job run ended ABORTED." in stage.detail
    assert "shared_1" not in stage.detail


def test_at_once_answers_in_order_and_raises_once_every_task_ended() -> None:
    ended: list[str] = []

    def slow() -> int:
        time.sleep(0.05)
        ended.append("slow")
        return 1

    def fails() -> int:
        raise ValueError("refused")

    assert at_once(lambda: 1, lambda: 2, lambda: 3) == [1, 2, 3]
    with pytest.raises(ValueError, match="refused"):
        at_once(fails, slow)
    assert ended == ["slow"]


def test_the_paper_keys_come_from_the_two_secrets_and_never_print() -> None:
    values = {"ALPACA_PAPER_API_KEY": "paper-key-value", "ALPACA_PAPER_SECRET_KEY": "paper-secret"}

    def get(*, name: str) -> SimpleNamespace:
        return SimpleNamespace(value=SimpleNamespace(get_secret_value=lambda: values[name]))

    with patch("mainsequence.client.Secret.get", side_effect=get) as read:
        keys = PaperKeys.from_secrets()

    assert [call.kwargs["name"] for call in read.call_args_list] == list(values)
    assert (keys.api_key, keys.secret_key) == ("paper-key-value", "paper-secret")
    assert "paper-key-value" not in repr(keys) and "paper-secret" not in repr(keys)


def test_the_units_no_fund_takes_are_sold_only_from_long_positions() -> None:
    # A fund is assigned at most its target, so a lower exposure leaves the units above it on the
    # account's direct line. Only those of the run's symbols, held long, are sold, never more than
    # is held; a short and another symbol's line are left alone.
    lifecycle = Lifecycle(None, None, None, Clock(), PaperKeys("k", "s"), deployed=True)
    lifecycle.alpaca = SimpleNamespace(positions=lambda: {"NVDA": 30.0, "AAPL": -2.0, "JPM": 1.0})
    lifecycle.state = {"identifiers": {"NVDA": "N", "AAPL": "A", "JPM": "J"}}

    def line(owner: str, identifier: str, delta: float) -> dict:
        return {"owner": owner, "asset_identifier": identifier, "delta": delta}

    operation = {
        "result": {
            "lines": [
                line("fund", "N", 0.0),
                line("direct", "N", -12.5),
                line("direct", "A", -2.0),
                line("direct", "J", -4.0),
                line("direct", "CASH", -1000.0),
            ]
        }
    }

    assert lifecycle.excess(operation) == {"NVDA": -12.5, "JPM": -1.0}
