"""The sizing rule: an account's exposures turned into target units (ADR 0023).

ms-markets stores an account target as exposure, a share of the account's NAV or an amount per
portfolio, and reads no price (ms-markets ADR 0054). This module is the only place in the
connector that prices a target. It is a pure function of the exposure rows, Alpaca's equity, the
portfolio weights, the latest trades and the ``fractionable`` flags, so it runs without Alpaca or
Markets.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

# Alpaca accepts fractional quantities to nine decimal places.
FRACTIONAL_DECIMALS = 9


class FundAllocationError(ValueError):
    """A sync could not size or allocate the account's target.

    ``code`` is the public error code. The message is the connector's own and names no asset,
    portfolio or amount.
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Exposure:
    """One row of the account target that has to be sized.

    ``weight`` is a share of the account's NAV and ``amount`` a notional in USD; exactly one is
    set. For a portfolio, ``identifier`` is the portfolio's ``unique_identifier``, the key of its
    weights. For an asset it is the asset's ``unique_identifier``.
    """

    target_type: Literal["portfolio", "asset"]
    target_uid: str
    identifier: str
    weight: float | None = None
    amount: float | None = None


@dataclass(frozen=True, slots=True)
class AssetTerms:
    """What the sizing needs to know about one asset, keyed by its ``unique_identifier``."""

    asset_uid: str
    price: float | None
    fractionable: bool


def _finite(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def exposure_size(exposure: Exposure, *, equity: float | None) -> float:
    """The exposure in USD: its amount, or its share of Alpaca's reported equity."""
    if exposure.amount is not None:
        return float(exposure.amount)
    if exposure.weight is None:
        raise FundAllocationError(
            "invalid_account_target",
            "A row of the account target states neither a share of NAV nor an amount.",
        )
    account_equity = _finite(equity)
    if account_equity is None:
        raise FundAllocationError(
            "account_equity_missing",
            "Alpaca reported no equity for the account, so a share of NAV cannot be sized.",
        )
    return float(exposure.weight) * account_equity


def size_units(raw_units: float, *, fractionable: bool) -> float:
    """Signed target units: as computed for a fractionable asset, else rounded half up."""
    magnitude = abs(raw_units)
    if fractionable:
        units = round(magnitude, FRACTIONAL_DECIMALS)
    else:
        units = float(math.floor(magnitude + 0.5))
    return math.copysign(units, raw_units) if units else 0.0


def _position(asset: AssetTerms, raw_units: float) -> dict[str, Any] | None:
    units = size_units(raw_units, fractionable=asset.fractionable)
    if units == 0:
        return None
    return {
        "asset_uid": asset.asset_uid,
        "quantity": abs(units),
        "direction": 1 if units > 0 else -1,
    }


def _terms(assets: Mapping[str, AssetTerms], identifier: str) -> AssetTerms:
    asset = assets.get(identifier)
    if asset is None:
        raise FundAllocationError(
            "asset_unresolved",
            "An asset of the account target is not a registered Alpaca asset.",
        )
    if asset.price is None or asset.price <= 0:
        raise FundAllocationError(
            "asset_price_missing",
            "Alpaca has no latest trade for an asset of the account target.",
        )
    return asset


def size_targets(
    exposures: Sequence[Exposure],
    *,
    equity: float | None,
    weights: Mapping[str, Mapping[str, float]],
    assets: Mapping[str, AssetTerms],
) -> list[dict[str, Any]]:
    """One ``sized_targets`` entry per exposure, in the shape ms-markets takes.

    ``weights`` maps a portfolio's ``unique_identifier`` to its weight per asset identifier, and
    ``assets`` an asset identifier to its terms. Nothing is returned for part of a target: a
    portfolio without weights, an unregistered asset or a missing price raises
    ``FundAllocationError``.
    """
    sized: list[dict[str, Any]] = []
    for exposure in exposures:
        size = exposure_size(exposure, equity=equity)
        if exposure.target_type == "portfolio":
            portfolio_weights = weights.get(exposure.identifier)
            if not portfolio_weights:
                raise FundAllocationError(
                    "portfolio_weights_missing",
                    "A portfolio of the account target has no published weights at the sync.",
                )
            legs = portfolio_weights
        else:
            legs = {exposure.identifier: 1.0}
        positions: list[dict[str, Any]] = []
        for identifier in sorted(legs):
            weight = _finite(legs[identifier])
            if not weight or size == 0:
                continue
            asset = _terms(assets, identifier)
            position = _position(asset, size * weight / float(asset.price))
            if position is not None:
                positions.append(position)
        sized.append(
            {
                "target_type": exposure.target_type,
                "target_uid": exposure.target_uid,
                "positions": positions,
            }
        )
    return sized


__all__ = [
    "FRACTIONAL_DECIMALS",
    "AssetTerms",
    "Exposure",
    "FundAllocationError",
    "exposure_size",
    "size_targets",
    "size_units",
]
