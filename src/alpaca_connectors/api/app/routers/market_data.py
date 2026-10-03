"""Migrated price dataset API."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Query

from ..errors import api_http_error, not_found
from ..schemas import (
    MarketDataDatasetResponse,
    ResourceCollection,
    ResourceDiscoveryResponse,
)
from ..services.common import resource_discovery, validate_ordering, validate_page_window
from ..services.market_data import (
    get_dataset,
    list_datasets,
    list_observations,
)

router = APIRouter(prefix="/v1/market-data/datasets", tags=["Market Data"])


@router.get("", response_model=ResourceCollection)
def datasets_list(
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    ordering: str = "frequency_id",
) -> ResourceCollection:
    validate_page_window(limit=limit, offset=offset)
    validate_ordering(ordering, allowed_fields={"frequency_id", "feed", "adjustment"})
    try:
        return list_datasets(limit=limit, offset=offset, ordering=ordering)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.get("/discovery", response_model=ResourceDiscoveryResponse)
def datasets_discovery() -> ResourceDiscoveryResponse:
    return resource_discovery(
        resource_id="alpaca-market-data-datasets",
        label="Alpaca Price Datasets",
        item_label="dataset",
        identity_fields=["uid"],
        orderable_fields=["frequency_id", "feed", "adjustment"],
        columns=[
            {"id": "identifier", "header": "Dataset", "hideable": False},
            {"id": "frequency_id", "header": "Frequency", "sortable_key": "frequency_id"},
            {"id": "feed", "header": "Feed", "sortable_key": "feed"},
            {"id": "adjustment", "header": "Adjustment", "sortable_key": "adjustment"},
            {"id": "row_count", "header": "Rows", "data_type": "number"},
            {
                "id": "latest_observation",
                "header": "Latest Observation",
                "data_type": "datetime",
            },
            {"id": "uid", "header": "MetaTable UID"},
        ],
    )


@router.get("/{dataset_uid}", response_model=MarketDataDatasetResponse)
def dataset_get(dataset_uid: str) -> MarketDataDatasetResponse:
    dataset = get_dataset(dataset_uid)
    if dataset is None:
        raise not_found("Market-data dataset not found.")
    return dataset


@router.get("/{dataset_uid}/observations", response_model=ResourceCollection)
def observations_list(
    dataset_uid: str,
    asset_uid: list[str] | None = Query(default=None),
    asset_identifier: list[str] | None = Query(default=None),
    start: dt.datetime | None = None,
    end: dt.datetime | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    ordering: str = "-time_index",
) -> ResourceCollection:
    validate_page_window(limit=limit, offset=offset)
    validate_ordering(ordering, allowed_fields={"time_index", "asset_identifier"})
    try:
        return list_observations(
            dataset_uid,
            asset_uids=asset_uid,
            asset_identifiers=asset_identifier,
            start=start,
            end=end,
            ordering=ordering,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.get(
    "/{dataset_uid}/observations/discovery",
    response_model=ResourceDiscoveryResponse,
)
def observations_discovery(dataset_uid: str) -> ResourceDiscoveryResponse:
    del dataset_uid
    return resource_discovery(
        resource_id="alpaca-price-observations",
        label="Price Observations",
        item_label="observation",
        identity_fields=["time_index", "asset_identifier"],
        filterable_fields=["asset_uid", "asset_identifier"],
        orderable_fields=["time_index", "asset_identifier"],
        columns=[
            {
                "id": "time_index",
                "header": "Time",
                "data_type": "datetime",
                "sortable_key": "time_index",
                "hideable": False,
            },
            {
                "id": "asset_identifier",
                "header": "Asset",
                "sortable_key": "asset_identifier",
                "filter_key": "asset_identifier",
            },
            {"id": "asset_uid", "header": "Asset UID", "filter_key": "asset_uid"},
            {"id": "open", "header": "Open", "data_type": "number"},
            {"id": "high", "header": "High", "data_type": "number"},
            {"id": "low", "header": "Low", "data_type": "number"},
            {"id": "close", "header": "Close", "data_type": "number"},
            {"id": "volume", "header": "Volume", "data_type": "number"},
        ],
    )
