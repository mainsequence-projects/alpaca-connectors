"""Revision 0026 indexed the two weights instances; revision 0033 drops them with their indexes."""

from __future__ import annotations

import importlib.util
from pathlib import Path

REVISIONS = (
    Path(__file__).resolve().parents[1]
    / "src/alpaca_connectors/migrations/versions/alpaca_connectors"
)


def _revision(file_name: str):
    spec = importlib.util.spec_from_file_location(
        f"alpaca_revision_{file_name[:4]}", REVISIONS / file_name
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_0026_follows_0025() -> None:
    revision = _revision("0026_add_weights_series_lookup_indexes.py")

    assert (revision.revision, revision.down_revision) == ("0026", "0025")


def test_revision_0033_drops_the_indexes_revision_0026_created() -> None:
    created = _revision("0026_add_weights_series_lookup_indexes.py")
    dropped = _revision("0033_drop_published_series_instances.py")

    assert (dropped.SIGNAL_WEIGHTS_TABLE, dropped.SIGNAL_SERIES_INDEX) == (
        created.SIGNAL_WEIGHTS_TABLE,
        created.SIGNAL_SERIES_INDEX,
    )
    assert (dropped.PORTFOLIO_WEIGHTS_TABLE, dropped.PORTFOLIO_SERIES_INDEX) == (
        created.PORTFOLIO_WEIGHTS_TABLE,
        created.PORTFOLIO_SERIES_INDEX,
    )
