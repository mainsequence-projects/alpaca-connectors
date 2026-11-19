"""The interpolated Alpaca prices that value every portfolio of this connector."""

from __future__ import annotations

from typing import Any

from alpaca_connectors.market_data import storage_for


def resolve_alpaca_bars_time_index_meta_table_uid(
    *,
    frequency_id: str = "1d",
    feed: str = "sip",
    adjustment: str = "all",
) -> str:
    """Resolve the registered Alpaca bars table UID for one migrated storage profile."""
    from metatables import TimeIndexMetaTable

    storage = storage_for(frequency_id, feed, adjustment)
    # The physical name identifies a bars table; its namespace and identifier change when the
    # project tables move to `alpaca-connectors` (docs/operations/markets-api-boundary.md).
    matches = TimeIndexMetaTable.filter_by_body(
        physical_table_name__in=[storage.__table__.name],
        limit=2,
    )
    exact_matches = [
        table for table in matches if table.physical_table_name == storage.__table__.name
    ]
    if len(exact_matches) != 1:
        raise RuntimeError(
            "Expected exactly one registered Alpaca bars TimeIndexMetaTable for "
            f"{storage.__table__.name}; found {len(exact_matches)}. Run and verify the project "
            "markets migration before building an Alpaca-backed portfolio price source."
        )
    return str(exact_matches[0].uid)


def resolve_alpaca_bars_time_index_meta_table_uids(
    profiles: list[tuple[str, str, str]],
) -> dict[tuple[str, str, str], str | Exception]:
    """The registered bars table UID of each ``(frequency_id, feed, adjustment)``, in one read.

    The batch form of ``resolve_alpaca_bars_time_index_meta_table_uid`` (issue #46): one
    MetaTables request for every profile. Each profile is answered with its UID, or with the
    error the single form raises for it: ``ValueError`` for a profile without storage,
    ``RuntimeError`` when its table is not registered exactly once.
    """
    from metatables import TimeIndexMetaTable

    answers: dict[tuple[str, str, str], str | Exception] = {}
    names: dict[tuple[str, str, str], str] = {}
    for profile in dict.fromkeys(profiles):
        try:
            names[profile] = storage_for(*profile).__table__.name
        except ValueError as exc:
            answers[profile] = exc
    if not names:
        return answers
    physical_names = sorted(set(names.values()))
    matches = TimeIndexMetaTable.filter_by_body(
        physical_table_name__in=physical_names,
        limit=2 * len(physical_names),
    )
    for profile, name in names.items():
        exact = [table for table in matches if table.physical_table_name == name]
        if len(exact) == 1:
            answers[profile] = str(exact[0].uid)
        else:
            answers[profile] = RuntimeError(
                "Expected exactly one registered Alpaca bars TimeIndexMetaTable for "
                f"{name}; found {len(exact)}. Run and verify the project markets migration "
                "before building an Alpaca-backed portfolio price source."
            )
    return answers


def alpaca_interpolated_prices_storage_model(
    *,
    source_time_index_meta_table_uid: str,
    frequency_id: str = "1d",
    feed: str = "sip",
    adjustment: str = "all",
    upsample_frequency_id: str = "1d",
    intraday_bar_interpolation_rule: str = "ffill",
) -> type[Any]:
    """Return the dynamic msm-portfolios storage model for Alpaca-derived prices."""
    from alpaca_connectors.portfolios.interpolated_prices_schema import (
        configured_alpaca_interpolated_prices_storage,
    )

    return configured_alpaca_interpolated_prices_storage(
        source_time_index_meta_table_uid=source_time_index_meta_table_uid,
        source_cadence=storage_for(frequency_id, feed, adjustment).__cadence__,
        upsample_frequency_id=upsample_frequency_id,
        intraday_bar_interpolation_rule=intraday_bar_interpolation_rule,
    )


def build_alpaca_interpolated_prices(
    *,
    source_time_index_meta_table_uid: str,
    asset_identifiers: list[str],
    upsample_frequency_id: str = "1d",
    intraday_bar_interpolation_rule: str = "ffill",
) -> Any:
    """Build InterpolatedPrices sourced from registered Alpaca bars.

    Each asset's bars follow the stored trading calendar it is linked to (ms-markets ADR 0056),
    which registration sets from its Alpaca class (ADR 0026); the scope carries identifiers only,
    and ms-markets refuses the run when an asset in it has no calendar.
    """
    from msm_portfolios.contrib.prices.updaters import (
        InterpolatedPrices,
        InterpolatedPricesConfig,
    )

    from alpaca_connectors.instances import interpolated_prices_storage_kwargs

    asset_scope = sorted(set(asset_identifiers))

    return InterpolatedPrices(
        interpolation_config=InterpolatedPricesConfig(
            source_time_index_meta_table_uid=source_time_index_meta_table_uid,
            asset_list=asset_scope,
            upsample_frequency_id=upsample_frequency_id,
            intraday_bar_interpolation_rule=intraday_bar_interpolation_rule,
        ),
        **interpolated_prices_storage_kwargs(),
    )


__all__ = [
    "alpaca_interpolated_prices_storage_model",
    "build_alpaca_interpolated_prices",
    "resolve_alpaca_bars_time_index_meta_table_uid",
    "resolve_alpaca_bars_time_index_meta_table_uids",
]
