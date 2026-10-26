"""Moving this application's history into its instances with the ms-markets series moves.

``copy_time_index_rows`` and the ``PortfolioDatasets`` catalog calls are replaced by recording
fakes, so these tests check which keys of which tables each move copies, in which order the
catalog is written or removed, and that an incomplete copy changes nothing in Markets.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from alpaca_connectors.operations import instance_backfill

PORTFOLIO = "ALPACA_ETF_PORTFOLIO__ABC"
PORTFOLIO_UID = "11111111-1111-4111-8111-111111111111"
CURRENT_SIGNAL = "signal-current"
OTHER_SIGNAL = "signal-other"


class Markets:
    """Records the copies and catalog calls of one run."""

    def __init__(
        self,
        *,
        missing: dict[tuple[str, str], int] | None = None,
        signals_without_metadata: tuple[str, ...] = (),
    ) -> None:
        self.missing = missing or {}
        self.signals_without_metadata = set(signals_without_metadata)
        self.calls: list[tuple[str, Any]] = []
        self.reads: list[tuple[int, float]] = []

    def copy(self, source, target, *, key_column, keys, dry_run, keys_per_read, deadline_seconds):
        from msm.storage_copies import KeyCopy, TimeIndexCopyReport

        target_name = target.__table__.name
        self.reads.append((keys_per_read, deadline_seconds))
        self.calls.append(("copy", (source.__table__.name, target_name, key_column, list(keys))))
        return TimeIndexCopyReport(
            source_table=source.__table__.name,
            target_table=target_name,
            key_column=key_column,
            keys=tuple(
                KeyCopy(
                    key=key,
                    source_rows=3,
                    target_rows=3,
                    missing_rows=self.missing.get((target_name, key), 0),
                    copied_rows=0 if dry_run else 3,
                )
                for key in keys
            ),
            dry_run=dry_run,
        )

    def catalog(self, name):
        return classmethod(lambda cls, *args, **kwargs: self.calls.append((name, args or kwargs)))

    def signal_metadata(self, cls=None, *, signal_uid__in, limit):
        """The moves refuse a signal without a SignalMetadata row before any catalog call."""
        return [
            SimpleNamespace(signal_uid=uid)
            for uid in signal_uid__in
            if uid not in self.signals_without_metadata
        ][:limit]


@pytest.fixture
def markets(monkeypatch):
    def install(**kwargs) -> Markets:
        from msm.api.portfolios import Portfolio
        from msm_portfolios.api.datasets import PortfolioDatasets

        fake = Markets(**kwargs)
        monkeypatch.setattr("msm.storage_copies.copy_time_index_rows", fake.copy)
        monkeypatch.setattr("msm_portfolios.services.series_moves.copy_time_index_rows", fake.copy)
        monkeypatch.setattr(
            "msm_portfolios.services.series_moves.registered_time_index_meta_table",
            lambda storage: SimpleNamespace(uid=f"uid:{storage.__table__.name}"),
        )
        monkeypatch.setattr(
            Portfolio,
            "get_by_uid",
            classmethod(lambda cls, uid: SimpleNamespace(signal_uid=CURRENT_SIGNAL)),
        )
        for name in ("register", "register_signal", "unregister", "unregister_signal"):
            monkeypatch.setattr(PortfolioDatasets, name, fake.catalog(name))
        from msm_portfolios.api.market_metadata import SignalMetadata

        monkeypatch.setattr(
            SignalMetadata, "filter", classmethod(lambda cls, **kw: fake.signal_metadata(**kw))
        )
        monkeypatch.setattr(
            instance_backfill,
            "_scope",
            lambda: {
                "portfolios": [
                    SimpleNamespace(
                        uid=PORTFOLIO_UID, unique_identifier=PORTFOLIO, signal_uid=CURRENT_SIGNAL
                    )
                ],
                "signal_uids": [OTHER_SIGNAL],
                "calendar_identifiers": ["XNYS"],
                "account_identifiers": ["PA1__ALPACA_PAPER"],
            },
        )
        monkeypatch.setattr(instance_backfill, "_interpolated_moves", lambda **reads: [])
        return fake

    return install


def _copies(fake: Markets) -> list[tuple[str, str, str, list[str]]]:
    return [call for kind, call in fake.calls if kind == "copy"]


def _catalog(fake: Markets) -> list[str]:
    return [kind for kind, _ in fake.calls if kind != "copy"]


def test_dry_run_counts_every_move_and_changes_nothing_in_markets(markets) -> None:
    fake = markets()

    report = instance_backfill.backfill_instances(register_catalog=True)

    assert report["dry_run"] is True
    assert [result["status"] for result in report["results"]] == ["planned"] * 5
    assert _catalog(fake) == []


def test_move_copies_signals_first_then_each_portfolio_then_registers_last(markets) -> None:
    fake = markets()

    report = instance_backfill.backfill_instances(execute=True, register_catalog=True)

    assert report["succeeded"] is True
    # Signal and table moves (move order 0) run before portfolio moves (move order 1).
    assert _copies(fake) == [
        (
            "ms_markets__signalweightsts",
            "alpaca_connectors__signalweightsts__main",
            "signal_uid",
            [OTHER_SIGNAL],
        ),
        (
            "ms_markets__portfoliocalendareventsts",
            "alpaca_connectors__portfoliocalendareventsts__main",
            "calendar_identifier",
            ["XNYS"],
        ),
        (
            "ms_markets__ordersts",
            "alpaca_connectors__ordersts__main",
            "account_identifier",
            ["PA1__ALPACA_PAPER"],
        ),
        (
            "ms_markets__tradests",
            "alpaca_connectors__tradests__main",
            "account_identifier",
            ["PA1__ALPACA_PAPER"],
        ),
        # The portfolio: its current signal, rebalance state, weights, then values.
        (
            "ms_markets__signalweightsts",
            "alpaca_connectors__signalweightsts__main",
            "signal_uid",
            [CURRENT_SIGNAL],
        ),
        (
            "ms_markets__portfoliorebalancestatets",
            "alpaca_connectors__portfoliorebalancestatets__main",
            "portfolio_identifier",
            [PORTFOLIO],
        ),
        (
            "ms_markets__portfolioweightsts",
            "alpaca_connectors__portfolioweightsts__main",
            "portfolio_identifier",
            [PORTFOLIO],
        ),
        (
            "ms_markets__portfoliosts",
            "alpaca_connectors__portfoliosts__main",
            "portfolio_identifier",
            [PORTFOLIO],
        ),
    ]
    register_signal, register = [call for kind, call in fake.calls if kind != "copy"]
    assert register_signal == (
        {
            "signal_uid": OTHER_SIGNAL,
            "signal_meta_table_uid": "uid:alpaca_connectors__signalweightsts__main",
        },
    )
    assert register == (
        {
            "portfolio_uid": PORTFOLIO_UID,
            "values_meta_table_uid": "uid:alpaca_connectors__portfoliosts__main",
            "weights_meta_table_uid": "uid:alpaca_connectors__portfolioweightsts__main",
            "signal_meta_table_uid": "uid:alpaca_connectors__signalweightsts__main",
        },
    )


def test_without_register_catalog_the_move_only_copies(markets) -> None:
    fake = markets()

    report = instance_backfill.backfill_instances(execute=True)

    assert report["succeeded"] is True
    assert _catalog(fake) == []


def test_an_incomplete_portfolio_copy_leaves_the_catalog_untouched(markets) -> None:
    fake = markets(missing={("alpaca_connectors__portfoliosts__main", PORTFOLIO): 2})

    report = instance_backfill.backfill_instances(execute=True, register_catalog=True)

    statuses = {result["label"]: result["status"] for result in report["results"]}
    assert statuses[PORTFOLIO] == "incomplete"
    assert report["succeeded"] is False
    # The separately moved signal is still bound; the portfolio's catalog row is not written.
    assert _catalog(fake) == ["register_signal"]


def test_rollback_copies_the_instances_back_and_unregisters_portfolios_first(markets) -> None:
    fake = markets()

    report = instance_backfill.rollback_instances(execute=True)

    assert report["rollback"] is True and report["succeeded"] is True
    copies = _copies(fake)
    assert copies[0] == (
        "alpaca_connectors__signalweightsts__main",
        "ms_markets__signalweightsts",
        "signal_uid",
        [CURRENT_SIGNAL],
    )
    assert all(target.startswith("ms_markets__") for _, target, _, _ in copies)
    # The portfolio's catalog row goes without touching signal bindings; each move then unbinds
    # only the signals it moved: the portfolio its current signal, the signal move the other.
    catalog = [(kind, call) for kind, call in fake.calls if kind != "copy"]
    assert catalog == [
        ("unregister", {"portfolio_uid": PORTFOLIO_UID, "unbind_signal": False}),
        ("unregister_signal", {"signal_uid": CURRENT_SIGNAL}),
        ("unregister_signal", {"signal_uid": OTHER_SIGNAL}),
    ]


def test_a_signal_without_metadata_refuses_the_move_before_any_catalog_call(markets) -> None:
    fake = markets(signals_without_metadata=(OTHER_SIGNAL,))

    report = instance_backfill.backfill_instances(execute=True, register_catalog=True)

    statuses = {result["kind"]: result["status"] for result in report["results"]}
    assert statuses["signal"] == "failed"
    assert "SignalMetadata" in next(
        result["detail"] for result in report["results"] if result["kind"] == "signal"
    )
    # The portfolio, whose current signal has metadata, still registers.
    assert _catalog(fake) == ["register"]


def test_batch_size_sets_the_keys_read_per_request(markets) -> None:
    fake = markets()

    instance_backfill.backfill_instances(batch_size=7)

    assert {keys_per_read for keys_per_read, _ in fake.reads} == {7}


def test_msm_series_callable_returns_the_registering_moves(markets) -> None:
    markets()

    moves = instance_backfill.series_moves()
    # `msm series rollback` passes rollback=; the moves are the same in both directions.
    assert [type(move) for move in instance_backfill.series_moves(rollback=True)] == [
        type(move) for move in moves
    ]

    assert [type(move).__name__ for move in moves] == [
        "SignalSeriesMove",
        "PortfolioSeriesMove",
        "TableSeriesMove",
        "TableSeriesMove",
        "TableSeriesMove",
    ]
    assert all(getattr(move, "register", True) for move in moves)


def test_interpolated_prices_move_by_asset_from_the_0011_tables(monkeypatch) -> None:
    from alpaca_connectors.portfolios import interpolated_prices_schema

    spec = interpolated_prices_schema.InterpolatedPricesStorageSpec(
        frequency_id="1d",
        feed="sip",
        adjustment="all",
        source_time_index_meta_table_uid="11111111-1111-4111-8111-111111111111",
        source_cadence="1d",
        output_table_name="unused",
        output_identifier="unused",
    )
    monkeypatch.setattr(
        interpolated_prices_schema, "resolve_interpolated_prices_storage_specs", lambda: [spec]
    )
    statements: list[Any] = []
    monkeypatch.setattr(
        instance_backfill,
        "_own_rows",
        lambda statement: (
            statements.append(statement)
            or [{"asset_identifier": "ALPACA::B"}, {"asset_identifier": "ALPACA::A"}]
        ),
    )

    (move,) = instance_backfill._interpolated_moves()

    assert move.core.__table__.name.startswith("ms_markets__interpolated_prices__t_")
    assert move.instance.__table__.name == move.core.__table__.name.replace(
        "ms_markets__", "alpaca_connectors__"
    )
    assert (move.key_column, list(move.keys)) == ("asset_identifier", ["ALPACA::A", "ALPACA::B"])
    # The keys come from this application's own bars table.
    assert "alpaca_connectors__bars_1d_sip_all" in str(statements[0])
