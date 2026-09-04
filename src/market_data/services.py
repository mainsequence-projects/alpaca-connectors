"""Dataset catalog, constrained observation reads, and account-backed price updates."""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import asdict, dataclass
from typing import Any

from msm.api.base import operation_result_rows

from src.account.credentials import (
    AlpacaSecretNames,
    build_alpaca_historical_data_client,
    build_alpaca_trading_client,
    resolve_alpaca_credentials,
)
from src.market_data.storage import ALPACA_STOCK_BARS_STORAGE_BY_TRIPLE


@dataclass(frozen=True, slots=True)
class MarketDataDataset:
    uid: str
    key: str
    identifier: str
    physical_table: str
    frequency_id: str
    feed: str
    adjustment: str
    cadence: str
    columns: list[str]
    row_count: int
    earliest_observation: dt.datetime | None
    latest_observation: dt.datetime | None


def _dataset_coverage(storage: type) -> tuple[int, dt.datetime | None, dt.datetime | None]:
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import func, select

    from src.runtime import start_markets_engine

    runtime = start_markets_engine()
    statement = select(
        func.count().label("row_count"),
        func.min(storage.time_index).label("earliest_observation"),
        func.max(storage.time_index).label("latest_observation"),
    )
    operation = compile_markets_statement(
        statement,
        context=runtime.context,
        operation="select",
        models=[storage],
        access="read",
    )
    rows = operation_result_rows(execute_markets_operation(operation, context=runtime.context))
    row = rows[0] if rows else {}
    return (
        int(row.get("row_count", 0)),
        row.get("earliest_observation"),
        row.get("latest_observation"),
    )


def _dataset_from_storage(triple: tuple[str, str, str], storage: type) -> MarketDataDataset:
    meta_table = storage.get_time_index_meta_table()
    frequency_id, feed, adjustment = triple
    row_count, earliest_observation, latest_observation = _dataset_coverage(storage)
    return MarketDataDataset(
        uid=str(meta_table.uid),
        key="/".join(triple),
        identifier=str(getattr(meta_table, "identifier", storage.__metatable_identifier__)),
        physical_table=storage.__table__.name,
        frequency_id=frequency_id,
        feed=feed,
        adjustment=adjustment,
        cadence=str(storage.__cadence__),
        columns=[column.name for column in storage.__table__.columns],
        row_count=row_count,
        earliest_observation=earliest_observation,
        latest_observation=latest_observation,
    )


def list_market_data_datasets() -> list[MarketDataDataset]:
    from src.runtime import start_markets_engine

    start_markets_engine()
    return [
        _dataset_from_storage(triple, storage)
        for triple, storage in sorted(ALPACA_STOCK_BARS_STORAGE_BY_TRIPLE.items())
    ]


def get_market_data_dataset(dataset_uid: uuid.UUID | str) -> MarketDataDataset | None:
    expected_uid = str(dataset_uid)
    return next(
        (dataset for dataset in list_market_data_datasets() if dataset.uid == expected_uid),
        None,
    )


def _storage_for_dataset_uid(dataset_uid: uuid.UUID | str) -> tuple[type, MarketDataDataset]:
    dataset = get_market_data_dataset(dataset_uid)
    if dataset is None:
        raise LookupError(f"Market-data dataset {dataset_uid!s} does not exist.")
    for triple, storage in ALPACA_STOCK_BARS_STORAGE_BY_TRIPLE.items():
        if "/".join(triple) == dataset.key:
            return storage, dataset
    raise RuntimeError(f"No storage class is bound to dataset {dataset_uid!s}.")


def query_price_observations(
    dataset_uid: uuid.UUID | str,
    *,
    asset_uids: list[str] | None = None,
    asset_identifiers: list[str] | None = None,
    start: dt.datetime | None = None,
    end: dt.datetime | None = None,
    limit: int = 100,
    offset: int = 0,
    ordering: str = "-time_index",
) -> tuple[list[dict[str, Any]], int]:
    """Execute a bounded, governed query; callers cannot supply SQL."""
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import func, select

    from src.runtime import start_markets_engine

    storage, _ = _storage_for_dataset_uid(dataset_uid)
    runtime = start_markets_engine()
    statement = select(storage)
    if asset_uids:
        resolved_identifiers = _asset_identifiers_from_uids(asset_uids)
        statement = statement.where(storage.asset_identifier.in_(resolved_identifiers))
    if asset_identifiers:
        statement = statement.where(storage.asset_identifier.in_(asset_identifiers))
    if start is not None:
        statement = statement.where(storage.time_index >= start)
    if end is not None:
        statement = statement.where(storage.time_index <= end)
    descending = ordering.startswith("-")
    ordering_key = ordering.removeprefix("-")
    ordering_columns = {
        "time_index": storage.time_index,
        "asset_identifier": storage.asset_identifier,
    }
    if ordering_key not in ordering_columns:
        raise ValueError(f"Unsupported price-observation ordering {ordering!r}.")
    ordering_column = ordering_columns[ordering_key]
    ordering_expression = ordering_column.desc() if descending else ordering_column.asc()
    statement = statement.order_by(
        ordering_expression,
        storage.time_index.desc(),
        storage.asset_identifier.asc(),
    )
    count_statement = select(func.count().label("count")).select_from(statement.subquery())
    page_operation = compile_markets_statement(
        statement.limit(limit).offset(offset),
        context=runtime.context,
        operation="select",
        models=[storage],
        access="read",
    )
    count_operation = compile_markets_statement(
        count_statement,
        context=runtime.context,
        operation="select",
        models=[storage],
        access="read",
    )
    rows = operation_result_rows(execute_markets_operation(page_operation, context=runtime.context))
    count_rows = operation_result_rows(
        execute_markets_operation(count_operation, context=runtime.context)
    )
    total = int(count_rows[0].get("count", 0)) if count_rows else 0
    from src.assets.resolution import assets_by_unique_identifiers

    identifiers = list(dict.fromkeys(str(row["asset_identifier"]) for row in rows))
    assets_by_identifier = assets_by_unique_identifiers(identifiers)
    for row in rows:
        identifier = str(row["asset_identifier"])
        asset = assets_by_identifier.get(identifier)
        row["asset_uid"] = str(asset.uid) if asset else None
    return rows, total


def _asset_identifiers_from_uids(asset_uids: list[str]) -> list[str]:
    from src.assets.resolution import assets_by_uids

    assets = assets_by_uids(asset_uids)
    missing = [str(asset_uid) for asset_uid in asset_uids if str(asset_uid) not in assets]
    if missing:
        raise LookupError(f"These Asset UIDs do not exist: {sorted(missing)!r}")
    return [assets[str(asset_uid)].unique_identifier for asset_uid in asset_uids]


def _asset_identifiers_from_universe_uid(
    universe_uid: str,
) -> tuple[list[str], str, str]:
    from src.assets.resolution import asset_unique_identifiers_for_category
    from src.universes import require_asset_universe_links

    universe, _source, category = require_asset_universe_links(universe_uid)
    if not universe.is_active:
        raise ValueError(
            f"Asset Universe {universe_uid!s} is inactive and cannot be used for market-data "
            "updates."
        )
    return (
        asset_unique_identifiers_for_category(category.unique_identifier),
        category.unique_identifier,
        str(category.uid),
    )


def _asset_uids_from_identifiers(asset_identifiers: list[str]) -> list[str]:
    from src.assets.resolution import assets_by_unique_identifiers

    assets = assets_by_unique_identifiers(asset_identifiers)
    missing = [identifier for identifier in asset_identifiers if identifier not in assets]
    if missing:
        raise LookupError(f"These Asset identifiers do not exist: {sorted(missing)!r}")
    return [str(assets[identifier].uid) for identifier in asset_identifiers]


def build_market_data_update(
    *,
    configuration_uid: uuid.UUID | str,
    hash_namespace: str | None = None,
) -> tuple[Any, dict[str, Any]]:
    """Resolve one stored configuration into an updater without resolving Secrets.

    The resolver performs governed reads only. In particular it does not contact Alpaca and an
    ``account_holdings`` source never captures a new snapshot.
    """
    from src.account.services import get_account_registration
    from src.holdings import resolve_recent_account_holdings_assets
    from src.market_data.alpaca_bars import AlpacaStockBarsConfig, AlpacaStockBarsNode
    from src.market_data.configurations import get_bar_configuration
    from src.market_data.storage import storage_for
    from src.runtime import account_runtime_models, start_markets_engine

    start_markets_engine(models=account_runtime_models())
    configuration = get_bar_configuration(configuration_uid)
    if configuration is None:
        raise LookupError(f"Bar configuration {configuration_uid!s} does not exist.")
    if not configuration.enabled:
        raise ValueError(f"Bar configuration {configuration_uid!s} is disabled.")
    registration = get_account_registration(configuration.account_uid)
    if registration is None:
        raise LookupError(
            f"Alpaca account registration {configuration.account_uid!s} does not exist."
        )

    triple = (
        configuration.frequency_id,
        configuration.feed,
        configuration.adjustment,
    )
    storage = storage_for(*triple)
    dataset = _dataset_from_storage(triple, storage)
    source_snapshot: dict[str, Any] | None = None
    category_identifier: str | None = None
    asset_category_uid: str | None = None
    runtime_config_values: dict[str, Any] = {
        "frequency_id": configuration.frequency_id,
        "feed": configuration.feed,
        "adjustment": configuration.adjustment,
        "asset_source": configuration.asset_source,
    }
    if configuration.asset_source == "assets":
        asset_uids = [str(asset_uid) for asset_uid in configuration.asset_uids]
        identifiers = _asset_identifiers_from_uids(asset_uids)
        runtime_config_values["asset_list"] = identifiers
    elif configuration.asset_source == "universe":
        identifiers, category_identifier, asset_category_uid = _asset_identifiers_from_universe_uid(
            str(configuration.universe_uid)
        )
        asset_uids = _asset_uids_from_identifiers(identifiers)
        runtime_config_values["asset_category_unique_identifier"] = category_identifier
    else:
        account_scope = resolve_recent_account_holdings_assets(configuration.account_uid)
        identifiers = account_scope.asset_identifiers
        asset_uids = account_scope.asset_uids
        source_snapshot = {
            "holdings_set_uid": account_scope.holdings_set_uid,
            "snapshot_time": account_scope.snapshot_time,
            "max_age_days": 30,
        }
        runtime_config_values["account_uid"] = configuration.account_uid
    if not identifiers:
        raise ValueError("The stored bar configuration resolves to no assets.")

    config = AlpacaStockBarsConfig(**runtime_config_values)
    node = AlpacaStockBarsNode(
        config=config,
        resolved_asset_identifiers=identifiers,
        hash_namespace=hash_namespace,
    )
    summary = {
        "configuration": configuration.model_dump(mode="json"),
        "dataset": asdict(dataset),
        "account_uid": str(configuration.account_uid),
        "asset_source": configuration.asset_source,
        "asset_count": len(identifiers),
        "asset_uids": asset_uids,
        "asset_identifiers": identifiers,
        "universe_uid": (
            str(configuration.universe_uid) if configuration.universe_uid is not None else None
        ),
        "asset_category_unique_identifier": category_identifier,
        "asset_category_uid": asset_category_uid,
        "source_snapshot": source_snapshot,
        "update_hash": node.update_hash,
        "hash_namespace": hash_namespace,
    }
    return node, summary


def resolve_market_data_update(
    *,
    configuration_uid: uuid.UUID | str,
    hash_namespace: str | None = None,
) -> dict[str, Any]:
    """Return the current resolved source, output, and updater identity without executing."""
    _, summary = build_market_data_update(
        configuration_uid=configuration_uid,
        hash_namespace=hash_namespace,
    )
    return summary


def execute_market_data_update(
    *,
    configuration_uid: uuid.UUID | str,
    hash_namespace: str | None = None,
) -> dict[str, Any]:
    from src.account.services import get_account_registration

    node, summary = build_market_data_update(
        configuration_uid=configuration_uid,
        hash_namespace=hash_namespace,
    )
    registration = get_account_registration(summary["account_uid"])
    if registration is None:
        raise LookupError(f"Alpaca account registration {summary['account_uid']!s} does not exist.")
    credentials = resolve_alpaca_credentials(
        AlpacaSecretNames(
            api_key_secret_name=str(registration["api_key_secret_name"]),
            secret_key_secret_name=str(registration["secret_key_secret_name"]),
        )
    )
    node._historical_client = build_alpaca_historical_data_client(credentials=credentials)
    node._trading_client = build_alpaca_trading_client(
        credentials=credentials,
        paper=bool(registration["is_paper"]),
    )
    error_on_last_update, result = node.run()
    if error_on_last_update:
        raise RuntimeError("Alpaca market-data update reported an error.")
    return {
        **summary,
        "rows_persisted": 0 if result is None else len(result),
    }


__all__ = [
    "MarketDataDataset",
    "build_market_data_update",
    "execute_market_data_update",
    "get_market_data_dataset",
    "list_market_data_datasets",
    "query_price_observations",
    "resolve_market_data_update",
]
