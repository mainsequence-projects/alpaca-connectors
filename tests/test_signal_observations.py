"""A signal's published observations, read from Markets with the ms-markets recipe (ADR 0031)."""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

from alpaca_connectors.portfolios.signal_observations import (
    read_signal_observation_bounds,
    read_signal_observation_bounds_by_signal,
    read_signal_observation_matrix,
)

FIRST = dt.datetime(2026, 9, 3, 14, tzinfo=dt.UTC)
SECOND = dt.datetime(2026, 9, 4, 14, tzinfo=dt.UTC)
DETAILS = "alpaca_connectors.assets.resolution.alpaca_details_by_identifier"


def _details(identifiers):
    known = {
        "ALPACA::a": {"symbol": "AAPL", "name": "Apple Inc."},
        "ALPACA::b": {"symbol": "MSFT", "name": "Microsoft Corporation"},
        "ALPACA::c": {"symbol": "NVDA", "name": "NVIDIA Corporation"},
    }
    return {identifier: known[identifier] for identifier in identifiers if identifier in known}


def test_signal_history_reads_all_assets_for_the_latest_distinct_observations_once(
    recipes,
) -> None:
    # The latest two observations, newest first as Markets may answer them; ALPACA::b left the
    # signal at SECOND (0) and ALPACA::c entered it then (NaN before its first observation).
    frame = pd.DataFrame(
        {"ALPACA::c": [0.3, float("nan")], "ALPACA::a": [0.7, 0.6], "ALPACA::b": [0.0, 0.4]},
        index=pd.DatetimeIndex([SECOND, FIRST]),
    )
    recipes.answer("signal_history", {"signal-123": SimpleNamespace(matrix=frame)})
    with patch(DETAILS, side_effect=_details) as details:
        matrix = read_signal_observation_matrix(signal_uid="signal-123", observation_limit=2)

    # One Markets call for the latest two distinct observations, every asset of each, and one
    # read of this application's Alpaca details for their symbols and names.
    assert recipes.calls == [("signal_history", {"signal_uids": ["signal-123"], "latest": 2})]
    details.assert_called_once_with(["ALPACA::a", "ALPACA::b", "ALPACA::c"])
    assert matrix.signal_uid == "signal-123"
    assert matrix.time_indexes == (FIRST, SECOND)
    assert [asset.symbol for asset in matrix.assets] == ["AAPL", "MSFT", "NVDA"]
    assert matrix.assets[0].name == "Apple Inc."
    assert matrix.assets[0].weights == (0.6, 0.7)
    assert matrix.assets[1].weights == (0.4, 0.0)
    assert matrix.assets[2].weights == (0.0, 0.3)


def test_an_asset_without_details_is_ordered_by_its_identifier(recipes) -> None:
    frame = pd.DataFrame({"ALPACA::z": [0.5], "ALPACA::a": [0.5]}, index=pd.DatetimeIndex([FIRST]))
    recipes.answer("signal_history", {"signal-123": {"matrix": frame}})
    with patch(DETAILS, side_effect=_details):
        matrix = read_signal_observation_matrix(signal_uid="signal-123")

    assert recipes.calls == [("signal_history", {"signal_uids": ["signal-123"], "latest": 100})]
    assert [(asset.asset_identifier, asset.symbol) for asset in matrix.assets] == [
        ("ALPACA::a", "AAPL"),
        ("ALPACA::z", None),
    ]


def test_a_signal_with_nothing_published_has_an_empty_matrix(recipes) -> None:
    recipes.answer("signal_history", {})
    with patch(DETAILS) as details:
        matrix = read_signal_observation_matrix(signal_uid="signal-123")
    assert (matrix.time_indexes, matrix.assets) == ((), ())
    details.assert_not_called()


def test_signal_observation_bounds_of_many_signals_are_one_markets_call(recipes) -> None:
    last = dt.datetime(2026, 9, 7, 8, tzinfo=dt.UTC)
    recipes.answer(
        "signal_history",
        {
            "signal-a": SimpleNamespace(first_observation_at=FIRST, last_observation_at=last),
            "signal-b": {"first_observation_at": None, "last_observation_at": None},
        },
    )

    bounds = read_signal_observation_bounds_by_signal(["signal-a", "signal-b", "signal-c"])

    assert recipes.calls == [
        ("signal_history", {"signal_uids": ["signal-a", "signal-b", "signal-c"]})
    ]
    assert (bounds["signal-a"].first_observation_at, bounds["signal-a"].last_observation_at) == (
        FIRST,
        last,
    )
    # One with no observation, or one Markets does not answer, has no bounds.
    for uid in ("signal-b", "signal-c"):
        assert (bounds[uid].first_observation_at, bounds[uid].last_observation_at) == (None, None)


def test_one_signals_bounds(recipes) -> None:
    recipes.answer(
        "signal_history",
        {"signal-123": SimpleNamespace(first_observation_at=FIRST, last_observation_at=SECOND)},
    )
    bounds = read_signal_observation_bounds(signal_uid="signal-123")
    assert (bounds.first_observation_at, bounds.last_observation_at) == (FIRST, SECOND)


def test_a_time_without_a_timezone_is_refused(recipes) -> None:
    recipes.answer(
        "signal_history",
        {
            "signal-123": SimpleNamespace(
                first_observation_at=dt.datetime(2026, 9, 3), last_observation_at=None
            )
        },
    )
    with pytest.raises(RuntimeError, match="without a timezone"):
        read_signal_observation_bounds(signal_uid="signal-123")


@pytest.mark.parametrize("limit", [0, 101, True])
def test_signal_history_rejects_invalid_observation_limits(recipes, limit: int) -> None:
    with pytest.raises(ValueError, match="observation_limit"):
        read_signal_observation_matrix(signal_uid="signal-123", observation_limit=limit)
    assert recipes.calls == []
