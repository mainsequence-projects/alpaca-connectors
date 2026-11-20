"""Signal weights, portfolio weights and portfolio values are Markets' published series (ADR 0031).

This application reaches them only through the ``markets_api`` composites over the ms-markets
recipes (ms-markets ADR 0060), faked here at that boundary, and never through a table of its own.
"""

from __future__ import annotations

import ast
import datetime as dt
import functools
from pathlib import Path
from types import SimpleNamespace

import pytest

from alpaca_connectors import markets_api

SRC = Path(__file__).resolve().parents[1] / "src" / "alpaca_connectors"
AT = dt.datetime(2026, 11, 18, 21, tzinfo=dt.UTC)


def test_weights_as_of_reads_every_portfolio_in_batches(recipes) -> None:
    identifiers = [f"ALPACA_PORTFOLIO__{index:032X}" for index in range(501)]
    recipes.answer(
        "weights_as_of",
        lambda *, portfolio_identifiers, at: {
            portfolio_identifiers[0]: {"ALPACA::a": 0.6, "ALPACA::b": "0.4", "ALPACA::c": None},
            # A portfolio with no weights by then may be answered empty.
            portfolio_identifiers[-1]: {},
        },
    )

    weights = markets_api.portfolio_weights_as_of(identifiers, at=AT)

    assert [len(kwargs["portfolio_identifiers"]) for _, kwargs in recipes.calls] == [500, 1]
    assert all(kwargs["at"] == AT for _, kwargs in recipes.calls)
    assert weights == {identifiers[0]: {"ALPACA::a": 0.6, "ALPACA::b": 0.4}}


def test_the_sync_sizes_with_the_weights_markets_answers(recipes) -> None:
    from alpaca_connectors.holdings.allocation import read_portfolio_weights

    recipes.answer("weights_as_of", {"P1": {"ALPACA::a": 1.0}})
    assert read_portfolio_weights(["P1", "P1", "P2"], at=AT) == {"P1": {"ALPACA::a": 1.0}}
    assert recipes.calls == [("weights_as_of", {"portfolio_identifiers": ["P1", "P2"], "at": AT})]
    # Nothing to read, no call.
    assert read_portfolio_weights([], at=AT) == {}
    assert len(recipes.calls) == 1


def test_signal_bounds_read_many_signals_in_batches(recipes) -> None:
    uids = [f"signal-{index}" for index in range(501)]
    recipes.answer("signal_history", {})
    bounds = markets_api.signal_bounds(uids)
    assert [len(kwargs["signal_uids"]) for _, kwargs in recipes.calls] == [500, 1]
    assert set(bounds) == set(uids)


def test_the_run_answer_is_translated_once_here(recipes) -> None:
    recipes.answer(
        "run_portfolio",
        {"portfolio": {"uid": "p-uid"}, "written": {"values": "3"}, "first_rebalance": None},
    )
    answer = markets_api.run_portfolio(
        portfolio_configuration="configuration",
        portfolio_description="description",
        unique_identifier="ALPACA_PORTFOLIO__A",
        calendar_uid="calendar-uid",
        signal_uid="signal-uid",
        start_at=AT,
        calendar_events=None,
        rebalance_output_table="rebalance-table",
    )
    assert answer == markets_api.PortfolioRunAnswer(
        portfolio_uid="p-uid",
        portfolio_unique_identifier="ALPACA_PORTFOLIO__A",
        written={"values": 3},
        first_rebalance=None,
    )


def test_private_series_are_deleted_in_batches_of_this_applications_tables(recipes) -> None:
    recipes.answer("delete_private_series", None)
    identifiers = [f"P{index}" for index in range(501)]
    markets_api.delete_private_series(tables=["table"], portfolio_identifiers=identifiers)
    assert [kwargs["tables"] for _, kwargs in recipes.calls] == [["table"], ["table"]]
    assert [len(kwargs["portfolio_identifiers"]) for _, kwargs in recipes.calls] == [500, 1]


def test_an_account_delete_removes_only_the_private_rebalance_state(recipes) -> None:
    from alpaca_connectors.account.deletion import delete_portfolio_series
    from alpaca_connectors.instances import AlpacaPortfolioRebalanceStateStorage

    recipes.answer("delete_private_series", None)
    delete_portfolio_series(["ALPACA_PORTFOLIO__A"])
    # Markets deletes the portfolio's weights and values with the Portfolio (ADR 0031).
    assert recipes.calls == [
        (
            "delete_private_series",
            {
                "tables": [AlpacaPortfolioRebalanceStateStorage],
                "portfolio_identifiers": ["ALPACA_PORTFOLIO__A"],
            },
        )
    ]


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (SimpleNamespace(rows=[], total_count=None), (0, [])),
        (
            {"rows": [{"time_index": AT, "close": 1.0, "return": 0.1}], "total_count": 9},
            (9, [AT]),
        ),
    ],
)
def test_portfolio_values_name_the_return_as_this_application_does(
    recipes, answer, expected
) -> None:
    recipes.answer("portfolio_history", answer)
    page = markets_api.portfolio_values("ALPACA_PORTFOLIO__A", limit=5)
    assert (page.total_count, [row["time_index"] for row in page.rows]) == expected
    for row in page.rows:
        assert row["period_return"] == 0.1 and "return" not in row


# ------------------------------------------------------------------------------------------------
# Nothing else reaches the published series
# ------------------------------------------------------------------------------------------------
_RETIRED_INSTANCES = {
    "AlpacaSignalWeightsStorage",
    "AlpacaPortfolioWeightsStorage",
    "AlpacaPortfoliosStorage",
}
# The instances are built and kept in the migration metadata until a later revision drops them;
# the history move of `instances backfill|rollback` goes with them (ADR 0031, Rollout step 8).
_KEEPS_THE_RETIRED_INSTANCES = {"instances.py", "operations/instance_backfill.py"}


@functools.cache
def _python_files() -> list[tuple[str, ast.Module]]:
    return [
        (path.relative_to(SRC).as_posix(), ast.parse(path.read_text()))
        for path in sorted(SRC.rglob("*.py"))
        if not path.relative_to(SRC).as_posix().startswith("migrations/versions/")
    ]


def _names(tree: ast.Module) -> list[tuple[int, str]]:
    """Every name a module uses, reads an attribute of, or imports, with its line."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.append((node.lineno, node.id))
        elif isinstance(node, ast.Attribute):
            found.append((node.lineno, node.attr))
        elif isinstance(node, ast.alias):
            found.append((node.lineno, node.name))
        elif isinstance(node, ast.ImportFrom):
            found.append((node.lineno, node.module or ""))
    return found


def test_no_module_reads_or_writes_the_three_retired_instances() -> None:
    violations = [
        f"{relative}:{line} {name}"
        for relative, tree in _python_files()
        if relative not in _KEEPS_THE_RETIRED_INSTANCES
        for line, name in _names(tree)
        if name in _RETIRED_INSTANCES
    ]
    assert violations == [], violations


def test_only_the_markets_api_composites_call_the_recipes() -> None:
    series = {"SignalWeightsSeries", "PortfolioWeightsSeries", "PortfolioValuesSeries"}
    violations = [
        f"{relative}:{line} {name}"
        for relative, tree in _python_files()
        if relative != "markets_api.py"
        for line, name in _names(tree)
        if name.startswith("msm_portfolios.recipes") or name in series
    ]
    assert violations == [], violations


def test_nothing_registers_with_or_shares_to_markets() -> None:
    """Decision 3: no catalog registration, no second namespace, no access tooling."""
    forbidden = {
        "PortfolioDatasets",
        "msm_portfolios.api.datasets",
        "register_portfolio_datasets",
        "register_signal_dataset",
        "share_with_markets",
        "MARKETS_INSTANCES_NAMESPACE",
        "__metatable_namespaces__",
        "grant_markets_access",
        "markets_instances_report",
    }
    violations = [
        f"{relative}:{line} {name}"
        for relative, tree in _python_files()
        # The history move ends with ms-markets' catalog calls until Rollout step 8 removes it.
        if relative != "operations/instance_backfill.py"
        for line, name in _names(tree)
        if name in forbidden
    ]
    assert violations == [], violations
    for removed in (
        "operations/markets_access.py",
        "portfolios/datasets.py",
        "portfolios/signal_history.py",
    ):
        assert not (SRC / removed).exists(), removed
