from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.portfolios.interpolated_prices_schema import (
    DYNAMIC_INTERPOLATED_PRICES_SOURCES_ENV,
    InterpolatedPricesStorageSpec,
    dynamic_provider_env,
    dynamic_storage_models_from_env,
    prepare_interpolated_prices_schema,
    resolve_interpolated_prices_storage_specs,
)


def storage(name: str, *, namespace: str = "mainsequence.markets"):
    return SimpleNamespace(
        __table__=SimpleNamespace(name=name),
        __metatable_namespace__=namespace,
        __cadence__="1d",
    )


def output_storage(name: str):
    return SimpleNamespace(
        __table__=SimpleNamespace(name=name),
        metatable_identifier=lambda: f"InterpolatedPricesTS.{name}",
    )


def test_resolve_interpolation_specs_queries_all_bar_profiles_once() -> None:
    profiles = {
        ("1d", "iex", "raw"): storage("bars_iex"),
        ("1d", "sip", "all"): storage("bars_sip"),
    }
    rows = [
        SimpleNamespace(
            uid="11111111-1111-4111-8111-111111111111",
            identifier="bars_iex",
            namespace="mainsequence.markets",
            physical_table_name="bars_iex",
        ),
        SimpleNamespace(
            uid="22222222-2222-4222-8222-222222222222",
            identifier="bars_sip",
            namespace="mainsequence.markets",
            physical_table_name="bars_sip",
        ),
    ]

    with (
        patch("src.market_data.ALPACA_STOCK_BARS_STORAGE_BY_TRIPLE", profiles),
        patch("mainsequence.client.TimeIndexMetaTable.filter_by_body", return_value=rows) as query,
        patch(
            "src.portfolios.interpolated_prices_schema.configured_alpaca_interpolated_prices_storage",
            side_effect=[output_storage("interp_iex"), output_storage("interp_sip")],
        ),
    ):
        specs = resolve_interpolated_prices_storage_specs()

    query.assert_called_once()
    assert [spec.output_table_name for spec in specs] == ["interp_iex", "interp_sip"]


def test_dynamic_provider_env_builds_all_configured_storage_models() -> None:
    specs = [
        InterpolatedPricesStorageSpec(
            frequency_id="1d",
            feed="sip",
            adjustment="all",
            source_time_index_meta_table_uid="11111111-1111-4111-8111-111111111111",
            source_cadence="1d",
            output_table_name="interp_sip",
            output_identifier="InterpolatedPricesTS.sip",
        )
    ]
    environment = dynamic_provider_env(specs)
    payload = json.loads(environment[DYNAMIC_INTERPOLATED_PRICES_SOURCES_ENV])
    assert payload == [
        {
            "source_cadence": "1d",
            "source_time_index_meta_table_uid": "11111111-1111-4111-8111-111111111111",
        }
    ]

    with (
        patch.dict("os.environ", environment, clear=False),
        patch(
            "src.portfolios.interpolated_prices_schema.configured_alpaca_interpolated_prices_storage",
            return_value=output_storage("interp_sip"),
        ),
    ):
        models = dynamic_storage_models_from_env()
    assert [model.__table__.name for model in models] == ["interp_sip"]


def test_dynamic_provider_rejects_missing_configuration() -> None:
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(RuntimeError, match="prepare-interpolated-prices"):
            dynamic_storage_models_from_env()


def test_check_only_requires_both_revision_and_registered_table() -> None:
    spec = InterpolatedPricesStorageSpec(
        frequency_id="1d",
        feed="sip",
        adjustment="all",
        source_time_index_meta_table_uid="11111111-1111-4111-8111-111111111111",
        source_cadence="1d",
        output_table_name="interp_sip",
        output_identifier="InterpolatedPricesTS.sip",
    )
    with (
        patch(
            "src.portfolios.interpolated_prices_schema.resolve_interpolated_prices_storage_specs",
            return_value=[spec],
        ),
        patch(
            "src.portfolios.interpolated_prices_schema._revision_contains_table",
            return_value=True,
        ),
        patch(
            "src.portfolios.interpolated_prices_schema.registered_interpolated_prices_by_table_name",
            return_value={"interp_sip": SimpleNamespace(uid="storage-uid")},
        ),
        patch("src.portfolios.interpolated_prices_schema._run_mainsequence") as run,
    ):
        result = prepare_interpolated_prices_schema(check_only=True)

    run.assert_not_called()
    assert result["storages"][0]["time_index_meta_table_uid"] == "storage-uid"
