"""The one-off copy into this application's time-series instances (ms-markets ADR 0047)."""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from typing import Any

import pytest
from msm_portfolios.time_index.signals.storage import SignalWeightsStorage

from alpaca_connectors.instances import AlpacaSignalWeightsStorage
from alpaca_connectors.operations import instance_backfill
from alpaca_connectors.operations.instance_backfill import BackfillPair, backfill_instances


def _sql(statement: Any) -> str:
    return str(statement.compile(compile_kwargs={"literal_binds": True}))


@pytest.fixture
def fake_backend(monkeypatch):
    executed: list[Any] = []
    pair = BackfillPair(
        "signal_weights",
        SignalWeightsStorage,
        AlpacaSignalWeightsStorage,
        "signal_uid",
        ("signal-a",),
    )
    rows = [
        {
            "time_index": dt.datetime(2026, 1, 1 + index, tzinfo=dt.UTC),
            "signal_uid": "signal-a",
            "asset_identifier": "A",
            "signal_weight": 1.0,
        }
        for index in range(3)
    ]

    def execute(statement, **kwargs):
        executed.append(statement)
        sql = _sql(statement)
        if "count(*)" in sql:
            return {"rows": [{"count": 3 if "ms_markets__" in sql else 0}]}
        if sql.startswith("SELECT"):
            offset = int(sql.rsplit("OFFSET", 1)[1]) if "OFFSET" in sql else 0
            return {"rows": rows[offset : offset + 2]}
        return {"rows": []}

    monkeypatch.setattr(instance_backfill, "backfill_pairs", lambda: [pair])
    monkeypatch.setattr(instance_backfill, "backfill_runtime_models", lambda: [])
    monkeypatch.setattr(
        "alpaca_connectors.runtime.start_markets_engine",
        lambda **kwargs: SimpleNamespace(context=object()),
    )
    monkeypatch.setattr(
        "msm.repositories.base.compile_markets_statement", lambda statement, **kwargs: statement
    )
    monkeypatch.setattr("msm.repositories.base.execute_markets_operation", execute)
    return executed


def test_dry_run_counts_source_and_destination_and_writes_nothing(fake_backend) -> None:
    result = backfill_instances()

    (table,) = result["tables"]
    assert table["source_rows"] == 3 and table["destination_rows_before"] == 0
    assert table["rows_to_read"] == 3
    assert all(_sql(statement).startswith("SELECT") for statement in fake_backend)


def test_execute_copies_scoped_rows_in_grain_order_and_ignores_existing_rows(fake_backend) -> None:
    result = backfill_instances(execute=True, batch_size=2)

    assert result["tables"][0]["rows_read"] == 3
    selects = [
        _sql(statement)
        for statement in fake_backend
        if _sql(statement).startswith("SELECT") and "count(*)" not in _sql(statement)
    ]
    inserts = [
        _sql(statement) for statement in fake_backend if _sql(statement).startswith("INSERT")
    ]
    assert len(selects) == 2 and len(inserts) == 2
    assert "FROM ms_markets__signalweightsts" in selects[0]
    assert "ms_markets__signalweightsts.signal_uid IN ('signal-a')" in selects[0]
    assert "ORDER BY ms_markets__signalweightsts.time_index" in selects[0]
    assert inserts[0].startswith("INSERT INTO alpaca_connectors__signalweightsts__main")
    assert "ON CONFLICT (time_index, signal_uid, asset_identifier) DO NOTHING" in inserts[0]


def test_backfill_is_the_only_module_that_reads_markets_time_series() -> None:
    """Every other module reaches Markets through row calls; see test_markets_api_boundary."""
    assert "data_viewer" in instance_backfill.__doc__
