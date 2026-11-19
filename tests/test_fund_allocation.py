"""Sizing an account's target and allocating a snapshot through the Markets API (ADR 0023).

Markets, the weights table and Alpaca are replaced by recording fakes, so these tests check what
the connector asks for and how it shapes the answers. No test needs the network.
"""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from alpaca_connectors.holdings import allocation
from alpaca_connectors.holdings.allocation import (
    SizedAccountTarget,
    account_exposures,
    allocate_sized_target,
    markets_refusal,
    read_asset_terms,
    size_account_target,
    size_and_allocate,
)
from alpaca_connectors.holdings.sizing import AssetTerms, Exposure, FundAllocationError

ACCOUNT = "account-uid"
POSITION_SET = "position-set-uid"
WHEN = dt.datetime(2026, 11, 16, 21, tzinfo=dt.UTC)
PORTFOLIO_ROW = {
    "target_type": "portfolio",
    "target_uid": "portfolio-a-uid",
    "portfolio_uid": "portfolio-a-uid",
    "weight_notional_exposure": "0.40",
    "constant_notional_exposure": None,
    "portfolio": {"uid": "portfolio-a-uid", "unique_identifier": "A"},
}
SIZED = SizedAccountTarget(
    position_set_uid=POSITION_SET,
    valuation_time=WHEN,
    sized_targets=[
        {
            "target_type": "portfolio",
            "target_uid": "portfolio-a-uid",
            "positions": [{"asset_uid": "aapl-uid", "quantity": 1200.0, "direction": 1}],
        }
    ],
)


def _target(*rows: Any, position_set_uid: str | None = POSITION_SET) -> Any:
    return SimpleNamespace(position_set_uid=position_set_uid, positions=list(rows))


# ------------------------------------------------------------------------------------------------
# The target's rows
# ------------------------------------------------------------------------------------------------
def test_exposures_are_the_rows_stated_as_a_share_or_an_amount() -> None:
    rows = [
        PORTFOLIO_ROW,
        SimpleNamespace(
            target_type="portfolio",
            target_uid="portfolio-b-uid",
            weight_notional_exposure=None,
            constant_notional_exposure="300000",
            portfolio=SimpleNamespace(unique_identifier="B"),
        ),
        {
            "target_type": "asset",
            "target_uid": "tsla-uid",
            "weight_notional_exposure": "0.05",
            "asset": {"unique_identifier": "TSLA"},
        },
        # Already in units: not sized.
        {"target_type": "asset", "target_uid": "usd-uid", "single_asset_quantity": "5000"},
    ]

    assert account_exposures(rows) == [
        Exposure("portfolio", "portfolio-a-uid", "A", weight=0.40),
        Exposure("portfolio", "portfolio-b-uid", "B", amount=300_000.0),
        Exposure("asset", "tsla-uid", "TSLA", weight=0.05),
    ]


@pytest.mark.parametrize(
    "row",
    [
        {"target_type": "portfolio", "target_uid": "p", "portfolio": {"unique_identifier": "A"}},
        {"target_type": "portfolio", "target_uid": "p", "weight_notional_exposure": "0.1"},
    ],
)
def test_a_row_that_cannot_be_sized_is_refused(row) -> None:
    with pytest.raises(FundAllocationError) as refused:
        account_exposures([row])

    assert refused.value.code == "invalid_account_target"


# ------------------------------------------------------------------------------------------------
# Sizing the target in force
# ------------------------------------------------------------------------------------------------
@pytest.fixture
def markets(monkeypatch) -> dict[str, Any]:
    """Recording stand-ins for the three Markets calls, the weights read and the asset read."""
    from alpaca_connectors import markets_api

    calls: dict[str, Any] = {"plan": [], "allocate": [], "weights": [], "assets": []}
    state: dict[str, Any] = {
        "calls": calls,
        "target": _target(PORTFOLIO_ROW),
        "plan": SimpleNamespace(
            policy={"id": "proportional_attribution", "version": "1.0.0", "arguments": {}},
            plan_digest="sha256:abc",
            apply_blockers=[],
        ),
        "allocation": SimpleNamespace(
            applied=True,
            already_applied=False,
            lines=[
                SimpleNamespace(
                    owner="fund",
                    target_portfolio_uid="portfolio-a-uid",
                    asset_uid="aapl-uid",
                    asset_identifier="AAPL",
                    target=1200.0,
                    assigned=1000.0,
                    delta=200.0,
                )
            ],
        ),
    }

    def answer(name: str) -> Any:
        if isinstance(state[name], Exception):
            raise state[name]
        return state[name]

    def plan(account_uid: Any, **request: Any) -> Any:
        calls["plan"].append((account_uid, request))
        return answer("plan")

    def allocate(account_uid: Any, **request: Any) -> Any:
        calls["allocate"].append((account_uid, request))
        return answer("allocation")

    def weights(identifiers: Any, *, at: dt.datetime) -> dict[str, dict[str, float]]:
        calls["weights"].append((list(identifiers), at))
        return {"A": {"AAPL": 0.6, "MSFT": 0.4}}

    def assets(identifiers: Any, *, price_reader: Any) -> dict[str, AssetTerms]:
        calls["assets"].append(list(identifiers))
        return {
            "AAPL": AssetTerms(asset_uid="aapl-uid", price=200.0, fractionable=False),
            "MSFT": AssetTerms(asset_uid="msft-uid", price=400.0, fractionable=False),
        }

    monkeypatch.setattr(markets_api, "account_target_positions", lambda uid: answer("target"))
    monkeypatch.setattr(markets_api, "plan_account_virtual_funds", plan)
    monkeypatch.setattr(markets_api, "allocate_account_virtual_funds", allocate)
    monkeypatch.setattr(allocation, "read_portfolio_weights", weights)
    monkeypatch.setattr(allocation, "read_asset_terms", assets)
    return state


def test_the_target_in_force_is_sized_at_the_snapshot_time(markets) -> None:
    sized = size_account_target(
        ACCOUNT, valuation_time=WHEN, equity=1_000_000, price_reader=lambda symbols: {}
    )

    assert sized == SizedAccountTarget(
        position_set_uid=POSITION_SET,
        valuation_time=WHEN,
        sized_targets=[
            {
                "target_type": "portfolio",
                "target_uid": "portfolio-a-uid",
                "positions": [
                    {"asset_uid": "aapl-uid", "quantity": 1200.0, "direction": 1},
                    {"asset_uid": "msft-uid", "quantity": 400.0, "direction": 1},
                ],
            }
        ],
    )
    # One weights read for every portfolio of the target, and one asset read for their legs.
    assert markets["calls"]["weights"] == [(["A"], WHEN)]
    assert markets["calls"]["assets"] == [["AAPL", "MSFT"]]


@pytest.mark.parametrize(
    "target",
    [None, _target(), _target(PORTFOLIO_ROW, position_set_uid=None)],
)
def test_an_account_with_no_target_sizes_nothing(markets, target) -> None:
    markets["target"] = target

    assert size_account_target(ACCOUNT, valuation_time=WHEN, equity=1.0, price_reader=dict) is None
    assert size_and_allocate(ACCOUNT, valuation_time=WHEN, equity=1.0, price_reader=dict) == (
        allocation.FundAllocationResult()
    )
    assert markets["calls"]["plan"] == []


# ------------------------------------------------------------------------------------------------
# Plan, then allocate
# ------------------------------------------------------------------------------------------------
def test_the_allocation_is_planned_then_written_with_the_plans_digest(markets) -> None:
    result = allocate_sized_target(ACCOUNT, SIZED)

    request = {
        "position_set_uid": POSITION_SET,
        "valuation_time": WHEN,
        "sized_targets": SIZED.sized_targets,
    }
    assert markets["calls"]["plan"] == [(ACCOUNT, request)]
    # The policy and the digest are the plan's, so a change since the plan is refused.
    assert markets["calls"]["allocate"] == [
        (ACCOUNT, {**request, "policy": markets["plan"].policy, "plan_digest": "sha256:abc"})
    ]
    assert (result.position_set_uid, result.allocation_applied) == (POSITION_SET, True)
    assert result.lines == [
        {
            "owner": "fund",
            "target_portfolio_uid": "portfolio-a-uid",
            "asset_uid": "aapl-uid",
            "asset_identifier": "AAPL",
            "target": 1200.0,
            "assigned": 1000.0,
            "delta": 200.0,
        }
    ]


def test_the_same_sync_again_is_a_success_that_wrote_nothing(markets) -> None:
    markets["allocation"].applied = False
    markets["allocation"].already_applied = True

    assert allocate_sized_target(ACCOUNT, SIZED).allocation_applied is False


def test_a_retired_portfolio_stops_before_the_write(markets) -> None:
    markets["plan"].apply_blockers = [{"code": "portfolio_retired", "portfolio_uid": "secret"}]

    with pytest.raises(FundAllocationError) as refused:
        allocate_sized_target(ACCOUNT, SIZED)

    assert refused.value.code == "portfolio_retired"
    assert "secret" not in str(refused.value)
    assert markets["calls"]["allocate"] == []


@pytest.mark.parametrize(
    "code",
    [
        "target_after_snapshot",
        "invalid_sized_targets",
        "plan_changed",
        "allocation_exists",
        "policy_version_mismatch",
        "portfolio_retired",
        "not_found",
    ],
)
def test_a_markets_refusal_becomes_the_connectors_own_error(markets, code) -> None:
    from msm.api.remote import MarketsConflictError

    detail = f"{{'code': '{code}', 'message': 'PositionSet 1234 of account ABC', 'errors': []}}"
    markets["allocation"] = MarketsConflictError(detail)

    with pytest.raises(FundAllocationError) as refused:
        allocate_sized_target(ACCOUNT, SIZED)

    assert refused.value.code == code
    # Markets' detail names stored rows; it is kept as the cause and never answered.
    assert "1234" not in str(refused.value)
    assert isinstance(refused.value.__cause__, MarketsConflictError)


def test_a_missing_snapshot_is_not_found_and_other_failures_pass_through() -> None:
    assert markets_refusal(LookupError("no holdings set")).code == "not_found"
    failure = RuntimeError("connection reset")
    assert markets_refusal(failure) is failure


# ------------------------------------------------------------------------------------------------
# The lookups are set-based
# ------------------------------------------------------------------------------------------------
def test_asset_terms_are_one_details_read_and_one_price_read(monkeypatch) -> None:
    from alpaca_connectors.assets import resolution

    aapl, msft = (str(uuid.uuid4()) for _ in range(2))
    calls: list[Any] = []

    def details(identifiers: Any) -> dict[str, Any]:
        calls.append(("details", list(identifiers)))
        # "GONE" has no Alpaca details row, so it is absent; no Markets read is made.
        return {
            "AAPL": {"asset_uid": aapl, "symbol": "AAPL", "fractionable": True},
            "MSFT": {"asset_uid": msft, "symbol": "MSFT", "fractionable": False},
        }

    def prices(symbols: Any) -> dict[str, float]:
        calls.append(("prices", list(symbols)))
        return {"AAPL": 200.0}

    monkeypatch.setattr(resolution, "alpaca_details_by_identifier", details)

    terms = read_asset_terms(["AAPL", "MSFT", "GONE"], price_reader=prices)

    assert calls == [("details", ["AAPL", "MSFT", "GONE"]), ("prices", ["AAPL", "MSFT"])]
    # An asset with no Alpaca details is left out, and one with no latest trade has no price.
    assert terms == {
        "AAPL": AssetTerms(asset_uid=aapl, price=200.0, fractionable=True),
        "MSFT": AssetTerms(asset_uid=msft, price=None, fractionable=False),
    }


def test_the_three_markets_calls_go_through_the_process_backend(monkeypatch) -> None:
    from msm.api import remote

    from alpaca_connectors import markets_api

    calls: list[Any] = []
    client = SimpleNamespace(
        account_target_positions=lambda uid: calls.append(("target", uid)) or "target",
        plan_account_virtual_funds=lambda uid, **kw: calls.append(("plan", uid, kw)) or "plan",
        allocate_account_virtual_funds=lambda uid, **kw: (
            calls.append(("allocate", uid, kw)) or "allocation"
        ),
    )
    monkeypatch.setattr(remote, "_remote_backend", SimpleNamespace(client=client))
    request = {"position_set_uid": POSITION_SET, "valuation_time": WHEN, "sized_targets": []}

    assert markets_api.account_target_positions(ACCOUNT) == "target"
    assert markets_api.plan_account_virtual_funds(ACCOUNT, **request) == "plan"
    assert (
        markets_api.allocate_account_virtual_funds(
            ACCOUNT, **request, policy="policy", plan_digest="digest"
        )
        == "allocation"
    )
    assert calls == [
        ("target", ACCOUNT),
        ("plan", ACCOUNT, request),
        ("allocate", ACCOUNT, {**request, "policy": "policy", "plan_digest": "digest"}),
    ]
