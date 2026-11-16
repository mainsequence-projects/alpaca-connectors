"""Revision 0026 adds exactly the series-leading indexes ms-markets 3.8.1 gives the weights instances."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from alpaca_connectors.instances import AlpacaPortfolioWeightsStorage, AlpacaSignalWeightsStorage

REVISION = (
    Path(__file__).resolve().parents[1]
    / "src/alpaca_connectors/migrations/versions/alpaca_connectors"
    / "0026_add_weights_series_lookup_indexes.py"
)


def _revision():
    spec = importlib.util.spec_from_file_location("alpaca_revision_0026", REVISION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _lookup_index(model) -> tuple[str, str, list[str]]:
    (index,) = [index for index in model.__table__.indexes if not index.unique]
    return model.__table__.name, index.name, [column.name for column in index.columns]


def test_revision_0026_follows_0025() -> None:
    revision = _revision()

    assert (revision.revision, revision.down_revision) == ("0026", "0025")


def test_revision_0026_creates_the_indexes_the_instances_declare() -> None:
    revision = _revision()

    assert _lookup_index(AlpacaSignalWeightsStorage) == (
        revision.SIGNAL_WEIGHTS_TABLE,
        revision.SIGNAL_SERIES_INDEX,
        ["signal_uid", "time_index"],
    )
    assert _lookup_index(AlpacaPortfolioWeightsStorage) == (
        revision.PORTFOLIO_WEIGHTS_TABLE,
        revision.PORTFOLIO_SERIES_INDEX,
        ["portfolio_identifier", "time_index"],
    )
