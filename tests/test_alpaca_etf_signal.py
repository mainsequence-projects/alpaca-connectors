from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest
from msm_portfolios.data_nodes.constants import ASSET_IDENTIFIER
from msm_portfolios.data_nodes.signals.storage import SignalWeightsStorage
from msm_portfolios.data_nodes.signals.weights import SignalWeights

from src.portfolios.alpaca_etf_signal import (
    AlpacaETFHoldingsSignal,
    AlpacaETFHoldingsSignalConfig,
)


def build_signal(*, universe_uid: str = "universe-uid", account_uid: str = "account-uid"):
    signal = AlpacaETFHoldingsSignal.__new__(AlpacaETFHoldingsSignal)
    signal.signal_configuration = AlpacaETFHoldingsSignalConfig(
        universe_uid=universe_uid,
        account_uid=account_uid,
    )
    return signal


def build_materialization(*, observed_at: dt.datetime | None = None):
    return SimpleNamespace(
        observed_at=observed_at or dt.datetime(2026, 9, 4, 8, 30, tzinfo=dt.UTC),
        component_weights_by_symbol={"AAPL": 60.0, "MSFT": 40.0},
        asset_identifiers_by_symbol={
            "AAPL": "ALPACA::aapl-uuid",
            "MSFT": "ALPACA::msft-uuid",
        },
    )


def test_config_contains_only_universe_and_runtime_account() -> None:
    assert set(AlpacaETFHoldingsSignalConfig.model_fields) == {
        "universe_uid",
        "account_uid",
    }
    with pytest.raises(ValueError, match="must not be empty"):
        AlpacaETFHoldingsSignalConfig(universe_uid="", account_uid="account-uid")


def test_account_changes_runtime_config_but_not_signal_business_identity() -> None:
    first = build_signal(account_uid="account-a")
    second = build_signal(account_uid="account-b")

    assert first.alpaca_etf_config.model_dump() != second.alpaca_etf_config.model_dump()
    assert first._signal_uid_payload()["config"] == {"universe_uid": "universe-uid"}
    assert first.signal_uid == second.signal_uid
    assert build_signal(universe_uid="other-universe").signal_uid != first.signal_uid


def test_signal_uses_canonical_ms_markets_storage() -> None:
    assert AlpacaETFHoldingsSignal._required_output_table() is SignalWeightsStorage
    assert SignalWeightsStorage.__index_names__ == [
        "time_index",
        "signal_uid",
        "asset_identifier",
    ]
    assert "account_uid" not in SignalWeightsStorage.__table__.columns
    assert "universe_uid" not in SignalWeightsStorage.__table__.columns


def test_each_update_materializes_once_and_returns_one_batched_observation() -> None:
    signal = build_signal()
    materialization = build_materialization()

    with patch(
        "src.universes.services.materialize_asset_universe",
        return_value=materialization,
    ) as materialize:
        frame = signal._calculate_signal_weights()

    materialize.assert_called_once_with(
        "universe-uid",
        account_uid="account-uid",
        plan=None,
    )
    assert list(frame.index.names) == ["time_index", ASSET_IDENTIFIER]
    assert len(frame) == 2
    assert set(frame.index.get_level_values(ASSET_IDENTIFIER)) == {
        "ALPACA::aapl-uuid",
        "ALPACA::msft-uuid",
    }
    assert frame.index.get_level_values("time_index").unique().tolist() == [
        pd.Timestamp(materialization.observed_at)
    ]
    assert frame["signal_weight"].sum() == pytest.approx(1.0)


def test_unchanged_extractions_are_still_distinct_observations() -> None:
    signal = build_signal()
    first = build_materialization(
        observed_at=dt.datetime(2026, 9, 4, 8, 30, tzinfo=dt.UTC)
    )
    second = build_materialization(
        observed_at=dt.datetime(2026, 9, 4, 9, 30, tzinfo=dt.UTC)
    )

    with patch(
        "src.universes.services.materialize_asset_universe",
        side_effect=[first, second],
    ):
        first_frame = signal._calculate_signal_weights()
        second_frame = signal._calculate_signal_weights()

    assert not first_frame.empty
    assert not second_frame.empty
    assert first_frame.index.get_level_values("time_index")[0] != second_frame.index.get_level_values(
        "time_index"
    )[0]


def test_prepared_plan_is_transient_and_consumed_by_the_next_update() -> None:
    signal = build_signal()
    plan = SimpleNamespace(universe_uid="universe-uid", account_uid="account-uid")
    materialization = build_materialization()
    signal.set_prepared_universe_plan(plan)

    with patch(
        "src.universes.services.materialize_asset_universe",
        return_value=materialization,
    ) as materialize:
        signal._calculate_signal_weights()

    materialize.assert_called_once_with(
        "universe-uid",
        account_uid="account-uid",
        plan=plan,
    )
    assert not hasattr(signal, "_prepared_universe_plan")


def test_signal_read_includes_prior_valid_observation_for_session_close_alignment() -> None:
    signal = build_signal()
    valuation_time = pd.Timestamp("2026-09-04T20:00:00Z")
    expected = pd.DataFrame({"signal_weight": [1.0]})

    with patch.object(
        SignalWeights,
        "_get_signal_weights_between_dates",
        return_value=expected,
    ) as get_weights:
        actual = signal._get_signal_weights_between_dates(
            weights_source="canonical-signal-storage",
            start_date=valuation_time,
            end_date=valuation_time,
        )

    assert actual is expected
    get_weights.assert_called_once_with(
        weights_source="canonical-signal-storage",
        start_date=valuation_time - dt.timedelta(days=90),
        end_date=valuation_time,
        dimension_range_map=None,
    )
