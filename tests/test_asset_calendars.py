"""Every Alpaca asset is linked to the trading calendar of its class (ADR 0026)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from alpaca_connectors.assets.alpaca_asset_details import (
    ASSET_TYPE_BY_ALPACA_CLASS,
    TRADING_CALENDAR_BY_ALPACA_CLASS,
    build_alpaca_unique_identifier,
    trading_calendar_from_alpaca_class,
)
from alpaca_connectors.assets.calendars import ensure_trading_calendar
from alpaca_connectors.operations import asset_calendars

AAPL_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
SPY_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
BTC_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
AAPL = build_alpaca_unique_identifier(AAPL_ID)
SPY = build_alpaca_unique_identifier(SPY_ID)
BTC = build_alpaca_unique_identifier(BTC_ID)


def test_every_alpaca_class_the_connector_registers_has_a_trading_calendar() -> None:
    assert set(TRADING_CALENDAR_BY_ALPACA_CLASS) == set(ASSET_TYPE_BY_ALPACA_CLASS)
    assert trading_calendar_from_alpaca_class("US_EQUITY") == "NYSE"
    assert trading_calendar_from_alpaca_class("crypto") == "CRYPTO_24_7"
    with pytest.raises(ValueError, match="Unsupported Alpaca asset_class"):
        trading_calendar_from_alpaca_class("fx")


def _details(*rows: tuple[uuid.UUID, str]) -> Mock:
    return Mock(
        return_value=[{"alpaca_asset_id": asset_id, "asset_class": cls} for asset_id, cls in rows]
    )


def _run(*, execute: bool, unlinked: list[str], link_results: dict[str, dict]):
    asset_calendar = Mock()
    # Markets lists every unlinked asset, of every type, a page at a time.
    asset_calendar.unlinked_assets.side_effect = lambda *, limit, offset: [
        SimpleNamespace(unique_identifier=identifier)
        for identifier in unlinked[offset : offset + limit]
    ]
    asset_calendar.link_many.side_effect = lambda identifiers, **kwargs: SimpleNamespace(
        **{"linked": [], "kept": [], "unknown": [], **link_results[kwargs["calendar_identifier"]]}
    )
    ensure_calendar = Mock()
    with (
        patch(
            "alpaca_connectors.runtime.start_markets_engine",
            return_value=SimpleNamespace(context=object()),
        ),
        patch("msm.repositories.base.compile_markets_statement"),
        patch("msm.repositories.base.execute_markets_operation"),
        patch(
            "msm.api.base.operation_result_rows",
            _details((AAPL_ID, "us_equity"), (SPY_ID, "us_equity"), (BTC_ID, "crypto")),
        ),
        patch("msm.api.assets.AssetCalendar", asset_calendar),
        patch(
            "alpaca_connectors.assets.calendars.ensure_trading_calendars_stored", ensure_calendar
        ),
        patch.object(asset_calendars, "_UNLINKED_PAGE", 2),
    ):
        report = asset_calendars.link_alpaca_asset_calendars(execute=execute)
    return report, asset_calendar, ensure_calendar


def test_the_dry_run_counts_each_calendars_assets_and_the_unlinked_ones_and_writes_nothing() -> (
    None
):
    report, asset_calendar, ensure_calendar = _run(
        execute=False, unlinked=[AAPL, "BOND-NOT-ALPACA"], link_results={}
    )

    assert report == {
        "execute": False,
        "calendars": {
            "CRYPTO_24_7": {"assets": 1, "unlinked": 0},
            "NYSE": {"assets": 2, "unlinked": 1},
        },
        "succeeded": True,
    }
    asset_calendar.link_many.assert_not_called()
    ensure_calendar.assert_not_called()


def test_execute_links_only_the_alpaca_assets_that_have_no_calendar() -> None:
    # SPY has a calendar, so Markets does not list it; the bond is not this project's asset.
    # Three unlinked assets with a page of two: the read follows every page.
    report, asset_calendar, ensure_calendar = _run(
        execute=True,
        unlinked=[AAPL, BTC, "BOND-NOT-ALPACA"],
        link_results={"NYSE": {"linked": [AAPL]}, "CRYPTO_24_7": {"linked": [BTC]}},
    )

    # Both calendars are ensured in one call, before any link.
    (call,) = ensure_calendar.call_args_list
    assert list(call.args[0]) == ["CRYPTO_24_7", "NYSE"]
    assert [(call.args[0], call.kwargs) for call in asset_calendar.link_many.call_args_list] == [
        ([BTC], {"calendar_identifier": "CRYPTO_24_7", "relink": False}),
        ([AAPL], {"calendar_identifier": "NYSE", "relink": False}),
    ]
    assert report["calendars"] == {
        "CRYPTO_24_7": {"assets": 1, "unlinked": 1, "linked": 1, "kept": 0, "unknown": []},
        "NYSE": {"assets": 2, "unlinked": 1, "linked": 1, "kept": 0, "unknown": []},
    }
    assert report["succeeded"] is True


def test_execute_writes_nothing_when_every_asset_has_a_calendar() -> None:
    report, asset_calendar, ensure_calendar = _run(
        execute=True, unlinked=["BOND-NOT-ALPACA"], link_results={}
    )

    asset_calendar.link_many.assert_not_called()
    (call,) = ensure_calendar.call_args_list
    assert list(call.args[0]) == []
    assert report["calendars"] == {
        "CRYPTO_24_7": {"assets": 1, "unlinked": 0, "linked": 0},
        "NYSE": {"assets": 2, "unlinked": 0, "linked": 0},
    }


def _stored_calendars(*stored: str) -> Mock:
    calendar = Mock()
    calendar.filter.side_effect = lambda *, unique_identifier__in, limit: [
        SimpleNamespace(unique_identifier=key) for key in stored if key in unique_identifier__in
    ][:limit]
    calendar.create_from_pandas_calendar.side_effect = lambda source, key, **kwargs: (
        SimpleNamespace(unique_identifier=key, source=source, **kwargs)
    )
    return calendar


def test_a_stored_calendar_is_read_once_and_never_written_again() -> None:
    calendar = _stored_calendars("NYSE")
    with patch("msm.api.calendars.Calendar", calendar):
        row = ensure_trading_calendar("NYSE")

    assert row.unique_identifier == "NYSE"
    calendar.filter.assert_called_once_with(unique_identifier__in=["NYSE"], limit=1)
    calendar.create_from_pandas_calendar.assert_not_called()


def test_a_process_reads_a_calendar_it_has_seen_stored_only_once() -> None:
    from alpaca_connectors.assets.calendars import ensure_trading_calendars_stored

    calendar = _stored_calendars("NYSE", "CRYPTO_24_7")
    with patch("msm.api.calendars.Calendar", calendar):
        ensure_trading_calendars_stored(["NYSE", "CRYPTO_24_7"])
        ensure_trading_calendars_stored(["CRYPTO_24_7", "NYSE"])
        ensure_trading_calendar("NYSE")

    # One read for both calendars; the later calls read nothing.
    calendar.filter.assert_called_once_with(unique_identifier__in=["NYSE", "CRYPTO_24_7"], limit=2)


def test_a_missing_exchange_calendar_is_stored_from_the_library_calendar_of_its_name() -> None:
    from msm.api.calendars import CalendarType

    calendar = _stored_calendars()
    with patch("msm.api.calendars.Calendar", calendar):
        ensure_trading_calendar("NYSE")

    calendar.create_from_pandas_calendar.assert_called_once_with(
        "NYSE",
        "NYSE",
        display_name="NYSE trading sessions",
        calendar_type=CalendarType.TRADING,
    )


@pytest.mark.parametrize("key", ["CRYPTO_24_7", "24/7"])
def test_a_missing_always_open_calendar_is_stored_from_the_library_24_7_calendar(key) -> None:
    from msm.api.calendars import CalendarType

    calendar = _stored_calendars()
    with patch("msm.api.calendars.Calendar", calendar):
        ensure_trading_calendar(key)

    calendar.create_from_pandas_calendar.assert_called_once_with(
        "24/7",
        key,
        display_name=f"{key} always-open sessions",
        calendar_type=CalendarType.TRADING,
    )


def test_every_class_calendar_the_connector_names_is_one_the_library_can_store() -> None:
    import pandas_market_calendars

    for key in set(TRADING_CALENDAR_BY_ALPACA_CLASS.values()):
        source = "24/7" if key == "CRYPTO_24_7" else key
        assert pandas_market_calendars.get_calendar(source).name
