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

import pandas as pd
from msm_portfolios.recipes import PortfolioHistory, PortfolioRun, SignalHistory
from msm_portfolios.services.series import SeriesBounds, SeriesPublication

from alpaca_connectors import markets_api

SRC = Path(__file__).resolve().parents[1] / "src" / "alpaca_connectors"
AT = dt.datetime(2026, 11, 18, 21, tzinfo=dt.UTC)


def _weights_frame(rows):
    """``weights_as_of``'s answer: indexed by time, portfolio and asset, a ``weight`` column."""
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(
        rows, columns=["time_index", "portfolio_identifier", "asset_identifier", "weight"]
    ).set_index(["time_index", "portfolio_identifier", "asset_identifier"])


def test_weights_as_of_reads_every_portfolio_in_batches(recipes) -> None:
    identifiers = [f"ALPACA_PORTFOLIO__{index:032X}" for index in range(501)]
    recipes.answer(
        "weights_as_of",
        lambda portfolio_identifiers, at=None: _weights_frame(
            [
                (AT, portfolio_identifiers[0], "ALPACA::a", 0.6),
                (AT, portfolio_identifiers[0], "ALPACA::b", 0.4),
                (AT, portfolio_identifiers[0], "ALPACA::c", float("nan")),
            ]
            if len(portfolio_identifiers) > 1
            else []
        ),
    )

    weights = markets_api.portfolio_weights_as_of(identifiers, at=AT)

    assert [len(arguments["portfolio_identifiers"]) for _, arguments in recipes.calls] == [500, 1]
    assert all(arguments["at"] == AT for _, arguments in recipes.calls)
    # A missing weight is left out, and a portfolio with no weights by then is absent.
    assert weights == {identifiers[0]: {"ALPACA::a": 0.6, "ALPACA::b": 0.4}}


def test_the_sync_sizes_with_the_weights_markets_answers(recipes) -> None:
    from alpaca_connectors.holdings.allocation import read_portfolio_weights

    recipes.answer("weights_as_of", _weights_frame([(AT, "P1", "ALPACA::a", 1.0)]))
    assert read_portfolio_weights(["P1", "P1", "P2"], at=AT) == {"P1": {"ALPACA::a": 1.0}}
    assert recipes.calls == [("weights_as_of", {"portfolio_identifiers": ["P1", "P2"], "at": AT})]
    # Nothing to read, no call.
    assert read_portfolio_weights([], at=AT) == {}
    assert len(recipes.calls) == 1


def test_signal_bounds_read_many_signals_in_batches(recipes) -> None:
    uids = [f"signal-{index}" for index in range(501)]
    recipes.answer(
        "signal_history",
        lambda signal_uids, latest=0: SignalHistory(
            bounds={
                uid: SeriesBounds(key=uid, first_time=AT, last_time=AT, row_count=1)
                for uid in signal_uids[:1]
            }
        ),
    )
    bounds = markets_api.signal_bounds(uids)
    assert [len(arguments["signal_uids"]) for _, arguments in recipes.calls] == [500, 1]
    assert set(bounds) == set(uids)
    assert bounds["signal-0"].first_observation_at == AT
    assert bounds["signal-1"].first_observation_at is None


def test_the_run_answer_is_translated_once_here(recipes) -> None:
    recipes.answer(
        "run_portfolio",
        PortfolioRun(
            portfolio=SimpleNamespace(uid="p-uid", unique_identifier="ALPACA_PORTFOLIO__A"),
            weights=None,
            values=SeriesPublication(key="ALPACA_PORTFOLIO__A", written=3, skipped=1),
            first_rebalance=False,
        ),
    )
    answer = markets_api.run_portfolio(
        "configuration",
        unique_identifier="ALPACA_PORTFOLIO__A",
        calendar_uid="calendar-uid",
        portfolio_description="description",
        rebalance_output_table="rebalance-table",
    )
    assert answer == markets_api.PortfolioRunAnswer(
        portfolio_uid="p-uid",
        portfolio_unique_identifier="ALPACA_PORTFOLIO__A",
        written={"weights": 0, "values": 3},
        first_rebalance=False,
    )
    [(_, arguments)] = recipes.calls
    assert arguments["portfolio_configuration"] == "configuration"
    assert arguments["rebalance_output_table"] == "rebalance-table"
    assert arguments["portfolio_description"] == "description"
    assert "namespace" not in arguments


def test_private_series_are_deleted_in_batches_of_this_applications_tables(recipes) -> None:
    recipes.answer("delete_private_series", {})
    identifiers = [f"P{index}" for index in range(501)]
    markets_api.delete_private_series(tables=["table"], portfolio_identifiers=identifiers)
    assert [arguments["tables"] for _, arguments in recipes.calls] == [["table"], ["table"]]
    assert [len(arguments["portfolios"]) for _, arguments in recipes.calls] == [500, 1]


def test_an_account_delete_removes_only_the_private_rebalance_state(recipes) -> None:
    from alpaca_connectors.account.deletion import delete_portfolio_series
    from alpaca_connectors.instances import AlpacaPortfolioRebalanceStateStorage

    recipes.answer("delete_private_series", {})
    delete_portfolio_series(["ALPACA_PORTFOLIO__A"])
    # Markets deletes the portfolio's weights and values with the Portfolio (ADR 0031).
    assert recipes.calls == [
        (
            "delete_private_series",
            {
                "tables": [AlpacaPortfolioRebalanceStateStorage],
                "portfolios": ["ALPACA_PORTFOLIO__A"],
            },
        )
    ]


def test_portfolio_values_name_the_return_as_this_application_does(recipes) -> None:
    values = pd.DataFrame(
        {
            "time_index": [AT],
            "portfolio_identifier": ["ALPACA_PORTFOLIO__A"],
            "close": [1.0],
            "return": [0.1],
            "calculated_close": [float("nan")],
            "close_time": [pd.NaT],
        }
    ).set_index(["time_index", "portfolio_identifier"])
    recipes.answer(
        "portfolio_history",
        PortfolioHistory(
            portfolio_identifier="ALPACA_PORTFOLIO__A", count=9, values=values, next_offset=1
        ),
    )
    page = markets_api.portfolio_values("ALPACA_PORTFOLIO__A", limit=5)
    assert page.total_count == 9
    # A missing value is None, never NaN, which the performance figures would refuse.
    assert page.rows == [
        {
            "time_index": AT,
            "close": 1.0,
            "period_return": 0.1,
            "calculated_close": None,
            "close_time": None,
        }
    ]
    assert recipes.calls == [
        ("portfolio_history", {"portfolio_identifier": "ALPACA_PORTFOLIO__A", "limit": 5})
    ]


# ------------------------------------------------------------------------------------------------
# Nothing else reaches the published series
# ------------------------------------------------------------------------------------------------
_RETIRED_INSTANCES = {
    "AlpacaSignalWeightsStorage",
    "AlpacaPortfolioWeightsStorage",
    "AlpacaPortfoliosStorage",
}


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


def test_the_three_series_instances_are_gone() -> None:
    """Revision 0033 dropped them; only the revisions name their tables (ADR 0031)."""
    from alpaca_connectors.migrations import all_project_metatable_models

    violations = [
        f"{relative}:{line} {name}"
        for relative, tree in _python_files()
        for line, name in _names(tree)
        if name in _RETIRED_INSTANCES
    ]
    assert violations == [], violations
    names = {model.__table__.name for model in all_project_metatable_models()}
    assert (
        not {
            "alpaca_connectors__signalweightsts__main",
            "alpaca_connectors__portfolioweightsts__main",
            "alpaca_connectors__portfoliosts__main",
        }
        & names
    )


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
    """Decision 3: no catalog registration, no second namespace, no access or move tooling."""
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
        "SeriesHistoryPublish",
        "TableSeriesMove",
        "PortfolioSeriesMove",
        "SignalSeriesMove",
    }
    violations = [
        f"{relative}:{line} {name}"
        for relative, tree in _python_files()
        for line, name in _names(tree)
        if name in forbidden
    ]
    assert violations == [], violations
    for removed in (
        "operations/markets_access.py",
        "portfolios/datasets.py",
        "portfolios/signal_history.py",
        "operations/instance_backfill.py",
        "cli/instances.py",
    ):
        assert not (SRC / removed).exists(), removed
