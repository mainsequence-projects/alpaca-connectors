"""The sizing rule of ADR 0023: exposures into target units, without Alpaca or Markets."""

from __future__ import annotations

import pytest

from alpaca_connectors.holdings.sizing import (
    AssetTerms,
    Exposure,
    FundAllocationError,
    size_targets,
    size_units,
)

PORTFOLIO_A = "portfolio-a-uid"
PORTFOLIO_B = "portfolio-b-uid"
WEIGHTS = {
    "A": {"AAPL": 0.6, "MSFT": 0.4},
    "B": {"MSFT": 0.5, "JNJ": 0.5},
}
ASSETS = {
    "AAPL": AssetTerms(asset_uid="aapl-uid", price=200.0, fractionable=False),
    "MSFT": AssetTerms(asset_uid="msft-uid", price=400.0, fractionable=False),
    "JNJ": AssetTerms(asset_uid="jnj-uid", price=150.0, fractionable=False),
    "TSLA": AssetTerms(asset_uid="tsla-uid", price=250.0, fractionable=True),
}


def _positions(sized: list[dict], target_uid: str) -> dict[str, tuple[float, int]]:
    entry = next(item for item in sized if item["target_uid"] == target_uid)
    return {row["asset_uid"]: (row["quantity"], row["direction"]) for row in entry["positions"]}


def test_the_worked_example_of_the_adr() -> None:
    sized = size_targets(
        [
            Exposure("portfolio", PORTFOLIO_A, "A", weight=0.40),
            Exposure("portfolio", PORTFOLIO_B, "B", amount=300_000),
        ],
        equity=1_000_000,
        weights=WEIGHTS,
        assets=ASSETS,
    )

    assert [item["target_type"] for item in sized] == ["portfolio", "portfolio"]
    assert _positions(sized, PORTFOLIO_A) == {"aapl-uid": (1200.0, 1), "msft-uid": (400.0, 1)}
    assert _positions(sized, PORTFOLIO_B) == {"msft-uid": (375.0, 1), "jnj-uid": (1000.0, 1)}


def test_an_asset_exposure_is_sized_with_a_weight_of_one() -> None:
    sized = size_targets(
        [Exposure("asset", "tsla-uid", "TSLA", weight=0.05)],
        equity=1_000_000,
        weights={},
        assets=ASSETS,
    )

    assert sized == [
        {
            "target_type": "asset",
            "target_uid": "tsla-uid",
            "positions": [{"asset_uid": "tsla-uid", "quantity": 200.0, "direction": 1}],
        }
    ]


@pytest.mark.parametrize(
    ("raw", "fractionable", "expected"),
    [
        (3.93, False, 4.0),
        (3.5, False, 4.0),
        (3.49, False, 3.0),
        (0.49, False, 0.0),
        (-2.5, False, -3.0),
        (3.123456789123, True, 3.123456789),
        (-0.25, True, -0.25),
    ],
)
def test_units_are_whole_and_half_up_unless_fractionable(raw, fractionable, expected) -> None:
    assert size_units(raw, fractionable=fractionable) == expected


def test_a_negative_weight_is_a_short_with_a_positive_quantity() -> None:
    sized = size_targets(
        [Exposure("portfolio", PORTFOLIO_A, "A", amount=100_000)],
        equity=None,
        weights={"A": {"AAPL": 1.3, "MSFT": -0.3}},
        assets=ASSETS,
    )

    assert _positions(sized, PORTFOLIO_A) == {"aapl-uid": (650.0, 1), "msft-uid": (75.0, -1)}


def test_an_asset_that_rounds_to_zero_has_no_position() -> None:
    sized = size_targets(
        [Exposure("portfolio", PORTFOLIO_A, "A", amount=250)],
        equity=None,
        weights=WEIGHTS,
        assets=ASSETS,
    )

    # 150 / 200 = 0.75 rounds to 1 AAPL; 100 / 400 = 0.25 rounds to nothing.
    assert _positions(sized, PORTFOLIO_A) == {"aapl-uid": (1.0, 1)}


def test_a_fund_sized_to_nothing_keeps_its_entry() -> None:
    sized = size_targets(
        [Exposure("portfolio", PORTFOLIO_A, "A", weight=0.4)],
        equity=0,
        weights=WEIGHTS,
        assets=ASSETS,
    )

    assert sized == [{"target_type": "portfolio", "target_uid": PORTFOLIO_A, "positions": []}]


def test_an_amount_needs_no_equity_but_a_share_does() -> None:
    amount = [Exposure("portfolio", PORTFOLIO_A, "A", amount=1000)]
    share = [Exposure("portfolio", PORTFOLIO_A, "A", weight=0.1)]

    assert size_targets(amount, equity=None, weights=WEIGHTS, assets=ASSETS)
    with pytest.raises(FundAllocationError) as refused:
        size_targets(share, equity=None, weights=WEIGHTS, assets=ASSETS)
    assert refused.value.code == "account_equity_missing"


@pytest.mark.parametrize(
    ("weights", "assets", "code"),
    [
        ({}, ASSETS, "portfolio_weights_missing"),
        ({"A": {}}, ASSETS, "portfolio_weights_missing"),
        ({"A": {"NVDA": 1.0}}, ASSETS, "asset_unresolved"),
        (
            {"A": {"AAPL": 1.0}},
            {"AAPL": AssetTerms(asset_uid="aapl-uid", price=None, fractionable=False)},
            "asset_price_missing",
        ),
    ],
)
def test_nothing_is_sized_with_part_of_a_target(weights, assets, code) -> None:
    with pytest.raises(FundAllocationError) as refused:
        size_targets(
            [Exposure("portfolio", PORTFOLIO_A, "A", amount=1000)],
            equity=None,
            weights=weights,
            assets=assets,
        )

    assert refused.value.code == code
    assert "AAPL" not in str(refused.value) and "NVDA" not in str(refused.value)
