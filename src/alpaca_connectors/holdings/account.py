"""Pure transformation of Alpaca positions into canonical account holdings rows."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

DEFAULT_CASH_ASSET_IDENTIFIER = "USD"


def _enum_str(value: Any) -> str | None:
    if value is None:
        return None
    return value.value if hasattr(value, "value") else str(value)


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def build_account_holdings_rows(
    *,
    positions: Sequence[Any],
    cash: Any,
    resolve_asset: Callable[[Any], str | None],
    currency_identifier: str | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Build account-holdings rows from Alpaca positions and cash.

    The resolver receives the complete Alpaca position so it can use ``asset_id`` as identity.
    Every Alpaca asset class is accepted; provider support is decided by the resolver.
    """
    rows: list[dict[str, Any]] = []
    unresolved: list[str] = []

    for position in positions:
        symbol = getattr(position, "symbol", None)
        quantity = _to_decimal(getattr(position, "qty", None)) or Decimal(0)
        if quantity == 0:
            continue
        asset_class = _enum_str(getattr(position, "asset_class", None))
        identifier = resolve_asset(position)
        if not identifier:
            unresolved.append(str(getattr(position, "asset_id", None) or symbol or "unknown"))
            continue

        side = _enum_str(getattr(position, "side", None))
        direction = -1 if (side == "short" or quantity < 0) else 1
        rows.append(
            {
                "asset_identifier": identifier,
                "quantity": abs(quantity),
                "direction": direction,
                "extra_details": {
                    "symbol": symbol,
                    "asset_class": asset_class,
                    "exchange": _enum_str(getattr(position, "exchange", None)),
                    "avg_entry_price": getattr(position, "avg_entry_price", None),
                    "market_value": getattr(position, "market_value", None),
                    "cost_basis": getattr(position, "cost_basis", None),
                    "unrealized_pl": getattr(position, "unrealized_pl", None),
                    "current_price": getattr(position, "current_price", None),
                    "alpaca_asset_id": str(getattr(position, "asset_id", "")) or None,
                },
            }
        )

    cash_amount = _to_decimal(cash)
    if currency_identifier and cash_amount is not None and cash_amount != 0:
        rows.append(
            {
                "asset_identifier": currency_identifier,
                "quantity": abs(cash_amount),
                "direction": 1 if cash_amount >= 0 else -1,
                "extra_details": {"kind": "cash"},
            }
        )

    return rows, sorted(set(unresolved))


__all__ = [
    "DEFAULT_CASH_ASSET_IDENTIFIER",
    "build_account_holdings_rows",
]
