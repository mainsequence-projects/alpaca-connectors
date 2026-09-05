"""Migration-first preparation for Alpaca-derived persistent ``InterpolatedPrices``."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from mainsequence.meta_tables.migrations import namespace_version_location

DYNAMIC_INTERPOLATED_PRICES_SOURCES_ENV = (
    "ALPACA_CONNECTORS_INTERPOLATED_PRICES_SOURCES"
)
DYNAMIC_MIGRATION_PROVIDER = "src.portfolios.interpolated_prices_migration:migration"
UPSAMPLE_FREQUENCY_ID = "1d"
INTERPOLATION_RULE = "ffill"


@dataclass(frozen=True, slots=True)
class InterpolatedPricesStorageSpec:
    frequency_id: str
    feed: str
    adjustment: str
    source_time_index_meta_table_uid: str
    source_cadence: str
    output_table_name: str
    output_identifier: str


def configured_alpaca_interpolated_prices_storage(
    *,
    source_time_index_meta_table_uid: str,
    source_cadence: str,
    upsample_frequency_id: str = UPSAMPLE_FREQUENCY_ID,
    intraday_bar_interpolation_rule: str = INTERPOLATION_RULE,
) -> type[Any]:
    from msm_portfolios.data_nodes.prices.storage import (
        configured_interpolated_prices_storage,
    )

    return configured_interpolated_prices_storage(
        source_time_index_meta_table_uid=str(source_time_index_meta_table_uid),
        source_cadence=str(source_cadence),
        upsample_frequency_id=str(upsample_frequency_id),
        intraday_bar_interpolation_rule=str(intraday_bar_interpolation_rule),
    )


def resolve_interpolated_prices_storage_specs() -> list[InterpolatedPricesStorageSpec]:
    """Resolve every migrated Alpaca bars profile with one catalog request."""
    from mainsequence.client import TimeIndexMetaTable
    from src.market_data import ALPACA_STOCK_BARS_STORAGE_BY_TRIPLE

    sources = []
    for triple, storage in sorted(ALPACA_STOCK_BARS_STORAGE_BY_TRIPLE.items()):
        sources.append(
            {
                "triple": triple,
                "storage": storage,
                "identifier": storage.__table__.name,
                "namespace": storage.__metatable_namespace__,
                "physical_table_name": storage.__table__.name,
            }
        )
    rows = TimeIndexMetaTable.filter_by_body(
        identifier__in=sorted({item["identifier"] for item in sources}),
        namespace__in=sorted({item["namespace"] for item in sources}),
        physical_table_name__in=sorted({item["physical_table_name"] for item in sources}),
        limit=max(len(sources) * 2, 1),
    )
    specs: list[InterpolatedPricesStorageSpec] = []
    for item in sources:
        exact = [
            row
            for row in rows
            if row.identifier == item["identifier"]
            and row.namespace == item["namespace"]
            and row.physical_table_name == item["physical_table_name"]
        ]
        if len(exact) != 1:
            frequency_id, feed, adjustment = item["triple"]
            raise RuntimeError(
                "Expected exactly one registered Alpaca bars TimeIndexMetaTable for "
                f"{frequency_id}/{feed}/{adjustment}; found {len(exact)}. Apply and verify "
                "the project migration before preparing portfolio interpolation storage."
            )
        frequency_id, feed, adjustment = item["triple"]
        source_uid = str(exact[0].uid)
        source_cadence = str(item["storage"].__cadence__)
        output = configured_alpaca_interpolated_prices_storage(
            source_time_index_meta_table_uid=source_uid,
            source_cadence=source_cadence,
        )
        specs.append(
            InterpolatedPricesStorageSpec(
                frequency_id=frequency_id,
                feed=feed,
                adjustment=adjustment,
                source_time_index_meta_table_uid=source_uid,
                source_cadence=source_cadence,
                output_table_name=output.__table__.name,
                output_identifier=output.metatable_identifier(),
            )
        )
    return specs


def dynamic_provider_env(
    specs: list[InterpolatedPricesStorageSpec],
) -> dict[str, str]:
    payload = [
        {
            "source_time_index_meta_table_uid": spec.source_time_index_meta_table_uid,
            "source_cadence": spec.source_cadence,
        }
        for spec in specs
    ]
    return {
        DYNAMIC_INTERPOLATED_PRICES_SOURCES_ENV: json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        )
    }


def dynamic_storage_models_from_env() -> list[type[Any]]:
    raw = os.environ.get(DYNAMIC_INTERPOLATED_PRICES_SOURCES_ENV)
    if not raw:
        raise RuntimeError(
            "The dynamic interpolation migration provider requires "
            f"{DYNAMIC_INTERPOLATED_PRICES_SOURCES_ENV}. Run "
            "`alpaca-connectors portfolio prepare-interpolated-prices` instead of invoking "
            "the provider directly."
        )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{DYNAMIC_INTERPOLATED_PRICES_SOURCES_ENV} must contain valid JSON."
        ) from exc
    if not isinstance(payload, list) or not payload:
        raise RuntimeError(
            f"{DYNAMIC_INTERPOLATED_PRICES_SOURCES_ENV} must contain a non-empty list."
        )
    models: dict[str, type[Any]] = {}
    for item in payload:
        if not isinstance(item, dict):
            raise RuntimeError("Every dynamic interpolation source must be an object.")
        source_uid = str(item.get("source_time_index_meta_table_uid") or "").strip()
        source_cadence = str(item.get("source_cadence") or "").strip()
        if not source_uid or not source_cadence:
            raise RuntimeError(
                "Every dynamic interpolation source requires a source UID and cadence."
            )
        model = configured_alpaca_interpolated_prices_storage(
            source_time_index_meta_table_uid=source_uid,
            source_cadence=source_cadence,
        )
        models[model.__table__.name] = model
    return [models[name] for name in sorted(models)]


def registered_interpolated_prices_by_table_name(
    table_names: list[str],
) -> dict[str, Any]:
    from mainsequence.client import TimeIndexMetaTable

    if not table_names:
        return {}
    rows = TimeIndexMetaTable.filter_by_body(
        physical_table_name__in=sorted(set(table_names)),
        limit=max(len(table_names) * 2, 1),
    )
    result: dict[str, Any] = {}
    for row in rows:
        name = str(row.physical_table_name)
        if name in result:
            raise RuntimeError(
                f"Multiple TimeIndexMetaTables use interpolation table {name!r}."
            )
        result[name] = row
    return result


def assert_interpolated_prices_registered(
    specs: list[InterpolatedPricesStorageSpec],
) -> None:
    table_names = [spec.output_table_name for spec in specs]
    registered = registered_interpolated_prices_by_table_name(table_names)
    missing = sorted(set(table_names) - registered.keys())
    if missing:
        raise RuntimeError(
            "Persistent portfolio InterpolatedPrices storage is not migrated: "
            + ", ".join(missing)
            + ". Run `alpaca-connectors portfolio prepare-interpolated-prices`."
        )


def prepare_interpolated_prices_schema(
    *,
    check_only: bool = False,
    revision_message: str | None = None,
) -> dict[str, Any]:
    """Generate/apply the dynamic provider revision and verify every output table."""
    specs = resolve_interpolated_prices_storage_specs()
    table_names = [spec.output_table_name for spec in specs]
    missing_revisions = [name for name in table_names if not _revision_contains_table(name)]
    registered = registered_interpolated_prices_by_table_name(table_names)
    missing_registered = sorted(set(table_names) - registered.keys())
    if check_only:
        if missing_revisions or missing_registered:
            raise RuntimeError(
                "Portfolio interpolation schema check failed. Missing revisions: "
                f"{missing_revisions}; missing registered tables: {missing_registered}."
            )
        return _preparation_summary(specs, registered, created_revision=False)

    provider_env = dynamic_provider_env(specs)
    created_revision = False
    if missing_revisions:
        created_revision = True
        _run_mainsequence(
            [
                "migrations",
                "revision",
                "--provider",
                DYNAMIC_MIGRATION_PROVIDER,
                "--autogenerate",
                "-m",
                revision_message or "add Alpaca portfolio interpolated prices",
            ],
            env=provider_env,
        )
        still_missing = [name for name in table_names if not _revision_contains_table(name)]
        if still_missing:
            raise RuntimeError(
                "The generated dynamic migration does not create: " + ", ".join(still_missing)
            )
    _run_mainsequence(
        [
            "migrations",
            "upgrade",
            "--provider",
            DYNAMIC_MIGRATION_PROVIDER,
            "head",
        ],
        env=provider_env,
    )
    registered = registered_interpolated_prices_by_table_name(table_names)
    missing_registered = sorted(set(table_names) - registered.keys())
    if missing_registered:
        raise RuntimeError(
            "Dynamic migration completed without registering: "
            + ", ".join(missing_registered)
        )
    return _preparation_summary(specs, registered, created_revision=created_revision)


def _preparation_summary(
    specs: list[InterpolatedPricesStorageSpec],
    registered: dict[str, Any],
    *,
    created_revision: bool,
) -> dict[str, Any]:
    return {
        "created_revision": created_revision,
        "dynamic_provider": DYNAMIC_MIGRATION_PROVIDER,
        "storages": [
            {
                **asdict(spec),
                "time_index_meta_table_uid": str(registered[spec.output_table_name].uid),
            }
            for spec in specs
        ],
    }


def _version_directory() -> Path:
    location = namespace_version_location("alpaca-connectors")
    package_name, separator, resource_path = location.partition(":")
    if not separator:
        raise RuntimeError(f"Invalid migration version location {location!r}.")
    root = resources.files(package_name)
    for part in resource_path.strip("/").split("/"):
        if part:
            root = root.joinpath(part)
    return Path(str(root))


def _revision_contains_table(table_name: str) -> bool:
    for path in _version_directory().glob("**/*.py"):
        if path.name == "__init__.py":
            continue
        content = path.read_text(encoding="utf-8")
        if table_name in content and "op.create_table" in content:
            return True
    return False


def _run_mainsequence(args: list[str], *, env: dict[str, str]) -> None:
    subprocess.run(
        [sys.executable, "-m", "mainsequence", *args],
        env={**os.environ, **env},
        check=True,
    )


__all__ = [
    "DYNAMIC_INTERPOLATED_PRICES_SOURCES_ENV",
    "DYNAMIC_MIGRATION_PROVIDER",
    "INTERPOLATION_RULE",
    "InterpolatedPricesStorageSpec",
    "UPSAMPLE_FREQUENCY_ID",
    "assert_interpolated_prices_registered",
    "configured_alpaca_interpolated_prices_storage",
    "dynamic_provider_env",
    "dynamic_storage_models_from_env",
    "prepare_interpolated_prices_schema",
    "registered_interpolated_prices_by_table_name",
    "resolve_interpolated_prices_storage_specs",
]
