"""The dispatcher's tick runs every scheduled portfolio in one run (ADR 0013, as amended).

``markets_api.run_portfolios`` makes one ms-markets ``run_portfolios`` call, whose graph runs a
node several portfolios share once; ``execute_portfolio_configurations`` updates each bars
configuration once and answers every configuration with its result or its error.
"""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from msm_portfolios.recipes import PortfolioRun, PortfolioRunFailure, PortfolioRuns
from msm_portfolios.services.series import SeriesPublication

from alpaca_connectors import markets_api
from alpaca_connectors.portfolios import execution
from alpaca_connectors.portfolios.execution import ResolvedPortfolioConfiguration

START = dt.datetime(2026, 11, 20, 21, tzinfo=dt.UTC)


def _entry(name: str) -> markets_api.PortfolioRunEntry:
    return markets_api.PortfolioRunEntry(
        portfolio_configuration=SimpleNamespace(name=name),
        unique_identifier=f"ALPACA_PORTFOLIO__{name}",
        calendar_uid="calendar-uid",
    )


def _ran(identifier: str, written: int = 2) -> PortfolioRun:
    return PortfolioRun(
        portfolio=SimpleNamespace(uid=f"uid-{identifier}", unique_identifier=identifier),
        weights=SeriesPublication(key=identifier, written=written, skipped=0),
        values=None,
        first_rebalance=True,
    )


def test_one_call_runs_them_and_each_is_answered_in_order() -> None:
    entries = [_entry("A"), _entry("B"), _entry("C")]
    # A and C hash the same, so they ask the resolver the same question: C goes in a second call.
    hashes = {"A": "h1", "B": "h2", "C": "h1"}
    calls = []
    upserts = []

    def recipe(configurations, *, portfolio_resolver, **tables):
        names = [configuration.name for configuration in configurations]
        calls.append((names, tables))
        rows = {
            name: portfolio_resolver.get_or_create_from_configuration_hash(
                portfolio_configuration_hash=hashes[name], portfolio_configuration=None
            )
            for name in names
        }
        failed = tuple(
            PortfolioRunFailure(
                position=position,
                portfolio_identifier=str(rows[name].unique_identifier),
                detail="refused",
                error=ValueError("The portfolio cannot run."),
            )
            for position, name in enumerate(names)
            if name == "B"
        )
        ran = tuple(_ran(str(rows[name].unique_identifier)) for name in names if name != "B")
        return PortfolioRuns(ran=ran, failed=failed)

    with (
        patch("msm_portfolios.recipes.run_portfolios", recipe),
        patch(
            "msm_portfolios.time_index.portfolio_identity.compute_portfolio_configuration_hash",
            lambda configuration: hashes[configuration.name],
        ),
        patch(
            "msm.api.portfolios.Portfolio.upsert",
            lambda **values: upserts.append(values) or SimpleNamespace(**values),
        ),
    ):
        answers = markets_api.run_portfolios(
            entries, rebalance_output_table="rebalance", event_ledger_output_table="ledger"
        )

    assert [names for names, _ in calls] == [["A", "B"], ["C"]]
    assert all(
        tables == {"rebalance_output_table": "rebalance", "event_ledger_output_table": "ledger"}
        for _, tables in calls
    )
    # Each hash names its own Portfolio row.
    assert [row["unique_identifier"] for row in upserts] == [
        "ALPACA_PORTFOLIO__A",
        "ALPACA_PORTFOLIO__B",
        "ALPACA_PORTFOLIO__C",
    ]
    first, refused, third = answers
    assert first == markets_api.PortfolioRunAnswer(
        portfolio_uid="uid-ALPACA_PORTFOLIO__A",
        portfolio_unique_identifier="ALPACA_PORTFOLIO__A",
        written={"weights": 2, "values": 0},
        first_rebalance=True,
    )
    assert isinstance(refused, ValueError)
    assert third.portfolio_unique_identifier == "ALPACA_PORTFOLIO__C"


def _resolved(name: str, bars_uid: str, assets: tuple[str, ...]) -> ResolvedPortfolioConfiguration:
    return ResolvedPortfolioConfiguration(
        configuration=SimpleNamespace(uid=uuid.UUID(name * 32), portfolio_uid=None),
        bars_configuration=SimpleNamespace(uid=bars_uid),
        rebalance_configuration=SimpleNamespace(uid="rebalance", strategy="immediate_signal"),
        signal_uid=f"signal-{name}",
        signal_start_time=START,
        asset_identifiers=assets,
        source_time_index_meta_table_uid="bars-table",
    )


def test_a_tick_shares_its_price_chains_and_isolates_each_failure() -> None:
    x, y, z = (str(uuid.UUID(name * 32)) for name in "abc")
    resolved = {
        x: _resolved("a", "bars-1", ("ALPACA::a",)),
        y: _resolved("b", "bars-1", ("ALPACA::b",)),
        z: LookupError("Bars configuration missing."),
    }
    chains = []
    entries = []
    recorded_rows = []

    def run_portfolios(given, **tables):
        entries.extend(given)
        return [
            markets_api.PortfolioRunAnswer(
                portfolio_uid="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
                portfolio_unique_identifier=given[0].unique_identifier,
                written={"weights": 1, "values": 3},
                first_rebalance=True,
            ),
            RuntimeError("The ledger failed."),
        ]

    with (
        patch("alpaca_connectors.runtime.start_portfolio_job_engine"),
        patch.object(execution, "resolve_portfolio_configurations", return_value=resolved),
        patch.object(
            execution,
            "build_portfolio_run",
            lambda value, *, price_chains: (
                chains.append(price_chains)
                or SimpleNamespace(
                    portfolio_configuration=value.signal_uid,
                    unique_identifier=f"ALPACA_PORTFOLIO__{value.signal_uid}",
                    calendar_uid="calendar-uid",
                )
            ),
        ),
        patch.object(markets_api, "run_portfolios", run_portfolios),
        patch.object(
            execution,
            "first_rebalance_state",
            return_value={"rebalanced": True, "first_rebalance_at": START.isoformat()},
        ),
        patch.object(
            markets_api,
            "portfolio_weights_recorded",
            lambda identifier: recorded_rows.append(identifier) or True,
        ),
        patch.object(execution, "update_portfolio_configuration_row") as record_portfolio,
    ):
        result = execution.execute_portfolio_configurations([x, y, z])

    # No bars update before the run: every build shares one set of price chains (ADR 0033).
    assert len(chains) == 2 and chains[0] is chains[1]
    # One run for both portfolios that resolved; Markets is asked only about the one that ran.
    assert [entry.unique_identifier for entry in entries] == [
        "ALPACA_PORTFOLIO__signal-a",
        "ALPACA_PORTFOLIO__signal-b",
    ]
    assert recorded_rows == ["ALPACA_PORTFOLIO__signal-a"]
    record_portfolio.assert_called_once()
    summary = result.summary()
    assert summary["configuration_uids"] == [x, y, z]
    assert [item["configuration_uid"] for item in summary["ran"]] == [x]
    assert summary["ran"][0]["portfolio_weights"] == {"error": False, "rows": 1}
    assert [item["configuration_uid"] for item in summary["failed"]] == [y, z]


def test_a_tick_resolves_its_configurations_in_one_read_per_kind() -> None:
    from alpaca_connectors.portfolios.execution import _PortfolioInputs

    x, y, missing = (str(uuid.UUID(name * 32)) for name in "abc")
    rows = {uid: SimpleNamespace(uid=uuid.UUID(uid)) for uid in (x, y)}
    inputs = {
        uid: _PortfolioInputs(
            configuration=rows[uid],
            rebalance_configuration=SimpleNamespace(),
            bars_configuration=SimpleNamespace(),
            signal_uid="signal-shared",
            signal_start_time=START,
            source_time_index_meta_table_uid="bars-table",
            source_table=SimpleNamespace(uid="bars-table"),
        )
        for uid in (x, y)
    }
    matrix = SimpleNamespace(
        time_indexes=[START], assets=[SimpleNamespace(asset_identifier="ALPACA::a")]
    )
    read_inputs, read_matrices, read_assets = [], [], []

    with (
        patch(
            "alpaca_connectors.portfolios.configurations.portfolio_configurations_by_uids",
            lambda uids: rows,
        ),
        patch.object(
            execution, "_resolve_inputs", lambda found: read_inputs.append(found) or inputs
        ),
        patch.object(
            execution,
            "read_signal_observation_matrix",
            lambda **kwargs: read_matrices.append(kwargs) or matrix,
        ),
        patch(
            "alpaca_connectors.portfolios.signal_assets.assets_by_signal",
            lambda signals: read_assets.append(signals) or {"signal-shared": ["ALPACA::b"]},
        ),
    ):
        answers = execution.resolve_portfolio_configurations([x, y, missing])

    assert len(read_inputs) == 1 and len(read_assets) == 1
    # Two portfolios on one signal read its latest observation once.
    assert read_matrices == [{"signal_uid": "signal-shared", "observation_limit": 1}]
    assert answers[x].asset_identifiers == ("ALPACA::a", "ALPACA::b")
    assert answers[y].source_table.uid == "bars-table"
    assert isinstance(answers[missing], LookupError)
