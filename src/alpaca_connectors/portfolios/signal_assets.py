"""Every asset each signal has ever published (ADR 0024, decision 5).

`AlpacaSignalWeightsStorage` grows with every observation, and its only index leads with
``time_index``, so "every asset this signal ever published" read from it walks the series. This
table keeps the answer as one row per ``(signal_uid, asset_identifier)``, so a `portfolios` bars
configuration resolves its assets by primary key, whatever the number of observations.

The provided-weights service writes it before ``ApplicationWeights.publish``. Writing first keeps
it a superset of what is published: a publication that then fails leaves at worst an asset fetched
that no portfolio holds yet, never a held asset without bars.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence
from typing import Any

from msm.base import MarketsBase, markets_table_args
from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from alpaca_connectors.metatables import AlpacaMarketsMetaTableMixin, ProjectStorageNameMixin

UTC = dt.timezone.utc


def utc_now() -> dt.datetime:
    return dt.datetime.now(UTC).replace(microsecond=0)


class SignalAssetTable(ProjectStorageNameMixin, AlpacaMarketsMetaTableMixin, MarketsBase):
    """One row per signal and asset the signal has ever published."""

    __project_storage_concept__ = "signal_asset"
    __markets_base_identifier__ = "SignalAsset"
    __metatable_description__ = (
        "One row per weight signal and asset it has ever published. Which US stocks and "
        "exchange-traded funds each application-provided weights signal of the Alpaca "
        "Connectors application has given a weight, zero included, so price updates can find "
        "the assets a portfolio needs without reading the signal's whole history; written by "
        "the application each time a signal publishes."
    )
    __metatable_labels__ = ["signals", "portfolios", "configuration", "alpaca"]
    __table_args__ = markets_table_args("SignalAsset")

    signal_uid: Mapped[str] = mapped_column(
        String(255),
        primary_key=True,
        nullable=False,
        info={
            "label": "Signal UID",
            "description": "UID of the weight signal that published the asset.",
        },
    )
    asset_identifier: Mapped[str] = mapped_column(
        String(255),
        primary_key=True,
        nullable=False,
        info={
            "label": "Asset identifier",
            "description": "Main Sequence asset unique identifier the signal gave a weight.",
        },
    )
    first_published_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        info={
            "label": "First published at",
            "description": "Time, in UTC, the asset was first recorded for the signal.",
        },
    )


def _start_runtime() -> Any:
    from msm.bootstrap import resolve_runtime

    from alpaca_connectors.runtime import application_runtime_models, start_markets_engine

    start_markets_engine(models=application_runtime_models())
    return resolve_runtime(models=[SignalAssetTable], row_model_name="SignalAsset")


def _normalized(values: Iterable[Any]) -> list[str]:
    return sorted({str(value).strip() for value in values if str(value or "").strip()})


def record_signal_assets(
    signal_uid: str,
    asset_identifiers: Iterable[str],
    *,
    at: dt.datetime | None = None,
) -> list[str]:
    """Record the assets a signal is about to publish; return the ones it had not published.

    One primary-key read of the given pairs, then one insert of the missing ones. A recorded
    pair keeps its ``first_published_at``.
    """
    from msm.api.base import operation_result_rows
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from msm.repositories.crud import bulk_upsert_model
    from sqlalchemy import select

    identifiers = _normalized(asset_identifiers)
    normalized_signal = str(signal_uid).strip()
    if not normalized_signal:
        raise ValueError("signal_uid must not be empty.")
    if not identifiers:
        return []
    runtime = _start_runtime()
    table = SignalAssetTable
    existing_operation = compile_markets_statement(
        select(table.asset_identifier).where(
            table.signal_uid == normalized_signal,
            table.asset_identifier.in_(identifiers),
        ),
        context=runtime.context,
        operation="select",
    )
    existing = {
        str(row["asset_identifier"])
        for row in operation_result_rows(
            execute_markets_operation(existing_operation, context=runtime.context)
        )
    }
    missing = [identifier for identifier in identifiers if identifier not in existing]
    if missing:
        recorded_at = at or utc_now()
        bulk_upsert_model(
            runtime.context,
            model=table,
            values=[
                {
                    "signal_uid": normalized_signal,
                    "asset_identifier": identifier,
                    "first_published_at": recorded_at,
                }
                for identifier in missing
            ],
            conflict_columns=("signal_uid", "asset_identifier"),
        )
    return missing


def assets_for_signals(signal_uids: Sequence[str]) -> list[str]:
    """Every asset any of these signals has published, in one primary-key read."""
    from msm.api.base import operation_result_rows
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import select

    normalized = _normalized(signal_uids)
    if not normalized:
        return []
    runtime = _start_runtime()
    table = SignalAssetTable
    operation = compile_markets_statement(
        select(table.asset_identifier)
        .where(table.signal_uid.in_(normalized))
        .distinct()
        .order_by(table.asset_identifier.asc()),
        context=runtime.context,
        operation="select",
    )
    return [
        str(row["asset_identifier"])
        for row in operation_result_rows(
            execute_markets_operation(operation, context=runtime.context)
        )
    ]


def forget_signal_assets(signal_uid: str) -> None:
    """Delete one signal's rows, once nothing names its portfolio any more (issue #33)."""
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import delete

    normalized = str(signal_uid).strip()
    if not normalized:
        raise ValueError("signal_uid must not be empty.")
    runtime = _start_runtime()
    operation = compile_markets_statement(
        delete(SignalAssetTable).where(SignalAssetTable.signal_uid == normalized),
        context=runtime.context,
        operation="delete",
    )
    execute_markets_operation(operation, context=runtime.context)


def project_signal_asset_models() -> list[type[MarketsBase]]:
    return [SignalAssetTable]


__all__ = [
    "SignalAssetTable",
    "assets_for_signals",
    "forget_signal_assets",
    "project_signal_asset_models",
    "record_signal_assets",
]
