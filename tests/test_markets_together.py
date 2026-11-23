"""One-item Markets routes are called together, not one after another (issue #46)."""

from __future__ import annotations

import contextvars
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from alpaca_connectors import markets_api
from alpaca_connectors.markets_api import each_together

CALLER = contextvars.ContextVar("caller", default=None)


def test_the_calls_run_together_in_the_callers_context_and_answer_in_order() -> None:
    # The barrier opens only when all three calls wait at once: one after another, it times out.
    together = threading.Barrier(3, timeout=5)

    def call(value: int) -> tuple[int, str | None]:
        together.wait()
        return value * 10, CALLER.get()

    token = CALLER.set("person")
    try:
        answers = each_together(call, [1, 2, 3])
    finally:
        CALLER.reset(token)

    assert answers == [(10, "person"), (20, "person"), (30, "person")]


def test_every_call_runs_and_the_first_failure_is_raised_after() -> None:
    ended: list[int] = []
    together = threading.Barrier(3, timeout=5)

    def call(value: int) -> int:
        together.wait()
        if value == 2:
            raise ValueError("refused")
        ended.append(value)
        return value

    with pytest.raises(ValueError, match="refused"):
        each_together(call, [1, 2, 3])
    assert sorted(ended) == [1, 3]


def test_no_more_than_the_limit_are_in_flight() -> None:
    lock, active, most = threading.Lock(), [0], [0]

    def call(value: int) -> int:
        with lock:
            active[0] += 1
            most[0] = max(most[0], active[0])
        threading.Event().wait(0.02)
        with lock:
            active[0] -= 1
        return value

    values = list(range(markets_api.MARKETS_CONCURRENT_CALLS * 3))
    assert each_together(call, values) == values
    assert most[0] <= markets_api.MARKETS_CONCURRENT_CALLS


def test_the_signals_end_together() -> None:
    together = threading.Barrier(3, timeout=5)
    rows = [SimpleNamespace(signal_uid=uid) for uid in ("s1", "s2", "s3")]

    def end_signal(uid: str) -> dict[str, str]:
        together.wait()
        return {"uid": uid, "state": "deleted"}

    with (
        patch("msm_portfolios.api.SignalMetadata.filter", return_value=rows),
        patch("msm_portfolios.recipes.end_signal", side_effect=end_signal),
    ):
        answers = markets_api.end_signals(["s1", "s2", "s3", "never-published"])

    assert answers == {uid: {"uid": uid, "state": "deleted"} for uid in ("s1", "s2", "s3")}
