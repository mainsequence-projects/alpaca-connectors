"""A signal's published observations, read from Markets (ADR 0031, decision 6).

Signal weights are Markets' own rows; this application reads them through the ms-markets recipe
``signal_history`` (``markets_api.signal_bounds`` and ``markets_api.latest_signal_observations``)
and keeps only what is its own: the limits of its routes, and the join of each asset to its Alpaca
symbol and name, read from this application's Alpaca details.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass

from alpaca_connectors.markets_api import SignalObservationBounds

MAX_SIGNAL_OBSERVATIONS = 100


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


def _normalize_signal_uid(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("signal_uid must not be empty.")
    return normalized


def _normalize_observation_limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("observation_limit must be an integer.")
    if value < 1 or value > MAX_SIGNAL_OBSERVATIONS:
        raise ValueError(f"observation_limit must be between 1 and {MAX_SIGNAL_OBSERVATIONS}.")
    return value


def read_signal_observation_bounds_by_signal(
    signal_uids: Sequence[str],
) -> dict[str, SignalObservationBounds]:
    """The first and last published observation times of several signals, in one Markets call
    per ``MARKETS_BATCH_SIZE`` signals (issue #46). A signal with no observation has no bounds."""
    from alpaca_connectors.markets_api import signal_bounds

    return signal_bounds(_normalize_signal_uid(uid) for uid in signal_uids)


def read_signal_observation_bounds(*, signal_uid: str) -> SignalObservationBounds:
    """The first and last published observation times of one signal."""
    normalized = _normalize_signal_uid(signal_uid)
    return read_signal_observation_bounds_by_signal([normalized])[normalized]


def read_signal_observation_matrix(
    *,
    signal_uid: str,
    observation_limit: int = MAX_SIGNAL_OBSERVATIONS,
) -> SignalObservationMatrix:
    """Every asset of the latest ``observation_limit`` distinct observations of one signal.

    Markets answers the observations; each asset is joined here to its Alpaca symbol and name
    with one read of this application's details, and the assets are ordered by symbol (the
    identifier when it has none).
    """
    from alpaca_connectors.assets.resolution import alpaca_details_by_identifier
    from alpaca_connectors.markets_api import latest_signal_observations

    normalized_signal_uid = _normalize_signal_uid(signal_uid)
    normalized_limit = _normalize_observation_limit(observation_limit)
    latest = latest_signal_observations(normalized_signal_uid, limit=normalized_limit)
    if not latest.time_indexes:
        return SignalObservationMatrix(signal_uid=normalized_signal_uid, time_indexes=(), assets=())
    details = alpaca_details_by_identifier(sorted(latest.weights)) if latest.weights else {}

    def text(identifier: str, name: str) -> str | None:
        return str((details.get(identifier) or {}).get(name) or "").strip() or None

    ordered = sorted(
        latest.weights,
        key=lambda identifier: ((text(identifier, "symbol") or identifier).casefold(), identifier),
    )
    return SignalObservationMatrix(
        signal_uid=normalized_signal_uid,
        time_indexes=latest.time_indexes,
        assets=tuple(
            SignalObservationAsset(
                asset_identifier=identifier,
                symbol=text(identifier, "symbol"),
                name=text(identifier, "name"),
                weights=tuple(latest.weights[identifier]),
            )
            for identifier in ordered
        ),
    )


__all__ = [
    "MAX_SIGNAL_OBSERVATIONS",
    "SignalObservationAsset",
    "SignalObservationBounds",
    "SignalObservationMatrix",
    "read_signal_observation_bounds",
    "read_signal_observation_bounds_by_signal",
    "read_signal_observation_matrix",
]
