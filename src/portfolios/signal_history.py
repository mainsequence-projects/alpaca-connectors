"""Read transposed signal-weight observations from canonical ms-markets storage."""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, replace
from typing import Any

MAX_SIGNAL_OBSERVATIONS = 100
MAX_SIGNAL_OBSERVATION_ROWS = 100_000
SIGNAL_OBSERVATION_QUERY_TIMEOUT_MS = 30_000


@dataclass(frozen=True, slots=True)
class SignalObservationAsset:
    """One asset row aligned to every timestamp in a signal observation matrix."""

    asset_identifier: str
    symbol: str | None
    name: str | None
    weights: tuple[float | None, ...]


@dataclass(frozen=True, slots=True)
class SignalObservationMatrix:
    """Latest complete signal observations with assets transposed onto rows."""

    signal_uid: str
    time_indexes: tuple[dt.datetime, ...]
    assets: tuple[SignalObservationAsset, ...]


@dataclass(frozen=True, slots=True)
class SignalObservationBounds:
    """Exact stored observation-time range for one canonical signal."""

    signal_uid: str
    first_observation_at: dt.datetime | None
    last_observation_at: dt.datetime | None


def _normalize_signal_uid(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("signal_uid must not be empty.")
    return normalized


def _normalize_observation_limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("observation_limit must be an integer.")
    if value < 1 or value > MAX_SIGNAL_OBSERVATIONS:
        raise ValueError(
            f"observation_limit must be between 1 and {MAX_SIGNAL_OBSERVATIONS}."
        )
    return value


def _normalize_time_index(value: Any) -> dt.datetime:
    if isinstance(value, dt.datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            raise RuntimeError("Signal storage returned an empty time_index.")
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RuntimeError("Signal storage returned a time_index without a timezone.")
    return parsed.astimezone(dt.UTC)


def _normalize_weight(value: Any) -> float | None:
    if value is None:
        return None
    normalized = float(value)
    if not math.isfinite(normalized):
        raise RuntimeError("Signal storage returned a non-finite signal_weight.")
    return normalized


def _matrix_from_rows(
    *,
    signal_uid: str,
    rows: list[dict[str, Any]],
) -> SignalObservationMatrix:
    if not rows:
        return SignalObservationMatrix(signal_uid=signal_uid, time_indexes=(), assets=())

    normalized_rows: list[
        tuple[dt.datetime, str, float | None, str | None, str | None]
    ] = []
    for row in rows:
        asset_identifier = str(row.get("asset_identifier") or "").strip()
        if not asset_identifier:
            raise RuntimeError("Signal storage returned an empty asset_identifier.")
        symbol = str(row.get("symbol") or "").strip() or None
        name = str(row.get("name") or "").strip() or None
        normalized_rows.append(
            (
                _normalize_time_index(row.get("time_index")),
                asset_identifier,
                _normalize_weight(row.get("signal_weight")),
                symbol,
                name,
            )
        )

    time_indexes = tuple(sorted({row[0] for row in normalized_rows}))
    time_index_positions = {value: position for position, value in enumerate(time_indexes)}
    asset_metadata: dict[str, tuple[str | None, str | None]] = {}
    weights_by_asset: dict[str, list[float | None]] = {}
    seen_keys: set[tuple[dt.datetime, str]] = set()

    for time_index, asset_identifier, signal_weight, symbol, name in normalized_rows:
        key = (time_index, asset_identifier)
        if key in seen_keys:
            raise RuntimeError(
                "Signal storage returned duplicate rows for "
                f"{asset_identifier!r} at {time_index.isoformat()}."
            )
        seen_keys.add(key)
        asset_metadata.setdefault(asset_identifier, (symbol, name))
        weights = weights_by_asset.setdefault(
            asset_identifier,
            [0.0] * len(time_indexes),
        )
        weights[time_index_positions[time_index]] = signal_weight

    ordered_identifiers = sorted(
        weights_by_asset,
        key=lambda identifier: (
            (asset_metadata[identifier][0] or identifier).casefold(),
            identifier,
        ),
    )
    assets = tuple(
        SignalObservationAsset(
            asset_identifier=identifier,
            symbol=asset_metadata[identifier][0],
            name=asset_metadata[identifier][1],
            weights=tuple(weights_by_asset[identifier]),
        )
        for identifier in ordered_identifiers
    )
    return SignalObservationMatrix(
        signal_uid=signal_uid,
        time_indexes=time_indexes,
        assets=assets,
    )


def read_signal_observation_matrix(
    *,
    signal_uid: str,
    observation_limit: int = MAX_SIGNAL_OBSERVATIONS,
) -> SignalObservationMatrix:
    """Return all asset rows for the latest distinct observations of one signal.

    A signal observation is one distinct ``time_index``. The limit is applied inside a
    timestamp CTE before constituent rows are joined, so an observation is never truncated
    merely because it contains many assets.
    """
    from msm.api.base import operation_result_rows
    from msm.models import AssetTable
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from msm_portfolios.data_nodes.signals.storage import SignalWeightsStorage
    from sqlalchemy import select

    from src.assets.alpaca_asset_details import AlpacaAssetDetailsTable
    from src.runtime import start_markets_engine

    normalized_signal_uid = _normalize_signal_uid(signal_uid)
    normalized_limit = _normalize_observation_limit(observation_limit)
    runtime = start_markets_engine()
    query_context = replace(
        runtime.context,
        limits={
            "max_rows": MAX_SIGNAL_OBSERVATION_ROWS,
            "statement_timeout_ms": SIGNAL_OBSERVATION_QUERY_TIMEOUT_MS,
        },
    )
    latest_observations = (
        select(SignalWeightsStorage.time_index)
        .where(SignalWeightsStorage.signal_uid == normalized_signal_uid)
        .group_by(SignalWeightsStorage.time_index)
        .order_by(SignalWeightsStorage.time_index.desc())
        .limit(normalized_limit)
        .cte("latest_signal_observations")
    )
    statement = (
        select(
            SignalWeightsStorage.time_index,
            SignalWeightsStorage.asset_identifier,
            SignalWeightsStorage.signal_weight,
            AlpacaAssetDetailsTable.symbol,
            AlpacaAssetDetailsTable.name,
        )
        .select_from(SignalWeightsStorage)
        .join(
            latest_observations,
            latest_observations.c.time_index == SignalWeightsStorage.time_index,
        )
        .join(
            AssetTable,
            AssetTable.unique_identifier == SignalWeightsStorage.asset_identifier,
        )
        .outerjoin(
            AlpacaAssetDetailsTable,
            AlpacaAssetDetailsTable.asset_uid == AssetTable.uid,
        )
        .where(SignalWeightsStorage.signal_uid == normalized_signal_uid)
        .order_by(
            SignalWeightsStorage.time_index.asc(),
            SignalWeightsStorage.asset_identifier.asc(),
        )
    )
    operation = compile_markets_statement(
        statement,
        context=query_context,
        operation="select",
        models=[SignalWeightsStorage, AssetTable, AlpacaAssetDetailsTable],
        access="read",
    )
    result = execute_markets_operation(operation, context=query_context)
    if bool(result.get("truncated")):
        raise RuntimeError(
            "Signal observation history exceeds the bounded API read limit of "
            f"{MAX_SIGNAL_OBSERVATION_ROWS:,} rows."
        )
    return _matrix_from_rows(
        signal_uid=normalized_signal_uid,
        rows=operation_result_rows(result),
    )


def read_signal_observation_bounds(*, signal_uid: str) -> SignalObservationBounds:
    """Return the exact first and last stored observation timestamps for one signal."""
    from msm.api.base import operation_result_rows
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from msm_portfolios.data_nodes.signals.storage import SignalWeightsStorage
    from sqlalchemy import func, select

    from src.runtime import start_markets_engine

    normalized_signal_uid = _normalize_signal_uid(signal_uid)
    runtime = start_markets_engine()
    query_context = replace(
        runtime.context,
        limits={"max_rows": 1, "statement_timeout_ms": SIGNAL_OBSERVATION_QUERY_TIMEOUT_MS},
    )
    statement = select(
        func.min(SignalWeightsStorage.time_index).label("first_observation_at"),
        func.max(SignalWeightsStorage.time_index).label("last_observation_at"),
    ).where(SignalWeightsStorage.signal_uid == normalized_signal_uid)
    operation = compile_markets_statement(
        statement,
        context=query_context,
        operation="select",
        models=[SignalWeightsStorage],
        access="read",
    )
    rows = operation_result_rows(execute_markets_operation(operation, context=query_context))
    row = rows[0] if rows else {}
    first = row.get("first_observation_at")
    last = row.get("last_observation_at")
    return SignalObservationBounds(
        signal_uid=normalized_signal_uid,
        first_observation_at=None if first is None else _normalize_time_index(first),
        last_observation_at=None if last is None else _normalize_time_index(last),
    )


__all__ = [
    "MAX_SIGNAL_OBSERVATIONS",
    "SignalObservationBounds",
    "SignalObservationAsset",
    "SignalObservationMatrix",
    "read_signal_observation_bounds",
    "read_signal_observation_matrix",
]
