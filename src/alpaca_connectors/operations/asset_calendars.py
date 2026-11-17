"""Link this project's Alpaca assets that have no trading calendar to their class's one (ADR 0026).

ms-markets 4.0.0 computes an asset's price bars from the stored calendar it is linked to and
refuses a price update for an asset with no link; its revision 0028 creates the links empty.
Registration links every asset it writes. This admin operation links the Alpaca assets
registered before, once per Environment: of every asset this project's details table describes,
only those Markets lists as having no trading calendar are linked, on the calendar of their
Alpaca class. An asset that has a calendar is not written, and ``relink=False`` keeps one that
was linked between the read and the write.
"""

from __future__ import annotations

from typing import Any

_PAGE_ROWS = 1000
_UNLINKED_PAGE = 500


def _alpaca_identifiers_by_calendar() -> dict[str, list[str]]:
    """Every asset this project's details table describes, by the calendar of its class."""
    from msm.api.base import operation_result_rows
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import select

    from alpaca_connectors.assets.alpaca_asset_details import (
        AlpacaAssetDetailsTable,
        build_alpaca_unique_identifier,
        trading_calendar_from_alpaca_class,
    )
    from alpaca_connectors.runtime import start_markets_engine

    context = start_markets_engine().context
    identifiers: dict[str, set[str]] = {}
    offset = 0
    while True:
        statement = (
            select(AlpacaAssetDetailsTable.alpaca_asset_id, AlpacaAssetDetailsTable.asset_class)
            .order_by(AlpacaAssetDetailsTable.alpaca_asset_id)
            .limit(_PAGE_ROWS)
            .offset(offset)
        )
        rows = operation_result_rows(
            execute_markets_operation(
                compile_markets_statement(statement, context=context, operation="select"),
                context=context,
            )
        )
        for row in rows:
            identifiers.setdefault(
                trading_calendar_from_alpaca_class(row["asset_class"]), set()
            ).add(build_alpaca_unique_identifier(row["alpaca_asset_id"]))
        if len(rows) < _PAGE_ROWS:
            break
        offset += _PAGE_ROWS
    return {calendar: sorted(values) for calendar, values in sorted(identifiers.items())}


def _unlinked_identifiers() -> set[str]:
    """Every Markets asset that has no trading calendar, whatever its asset type."""
    from msm.api.assets import AssetCalendar

    unlinked: set[str] = set()
    offset = 0
    while True:
        page = AssetCalendar.unlinked_assets(limit=_UNLINKED_PAGE, offset=offset)
        unlinked.update(str(asset.unique_identifier) for asset in page)
        if len(page) < _UNLINKED_PAGE:
            return unlinked
        offset += _UNLINKED_PAGE


def link_alpaca_asset_calendars(*, execute: bool = False) -> dict[str, Any]:
    """Report, and with ``execute`` write, the links of this project's assets with no calendar.

    Reads the details table and the assets Markets lists without a trading calendar. The dry run
    writes nothing. ``execute`` stores each calendar that is missing and makes one
    ``AssetCalendar.link_many`` call per calendar with only its unlinked assets.
    """
    from msm.api.assets import AssetCalendar

    identifiers_by_calendar = _alpaca_identifiers_by_calendar()
    unlinked = _unlinked_identifiers()
    targets_by_calendar = {
        calendar: sorted(unlinked.intersection(identifiers))
        for calendar, identifiers in identifiers_by_calendar.items()
    }
    calendars: dict[str, dict[str, Any]] = {
        calendar: {
            "assets": len(identifiers_by_calendar[calendar]),
            "unlinked": len(targets),
        }
        for calendar, targets in targets_by_calendar.items()
    }
    if not execute:
        return {"execute": False, "calendars": calendars, "succeeded": True}

    from alpaca_connectors.assets.calendars import ensure_trading_calendar

    for calendar, targets in targets_by_calendar.items():
        if not targets:
            calendars[calendar]["linked"] = 0
            continue
        ensure_trading_calendar(calendar)
        result = AssetCalendar.link_many(targets, calendar_identifier=calendar, relink=False)
        calendars[calendar].update(
            linked=len(result.linked),
            kept=len(result.kept),
            unknown=sorted(result.unknown),
        )
    return {"execute": True, "calendars": calendars, "succeeded": True}


__all__ = ["link_alpaca_asset_calendars"]
