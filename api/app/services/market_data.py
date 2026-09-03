"""API shaping for migrated price datasets."""

from __future__ import annotations

from dataclasses import asdict

from src.market_data import (
    get_market_data_dataset,
    list_market_data_datasets,
    query_price_observations,
)

from ..schemas import MarketDataDatasetResponse
from .common import collection_response


def list_datasets(*, limit: int, offset: int, ordering: str):
    datasets = list_market_data_datasets()
    descending = ordering.startswith("-")
    ordering_key = ordering.removeprefix("-")
    if ordering_key not in {"frequency_id", "feed", "adjustment"}:
        raise ValueError(f"Unsupported dataset ordering {ordering!r}.")
    datasets.sort(key=lambda item: getattr(item, ordering_key), reverse=descending)
    page = datasets[offset : offset + limit]
    return collection_response(
        items=[asdict(item) for item in page],
        total=len(datasets),
        limit=limit,
        offset=offset,
    )


def get_dataset(dataset_uid: str) -> MarketDataDatasetResponse | None:
    dataset = get_market_data_dataset(dataset_uid)
    return MarketDataDatasetResponse.model_validate(asdict(dataset)) if dataset else None


def list_observations(dataset_uid: str, *, limit: int, offset: int, **filters):
    rows, total = query_price_observations(
        dataset_uid,
        limit=limit,
        offset=offset,
        **filters,
    )
    return collection_response(items=rows, total=total, limit=limit, offset=offset)


__all__ = [
    "get_dataset",
    "list_datasets",
    "list_observations",
]
