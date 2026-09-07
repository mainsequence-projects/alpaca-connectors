from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from msm.repositories.base import MarketsRepositoryContext

from src.portfolios.signal_history import (
    read_signal_observation_bounds,
    read_signal_observation_matrix,
)


def test_signal_history_reads_all_assets_for_the_latest_distinct_observations_once() -> None:
    first = dt.datetime(2026, 9, 3, 14, tzinfo=dt.UTC)
    second = dt.datetime(2026, 9, 4, 14, tzinfo=dt.UTC)
    rows = [
        {
            "time_index": second,
            "asset_identifier": "ALPACA::a",
            "signal_weight": 0.7,
            "symbol": "AAPL",
            "name": "Apple Inc.",
        },
        {
            "time_index": first,
            "asset_identifier": "ALPACA::b",
            "signal_weight": 0.4,
            "symbol": "MSFT",
            "name": "Microsoft Corporation",
        },
        {
            "time_index": first,
            "asset_identifier": "ALPACA::a",
            "signal_weight": 0.6,
            "symbol": "AAPL",
            "name": "Apple Inc.",
        },
        {
            "time_index": second,
            "asset_identifier": "ALPACA::c",
            "signal_weight": None,
            "symbol": "NVDA",
            "name": "NVIDIA Corporation",
        },
    ]
    context = MarketsRepositoryContext()

    with (
        patch(
            "src.runtime.start_markets_engine",
            return_value=SimpleNamespace(context=context),
        ),
        patch(
            "msm.repositories.base.compile_markets_statement",
            return_value="signal-history-query",
        ) as compile_statement,
        patch(
            "msm.repositories.base.execute_markets_operation",
            return_value={"rows": rows, "truncated": False},
        ) as execute_operation,
    ):
        matrix = read_signal_observation_matrix(
            signal_uid="signal-123",
            observation_limit=2,
        )

    assert matrix.signal_uid == "signal-123"
    assert matrix.time_indexes == (first, second)
    assert [asset.symbol for asset in matrix.assets] == ["AAPL", "MSFT", "NVDA"]
    assert matrix.assets[0].weights == (0.6, 0.7)
    assert matrix.assets[1].weights == (0.4, 0.0)
    assert matrix.assets[2].weights == (0.0, None)

    compile_statement.assert_called_once()
    statement = compile_statement.call_args.args[0]
    compiled_sql = str(statement)
    assert "WITH latest_signal_observations AS" in compiled_sql
    assert "GROUP BY" in compiled_sql
    assert "LIMIT" in compiled_sql
    assert set(statement.compile().params.values()) >= {"signal-123", 2}
    query_context = compile_statement.call_args.kwargs["context"]
    assert query_context.limits == {
        "max_rows": 100_000,
        "statement_timeout_ms": 30_000,
    }
    execute_operation.assert_called_once_with(
        "signal-history-query",
        context=query_context,
    )


def test_signal_history_refuses_a_truncated_constituent_result() -> None:
    context = MarketsRepositoryContext()
    with (
        patch(
            "src.runtime.start_markets_engine",
            return_value=SimpleNamespace(context=context),
        ),
        patch(
            "msm.repositories.base.compile_markets_statement",
            return_value="signal-history-query",
        ),
        patch(
            "msm.repositories.base.execute_markets_operation",
            return_value={"rows": [], "truncated": True},
        ),
        pytest.raises(RuntimeError, match="100,000 rows"),
    ):
        read_signal_observation_matrix(signal_uid="signal-123")


def test_signal_observation_bounds_use_one_aggregate_query() -> None:
    first = dt.datetime(2026, 9, 3, 14, tzinfo=dt.UTC)
    last = dt.datetime(2026, 9, 7, 8, tzinfo=dt.UTC)
    context = MarketsRepositoryContext()
    with (
        patch(
            "src.runtime.start_markets_engine",
            return_value=SimpleNamespace(context=context),
        ),
        patch(
            "msm.repositories.base.compile_markets_statement",
            return_value="signal-bounds-query",
        ) as compile_statement,
        patch(
            "msm.repositories.base.execute_markets_operation",
            return_value={
                "rows": [
                    {
                        "first_observation_at": first,
                        "last_observation_at": last,
                    }
                ]
            },
        ) as execute_operation,
    ):
        bounds = read_signal_observation_bounds(signal_uid="signal-123")

    assert bounds.first_observation_at == first
    assert bounds.last_observation_at == last
    statement = compile_statement.call_args.args[0]
    assert "min(" in str(statement).lower()
    assert "max(" in str(statement).lower()
    query_context = compile_statement.call_args.kwargs["context"]
    assert query_context.limits == {
        "max_rows": 1,
        "statement_timeout_ms": 30_000,
    }
    execute_operation.assert_called_once_with("signal-bounds-query", context=query_context)


@pytest.mark.parametrize("limit", [0, 101, True])
def test_signal_history_rejects_invalid_observation_limits(limit: int) -> None:
    with pytest.raises(ValueError, match="observation_limit"):
        read_signal_observation_matrix(signal_uid="signal-123", observation_limit=limit)
