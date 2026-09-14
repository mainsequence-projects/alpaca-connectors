"""Structured project tools used by the Alpaca Connectors coding agent.

The functions in this module deliberately reuse the same application-service
layer as the FastAPI surface.  They do not call the API over HTTP and they do
not contain a second copy of the connector's business logic.
"""

from __future__ import annotations

import copy
import datetime as dt
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from api.app.schemas import (
    AccountRegistrationRequest,
    AccountUpdateRequest,
    AssetRegistrationRequest,
    AssetUniverseCreateRequest,
    AssetUniverseUpdateRequest,
    BarConfigurationCreateRequest,
    BarConfigurationResolveRequest,
    BarConfigurationUpdateRequest,
    PortfolioConfigurationCreateRequest,
    PortfolioConfigurationUpdateRequest,
    PortfolioRebalanceConfigurationCreateRequest,
    PortfolioRebalanceConfigurationUpdateRequest,
    SignalJobConfigurationCreateRequest,
    SignalJobConfigurationUpdateRequest,
    UniverseSourceCreateRequest,
    UniverseSourceUpdateRequest,
)
from pydantic import BaseModel

ToolHandler = Callable[[Mapping[str, Any]], Any]
ExecutionMode = Literal["parallel", "sequential"]


@dataclass(frozen=True, slots=True)
class ProjectAgentTool:
    """One Tau-visible tool backed by a synchronous project service."""

    name: str
    label: str
    description: str
    parameters: dict[str, Any]
    execution_mode: ExecutionMode
    handler: ToolHandler


def _inline_model_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Return a self-contained JSON schema suitable for nesting in a tool case."""

    schema = model.model_json_schema()
    definitions = schema.pop("$defs", {})

    def resolve(value: Any) -> Any:
        if isinstance(value, list):
            return [resolve(item) for item in value]
        if not isinstance(value, dict):
            return value
        reference = value.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/$defs/"):
            key = reference.removeprefix("#/$defs/")
            if key not in definitions:
                raise ValueError(f"Unresolved Pydantic schema reference: {reference}")
            replacement = resolve(copy.deepcopy(definitions[key]))
            siblings = {key: item for key, item in value.items() if key != "$ref"}
            if siblings:
                replacement = {**replacement, **resolve(siblings)}
            return replacement
        return {key: resolve(item) for key, item in value.items()}

    return resolve(schema)


def _operation_case(
    operation: str,
    *,
    properties: Mapping[str, Mapping[str, Any]] | None = None,
    required: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "operation": {"type": "string", "const": operation},
            **dict(properties or {}),
        },
        "required": ["operation", *required],
        "additionalProperties": False,
    }


def _operations(*cases: dict[str, Any]) -> dict[str, Any]:
    return {"oneOf": list(cases)}


def _string(description: str) -> dict[str, Any]:
    return {"type": "string", "minLength": 1, "description": description}


def _uid(label: str) -> dict[str, Any]:
    return _string(f"Public UID of the {label} in the current Organization Environment.")


def _confirmation() -> dict[str, Any]:
    return _string("Exact destructive confirmation text: DELETE <uid>.")


def _page_properties(*, maximum: int = 100) -> dict[str, dict[str, Any]]:
    return {
        "limit": {"type": "integer", "minimum": 1, "maximum": maximum, "default": 25},
        "offset": {"type": "integer", "minimum": 0, "default": 0},
    }


def _request(model: type[BaseModel], description: str) -> dict[str, Any]:
    schema = _inline_model_schema(model)
    schema["description"] = description
    return schema


def _operation(arguments: Mapping[str, Any], allowed: set[str]) -> str:
    operation = str(arguments.get("operation") or "").strip()
    if operation not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ValueError(f"operation must be one of: {choices}.")
    return operation


def _required_text(arguments: Mapping[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string.")
    return value.strip()


def _optional_text(arguments: Mapping[str, Any], key: str) -> str | None:
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string when supplied.")
    return value.strip()


def _integer(
    arguments: Mapping[str, Any],
    key: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = arguments.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{key} must be an integer between {minimum} and {maximum}.")
    return value


def _page(arguments: Mapping[str, Any], *, maximum: int = 100) -> tuple[int, int]:
    limit = _integer(arguments, "limit", default=25, minimum=1, maximum=maximum)
    offset = _integer(arguments, "offset", default=0, minimum=0, maximum=2_147_483_647)
    if offset % limit:
        raise ValueError("offset must be an exact multiple of limit.")
    return limit, offset


def _request_model(arguments: Mapping[str, Any], model: type[BaseModel]) -> BaseModel:
    raw = arguments.get("request")
    if not isinstance(raw, Mapping):
        raise ValueError("request must be an object.")
    return model.model_validate(dict(raw))


def _datetime(value: Any, *, key: str) -> dt.datetime | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value
    if not isinstance(value, str):
        raise ValueError(f"{key} must be an ISO-8601 datetime string.")
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{key} must be an ISO-8601 datetime string.") from exc


def _require_found(value: Any, *, label: str, uid: str) -> Any:
    if value is None:
        raise LookupError(f"{label} {uid} does not exist.")
    return value


def _confirm_delete(arguments: Mapping[str, Any], uid: str) -> None:
    expected = f"DELETE {uid}"
    if arguments.get("confirmation") != expected:
        raise ValueError(f"confirmation must exactly equal {expected!r}.")


def project_state(_: Mapping[str, Any]) -> dict[str, Any]:
    from api.app.capabilities import capability_summaries
    from api.app.services.project_state import get_project_configuration

    configuration = get_project_configuration()
    return {
        "configuration": configuration,
        "capabilities": capability_summaries(),
    }


def query_assets(arguments: Mapping[str, Any]) -> Any:
    from api.app.services.assets import get_asset_resource, list_asset_resources

    operation = _operation(arguments, {"get", "list"})
    if operation == "get":
        asset_uid = _required_text(arguments, "asset_uid")
        return _require_found(
            get_asset_resource(asset_uid),
            label="Asset",
            uid=asset_uid,
        )
    limit, offset = _page(arguments)
    return list_asset_resources(
        limit=limit,
        offset=offset,
        search=_optional_text(arguments, "search"),
        ordering=str(arguments.get("ordering") or "name"),
        category_uid=_optional_text(arguments, "category_uid"),
    )


def register_assets(arguments: Mapping[str, Any]) -> Any:
    from api.app.services.assets import (
        build_asset_registration_discovery,
        execute_asset_registration,
    )

    operation = _operation(arguments, {"execute", "plan"})
    request = _request_model(arguments, AssetRegistrationRequest)
    if operation == "plan":
        return build_asset_registration_discovery(request)
    return execute_asset_registration(request)


def query_accounts(arguments: Mapping[str, Any]) -> Any:
    from api.app.services.accounts import get_account, list_accounts, list_secret_references
    from api.app.services.holdings import get_holdings_snapshot, list_holdings

    operation = _operation(
        arguments,
        {"get", "holdings", "holdings_snapshot", "list", "secret_references"},
    )
    if operation == "get":
        account_uid = _required_text(arguments, "account_uid")
        return _require_found(get_account(account_uid), label="Account", uid=account_uid)
    if operation == "secret_references":
        limit, offset = _page(arguments)
        return list_secret_references(
            limit=limit,
            offset=offset,
            search=_optional_text(arguments, "search"),
        )
    if operation == "holdings_snapshot":
        account_uid = _required_text(arguments, "account_uid")
        holdings_set_uid = _required_text(arguments, "holdings_set_uid")
        return _require_found(
            get_holdings_snapshot(account_uid, holdings_set_uid),
            label="Holdings snapshot",
            uid=holdings_set_uid,
        )
    if operation == "holdings":
        limit, offset = _page(arguments)
        return list_holdings(
            _required_text(arguments, "account_uid"),
            limit=limit,
            offset=offset,
            as_of=_datetime(arguments.get("as_of"), key="as_of"),
            latest_only=bool(arguments.get("latest_only", True)),
            asset_identifiers=list(arguments.get("asset_identifiers") or []) or None,
            ordering=str(arguments.get("ordering") or "-time_index"),
        )
    limit, offset = _page(arguments)
    return list_accounts(
        limit=limit,
        offset=offset,
        search=_optional_text(arguments, "search"),
        active=arguments.get("active"),
        is_paper=arguments.get("is_paper"),
        ordering=str(arguments.get("ordering") or "account_name"),
    )


def manage_account(arguments: Mapping[str, Any]) -> Any:
    from api.app.services.accounts import (
        account_delete_blockers,
        create_account_registration,
        preflight_account_registration,
        refresh_account,
        remove_account,
        update_account,
    )
    from api.app.services.holdings import capture_holdings, preflight_holdings

    operation = _operation(
        arguments,
        {"capture_holdings", "create", "delete", "plan_create", "refresh", "update"},
    )
    if operation in {"create", "plan_create"}:
        request = _request_model(arguments, AccountRegistrationRequest)
        if operation == "plan_create":
            return preflight_account_registration(request)
        return create_account_registration(request)
    account_uid = _required_text(arguments, "account_uid")
    if operation == "update":
        return update_account(account_uid, _request_model(arguments, AccountUpdateRequest))
    if operation == "refresh":
        return refresh_account(account_uid)
    if operation == "capture_holdings":
        preflight = preflight_holdings(account_uid)
        if not preflight.get("allowed", False):
            blockers = "; ".join(preflight.get("blockers") or ["Account is not ready."])
            raise ValueError(blockers)
        return capture_holdings(account_uid)
    _confirm_delete(arguments, account_uid)
    blockers = account_delete_blockers(account_uid)
    if blockers:
        raise ValueError(" ".join(blockers))
    return remove_account(account_uid)


def query_universes(arguments: Mapping[str, Any]) -> Any:
    from api.app.services.universe_sources import (
        get_source,
        list_sources,
        preview_source,
    )
    from api.app.services.universes import (
        get_universe,
        list_universe_assets,
        list_universes,
        preview_universe_run,
    )

    operation = _operation(
        arguments,
        {
            "get",
            "get_source",
            "list",
            "list_sources",
            "members",
            "preview_extraction",
            "preview_source",
        },
    )
    if operation == "get":
        universe_uid = _required_text(arguments, "universe_uid")
        return _require_found(get_universe(universe_uid), label="Universe", uid=universe_uid)
    if operation == "get_source":
        source_uid = _required_text(arguments, "source_uid")
        return _require_found(get_source(source_uid), label="Universe source", uid=source_uid)
    if operation == "preview_source":
        return preview_source(
            _required_text(arguments, "source_uid"),
            timeout=float(arguments.get("timeout", 30.0)),
        )
    if operation == "preview_extraction":
        return preview_universe_run(
            _required_text(arguments, "universe_uid"),
            account_uid=_required_text(arguments, "account_uid"),
            timeout=float(arguments.get("timeout", 30.0)),
        )
    limit, offset = _page(arguments)
    if operation == "members":
        return list_universe_assets(
            _required_text(arguments, "universe_uid"),
            limit=limit,
            offset=offset,
            search=_optional_text(arguments, "search"),
            ordering=str(arguments.get("ordering") or "name"),
        )
    if operation == "list_sources":
        return list_sources(
            limit=limit,
            offset=offset,
            search=_optional_text(arguments, "search"),
            enabled=arguments.get("enabled"),
            ordering=str(arguments.get("ordering") or "name"),
        )
    return list_universes(
        limit=limit,
        offset=offset,
        search=_optional_text(arguments, "search"),
        ordering=str(arguments.get("ordering") or "display_name"),
    )


def manage_universe(arguments: Mapping[str, Any]) -> Any:
    from api.app.services.universe_sources import (
        create_source,
        delete_source,
        update_source,
    )
    from api.app.services.universes import (
        create_universe,
        delete_universe,
        run_universe,
        universe_delete_blockers,
        update_universe,
    )

    operation = _operation(
        arguments,
        {
            "activate",
            "create",
            "create_source",
            "deactivate",
            "delete",
            "delete_source",
            "extract_components",
            "update",
            "update_source",
        },
    )
    if operation == "create":
        return create_universe(_request_model(arguments, AssetUniverseCreateRequest))
    if operation == "create_source":
        return create_source(_request_model(arguments, UniverseSourceCreateRequest))
    if operation == "update_source":
        return update_source(
            _required_text(arguments, "source_uid"),
            _request_model(arguments, UniverseSourceUpdateRequest),
        )
    if operation == "delete_source":
        source_uid = _required_text(arguments, "source_uid")
        _confirm_delete(arguments, source_uid)
        return delete_source(source_uid)
    universe_uid = _required_text(arguments, "universe_uid")
    if operation == "update":
        return update_universe(
            universe_uid,
            _request_model(arguments, AssetUniverseUpdateRequest),
        )
    if operation in {"activate", "deactivate"}:
        return update_universe(
            universe_uid,
            AssetUniverseUpdateRequest(is_active=operation == "activate"),
        )
    if operation == "extract_components":
        return run_universe(
            universe_uid,
            account_uid=_required_text(arguments, "account_uid"),
            timeout=float(arguments.get("timeout", 30.0)),
        )
    _confirm_delete(arguments, universe_uid)
    blockers = universe_delete_blockers(universe_uid)
    if blockers:
        raise ValueError(" ".join(blockers))
    return delete_universe(universe_uid)


def query_bar_configurations(arguments: Mapping[str, Any]) -> Any:
    from api.app.services.bar_configurations import (
        get_configuration,
        list_configurations,
        resolve_configuration,
    )
    from api.app.services.market_data import get_dataset, list_datasets, list_observations

    operation = _operation(
        arguments,
        {"dataset", "datasets", "get", "list", "observations", "resolve"},
    )
    if operation == "get":
        configuration_uid = _required_text(arguments, "configuration_uid")
        return _require_found(
            get_configuration(configuration_uid),
            label="Bar configuration",
            uid=configuration_uid,
        )
    if operation == "resolve":
        return resolve_configuration(
            _required_text(arguments, "configuration_uid"),
            BarConfigurationResolveRequest(
                hash_namespace=_optional_text(arguments, "hash_namespace")
            ),
        )
    if operation == "dataset":
        dataset_uid = _required_text(arguments, "dataset_uid")
        return _require_found(get_dataset(dataset_uid), label="Dataset", uid=dataset_uid)
    if operation == "observations":
        limit, offset = _page(arguments, maximum=1_000)
        return list_observations(
            _required_text(arguments, "dataset_uid"),
            asset_uids=list(arguments.get("asset_uids") or []) or None,
            asset_identifiers=list(arguments.get("asset_identifiers") or []) or None,
            start=_datetime(arguments.get("start"), key="start"),
            end=_datetime(arguments.get("end"), key="end"),
            ordering=str(arguments.get("ordering") or "-time_index"),
            limit=limit,
            offset=offset,
        )
    limit, offset = _page(arguments)
    if operation == "datasets":
        return list_datasets(
            limit=limit,
            offset=offset,
            ordering=str(arguments.get("ordering") or "frequency_id"),
        )
    return list_configurations(
        limit=limit,
        offset=offset,
        search=_optional_text(arguments, "search"),
        enabled=arguments.get("enabled"),
        asset_source=_optional_text(arguments, "asset_source"),
        ordering=str(arguments.get("ordering") or "name"),
    )


def manage_bar_configuration(arguments: Mapping[str, Any]) -> Any:
    from api.app.services.bar_configurations import (
        create_configuration,
        delete_configuration,
        submit_configuration_update,
        update_configuration,
    )

    operation = _operation(arguments, {"create", "delete", "run", "update"})
    if operation == "create":
        return create_configuration(_request_model(arguments, BarConfigurationCreateRequest))
    configuration_uid = _required_text(arguments, "configuration_uid")
    if operation == "update":
        return update_configuration(
            configuration_uid,
            _request_model(arguments, BarConfigurationUpdateRequest),
        )
    if operation == "run":
        return submit_configuration_update(configuration_uid)
    _confirm_delete(arguments, configuration_uid)
    return delete_configuration(configuration_uid)


def query_etf_signals(arguments: Mapping[str, Any]) -> Any:
    from api.app.services.signal_jobs import (
        configuration_runs,
        get_configuration,
        get_configuration_observations,
        list_configurations,
    )

    operation = _operation(arguments, {"get", "list", "observations", "runs"})
    if operation == "get":
        configuration_uid = _required_text(arguments, "configuration_uid")
        return _require_found(
            get_configuration(configuration_uid),
            label="ETF signal configuration",
            uid=configuration_uid,
        )
    if operation == "observations":
        return get_configuration_observations(
            _required_text(arguments, "configuration_uid"),
            limit=_integer(arguments, "limit", default=100, minimum=1, maximum=100),
        )
    if operation == "runs":
        return configuration_runs(
            _required_text(arguments, "configuration_uid"),
            limit=_integer(arguments, "limit", default=25, minimum=1, maximum=100),
        )
    limit, offset = _page(arguments)
    return list_configurations(
        limit=limit,
        offset=offset,
        search=_optional_text(arguments, "search"),
        enabled=arguments.get("enabled"),
        lifecycle_state=_optional_text(arguments, "lifecycle_state"),
        ordering=str(arguments.get("ordering") or "name"),
    )


def manage_etf_signal(arguments: Mapping[str, Any]) -> Any:
    from api.app.services.signal_jobs import (
        create_configuration,
        delete_configuration,
        run_configuration,
        update_configuration,
    )

    operation = _operation(arguments, {"create", "delete", "run", "update"})
    if operation == "create":
        return create_configuration(
            _request_model(arguments, SignalJobConfigurationCreateRequest)
        )
    configuration_uid = _required_text(arguments, "configuration_uid")
    if operation == "update":
        return update_configuration(
            configuration_uid,
            _request_model(arguments, SignalJobConfigurationUpdateRequest),
        )
    if operation == "run":
        return run_configuration(configuration_uid)
    _confirm_delete(arguments, configuration_uid)
    return delete_configuration(configuration_uid)


def query_rebalance_configurations(arguments: Mapping[str, Any]) -> Any:
    from api.app.services.portfolio_configurations import get_rebalance, list_rebalances

    operation = _operation(arguments, {"get", "list"})
    if operation == "get":
        configuration_uid = _required_text(arguments, "configuration_uid")
        return _require_found(
            get_rebalance(configuration_uid),
            label="Rebalance configuration",
            uid=configuration_uid,
        )
    limit, offset = _page(arguments)
    return list_rebalances(
        limit=limit,
        offset=offset,
        search=_optional_text(arguments, "search"),
        ordering=str(arguments.get("ordering") or "name"),
    )


def manage_rebalance_configuration(arguments: Mapping[str, Any]) -> Any:
    from api.app.services.portfolio_configurations import (
        create_rebalance,
        delete_rebalance,
        update_rebalance,
    )

    operation = _operation(arguments, {"create", "delete", "update"})
    if operation == "create":
        return create_rebalance(
            _request_model(arguments, PortfolioRebalanceConfigurationCreateRequest)
        )
    configuration_uid = _required_text(arguments, "configuration_uid")
    if operation == "update":
        return update_rebalance(
            configuration_uid,
            _request_model(arguments, PortfolioRebalanceConfigurationUpdateRequest),
        )
    _confirm_delete(arguments, configuration_uid)
    return delete_rebalance(configuration_uid)


def query_etf_portfolios(arguments: Mapping[str, Any]) -> Any:
    from api.app.services.portfolio_configurations import (
        configuration_runs,
        get_configuration_detail,
        list_configurations,
    )

    operation = _operation(arguments, {"get", "list", "runs"})
    if operation == "get":
        configuration_uid = _required_text(arguments, "configuration_uid")
        return _require_found(
            get_configuration_detail(
                configuration_uid,
                observation_limit=_integer(
                    arguments,
                    "observation_limit",
                    default=2_500,
                    minimum=1,
                    maximum=5_000,
                ),
            ),
            label="ETF portfolio configuration",
            uid=configuration_uid,
        )
    if operation == "runs":
        return configuration_runs(
            _required_text(arguments, "configuration_uid"),
            limit=_integer(arguments, "limit", default=25, minimum=1, maximum=100),
        )
    limit, offset = _page(arguments)
    return list_configurations(
        limit=limit,
        offset=offset,
        search=_optional_text(arguments, "search"),
        signal_configuration_uid=_optional_text(arguments, "signal_configuration_uid"),
        bars_configuration_uid=_optional_text(arguments, "bars_configuration_uid"),
        ordering=str(arguments.get("ordering") or "name"),
    )


def manage_etf_portfolio(arguments: Mapping[str, Any]) -> Any:
    from api.app.services.portfolio_configurations import (
        create_configuration,
        delete_configuration,
        run_configuration,
        update_configuration,
    )

    operation = _operation(arguments, {"create", "delete", "run", "update"})
    if operation == "create":
        return create_configuration(
            _request_model(arguments, PortfolioConfigurationCreateRequest)
        )
    configuration_uid = _required_text(arguments, "configuration_uid")
    if operation == "update":
        return update_configuration(
            configuration_uid,
            _request_model(arguments, PortfolioConfigurationUpdateRequest),
        )
    if operation == "run":
        return run_configuration(configuration_uid)
    _confirm_delete(arguments, configuration_uid)
    return delete_configuration(configuration_uid)


def get_job_run_status(arguments: Mapping[str, Any]) -> Any:
    from api.app.services.operations import get_job_run

    job_run_uid = _required_text(arguments, "job_run_uid")
    return _require_found(get_job_run(job_run_uid), label="JobRun", uid=job_run_uid)


PAGE = _page_properties()
SEARCH = {"search": {"type": "string"}}
ORDERING = {"ordering": {"type": "string"}}
ACCOUNT_BOOLEAN_FILTERS = {
    "active": {"type": "boolean"},
    "is_paper": {"type": "boolean"},
}
TIMEOUT = {
    "timeout": {
        "type": "number",
        "exclusiveMinimum": 0,
        "maximum": 300,
        "default": 30,
    }
}


PROJECT_AGENT_TOOLS: tuple[ProjectAgentTool, ...] = (
    ProjectAgentTool(
        name="alpaca_project_state",
        label="Inspect Alpaca Project State",
        description=(
            "Read the implemented Alpaca Connectors capability catalog and current runtime "
            "configuration for this CodeRepositoryBranch."
        ),
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        execution_mode="parallel",
        handler=project_state,
    ),
    ProjectAgentTool(
        name="alpaca_query_assets",
        label="Query Alpaca Assets",
        description="List or retrieve registered canonical Alpaca-backed Assets.",
        parameters=_operations(
            _operation_case(
                "list",
                properties={**PAGE, **SEARCH, **ORDERING, "category_uid": _uid("AssetCategory")},
            ),
            _operation_case("get", properties={"asset_uid": _uid("Asset")}, required=("asset_uid",)),
        ),
        execution_mode="parallel",
        handler=query_assets,
    ),
    ProjectAgentTool(
        name="alpaca_register_assets",
        label="Register Alpaca Assets",
        description=(
            "Plan or execute exact-symbol Alpaca asset registration through one registered "
            "account. Secret values are never accepted."
        ),
        parameters=_operations(
            _operation_case(
                "plan",
                properties={
                    "request": _request(
                        AssetRegistrationRequest,
                        "Exact asset-registration request using a registered Account UID.",
                    )
                },
                required=("request",),
            ),
            _operation_case(
                "execute",
                properties={
                    "request": _request(
                        AssetRegistrationRequest,
                        "Exact asset-registration request using a registered Account UID.",
                    )
                },
                required=("request",),
            ),
        ),
        execution_mode="sequential",
        handler=register_assets,
    ),
    ProjectAgentTool(
        name="alpaca_query_accounts",
        label="Query Alpaca Accounts",
        description=(
            "List or retrieve registered Alpaca accounts, safe Secret names, and account "
            "holdings. Holdings are queried only for the selected account."
        ),
        parameters=_operations(
            _operation_case(
                "list",
                properties={**PAGE, **SEARCH, **ORDERING, **ACCOUNT_BOOLEAN_FILTERS},
            ),
            _operation_case(
                "get",
                properties={"account_uid": _uid("Account")},
                required=("account_uid",),
            ),
            _operation_case(
                "secret_references",
                properties={**PAGE, **SEARCH},
            ),
            _operation_case(
                "holdings",
                properties={
                    "account_uid": _uid("Account"),
                    **PAGE,
                    "as_of": {"type": "string", "format": "date-time"},
                    "latest_only": {"type": "boolean", "default": True},
                    "asset_identifiers": {"type": "array", "items": {"type": "string"}},
                    **ORDERING,
                },
                required=("account_uid",),
            ),
            _operation_case(
                "holdings_snapshot",
                properties={
                    "account_uid": _uid("Account"),
                    "holdings_set_uid": _uid("holdings set"),
                },
                required=("account_uid", "holdings_set_uid"),
            ),
        ),
        execution_mode="parallel",
        handler=query_accounts,
    ),
    ProjectAgentTool(
        name="alpaca_manage_account",
        label="Manage Alpaca Account",
        description=(
            "Plan, create, update, refresh, or delete an Alpaca Account registration, or capture "
            "its holdings. Creation accepts Main Sequence Secret names only and always performs "
            "the connector's mandatory initial asset registration and holdings capture."
        ),
        parameters=_operations(
            *[
                _operation_case(
                    operation,
                    properties={
                        "request": _request(
                            AccountRegistrationRequest,
                            "Account registration containing Secret names, never credential values.",
                        )
                    },
                    required=("request",),
                )
                for operation in ("plan_create", "create")
            ],
            _operation_case(
                "update",
                properties={
                    "account_uid": _uid("Account"),
                    "request": _request(AccountUpdateRequest, "Mutable Account fields."),
                },
                required=("account_uid", "request"),
            ),
            _operation_case(
                "refresh",
                properties={"account_uid": _uid("Account")},
                required=("account_uid",),
            ),
            _operation_case(
                "capture_holdings",
                properties={"account_uid": _uid("Account")},
                required=("account_uid",),
            ),
            _operation_case(
                "delete",
                properties={"account_uid": _uid("Account"), "confirmation": _confirmation()},
                required=("account_uid", "confirmation"),
            ),
        ),
        execution_mode="sequential",
        handler=manage_account,
    ),
    ProjectAgentTool(
        name="alpaca_query_universes",
        label="Query Alpaca Universes",
        description=(
            "Read Universe sources, registered Universes, linked AssetCategory members, and "
            "component-extraction plans."
        ),
        parameters=_operations(
            _operation_case("list", properties={**PAGE, **SEARCH, **ORDERING}),
            _operation_case(
                "get",
                properties={"universe_uid": _uid("Universe")},
                required=("universe_uid",),
            ),
            _operation_case(
                "members",
                properties={
                    "universe_uid": _uid("Universe"),
                    **PAGE,
                    **SEARCH,
                    **ORDERING,
                },
                required=("universe_uid",),
            ),
            _operation_case(
                "list_sources",
                properties={**PAGE, **SEARCH, **ORDERING, "enabled": {"type": "boolean"}},
            ),
            _operation_case(
                "get_source",
                properties={"source_uid": _uid("UniverseSource")},
                required=("source_uid",),
            ),
            _operation_case(
                "preview_source",
                properties={"source_uid": _uid("UniverseSource"), **TIMEOUT},
                required=("source_uid",),
            ),
            _operation_case(
                "preview_extraction",
                properties={
                    "universe_uid": _uid("Universe"),
                    "account_uid": _uid("Account"),
                    **TIMEOUT,
                },
                required=("universe_uid", "account_uid"),
            ),
        ),
        execution_mode="parallel",
        handler=query_universes,
    ),
    ProjectAgentTool(
        name="alpaca_manage_universe",
        label="Manage Alpaca Universe",
        description=(
            "Create, update, activate, deactivate, delete, or extract one registered Universe, "
            "and manage its explicit extraction sources. Component extraction registers missing "
            "assets in bulk and publishes one weight observation."
        ),
        parameters=_operations(
            _operation_case(
                "create",
                properties={
                    "request": _request(AssetUniverseCreateRequest, "New Universe definition.")
                },
                required=("request",),
            ),
            _operation_case(
                "update",
                properties={
                    "universe_uid": _uid("Universe"),
                    "request": _request(AssetUniverseUpdateRequest, "Mutable Universe fields."),
                },
                required=("universe_uid", "request"),
            ),
            *[
                _operation_case(
                    operation,
                    properties={"universe_uid": _uid("Universe")},
                    required=("universe_uid",),
                )
                for operation in ("activate", "deactivate")
            ],
            _operation_case(
                "extract_components",
                properties={
                    "universe_uid": _uid("Universe"),
                    "account_uid": _uid("Account"),
                    **TIMEOUT,
                },
                required=("universe_uid", "account_uid"),
            ),
            _operation_case(
                "delete",
                properties={"universe_uid": _uid("Universe"), "confirmation": _confirmation()},
                required=("universe_uid", "confirmation"),
            ),
            _operation_case(
                "create_source",
                properties={
                    "request": _request(UniverseSourceCreateRequest, "New extraction source.")
                },
                required=("request",),
            ),
            _operation_case(
                "update_source",
                properties={
                    "source_uid": _uid("UniverseSource"),
                    "request": _request(
                        UniverseSourceUpdateRequest,
                        "Mutable extraction-source fields.",
                    ),
                },
                required=("source_uid", "request"),
            ),
            _operation_case(
                "delete_source",
                properties={"source_uid": _uid("UniverseSource"), "confirmation": _confirmation()},
                required=("source_uid", "confirmation"),
            ),
        ),
        execution_mode="sequential",
        handler=manage_universe,
    ),
    ProjectAgentTool(
        name="alpaca_query_bar_configurations",
        label="Query Alpaca Bar Configurations",
        description=(
            "Read stored bar configurations, resolve their current asset scope, and query migrated "
            "Alpaca bar datasets and bounded observations."
        ),
        parameters=_operations(
            _operation_case(
                "list",
                properties={
                    **PAGE,
                    **SEARCH,
                    **ORDERING,
                    "enabled": {"type": "boolean"},
                    "asset_source": {
                        "type": "string",
                        "enum": ["assets", "universe", "account_holdings"],
                    },
                },
            ),
            _operation_case(
                "get",
                properties={"configuration_uid": _uid("bar configuration")},
                required=("configuration_uid",),
            ),
            _operation_case(
                "resolve",
                properties={
                    "configuration_uid": _uid("bar configuration"),
                    "hash_namespace": {"type": "string", "minLength": 1},
                },
                required=("configuration_uid",),
            ),
            _operation_case("datasets", properties={**PAGE, **ORDERING}),
            _operation_case(
                "dataset",
                properties={"dataset_uid": _uid("market-data dataset")},
                required=("dataset_uid",),
            ),
            _operation_case(
                "observations",
                properties={
                    "dataset_uid": _uid("market-data dataset"),
                    **_page_properties(maximum=1_000),
                    "asset_uids": {"type": "array", "items": {"type": "string"}},
                    "asset_identifiers": {"type": "array", "items": {"type": "string"}},
                    "start": {"type": "string", "format": "date-time"},
                    "end": {"type": "string", "format": "date-time"},
                    **ORDERING,
                },
                required=("dataset_uid",),
            ),
        ),
        execution_mode="parallel",
        handler=query_bar_configurations,
    ),
    ProjectAgentTool(
        name="alpaca_manage_bar_configuration",
        label="Manage Alpaca Bar Configuration",
        description=(
            "Create, update, delete, or submit the dedicated Job for a stored Alpaca bar "
            "configuration. The asset scope and dataset are resolved from the configuration."
        ),
        parameters=_operations(
            _operation_case(
                "create",
                properties={
                    "request": _request(BarConfigurationCreateRequest, "New bar configuration.")
                },
                required=("request",),
            ),
            _operation_case(
                "update",
                properties={
                    "configuration_uid": _uid("bar configuration"),
                    "request": _request(
                        BarConfigurationUpdateRequest,
                        "Mutable bar-configuration fields.",
                    ),
                },
                required=("configuration_uid", "request"),
            ),
            _operation_case(
                "run",
                properties={"configuration_uid": _uid("bar configuration")},
                required=("configuration_uid",),
            ),
            _operation_case(
                "delete",
                properties={
                    "configuration_uid": _uid("bar configuration"),
                    "confirmation": _confirmation(),
                },
                required=("configuration_uid", "confirmation"),
            ),
        ),
        execution_mode="sequential",
        handler=manage_bar_configuration,
    ),
    ProjectAgentTool(
        name="alpaca_query_etf_signals",
        label="Query ETF Weight Signals",
        description=(
            "Read Universe-backed ETF signal configurations, their latest 100 observed weight "
            "frames, and dedicated JobRun history."
        ),
        parameters=_operations(
            _operation_case(
                "list",
                properties={
                    **PAGE,
                    **SEARCH,
                    **ORDERING,
                    "enabled": {"type": "boolean"},
                    "lifecycle_state": {"type": "string"},
                },
            ),
            _operation_case(
                "get",
                properties={"configuration_uid": _uid("ETF signal configuration")},
                required=("configuration_uid",),
            ),
            _operation_case(
                "observations",
                properties={
                    "configuration_uid": _uid("ETF signal configuration"),
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 100},
                },
                required=("configuration_uid",),
            ),
            _operation_case(
                "runs",
                properties={
                    "configuration_uid": _uid("ETF signal configuration"),
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
                },
                required=("configuration_uid",),
            ),
        ),
        execution_mode="parallel",
        handler=query_etf_signals,
    ),
    ProjectAgentTool(
        name="alpaca_manage_etf_signal",
        label="Manage ETF Weight Signal",
        description=(
            "Create, update, delete, or run a Universe-backed ETF signal configuration and its "
            "dedicated scheduled Main Sequence Job."
        ),
        parameters=_operations(
            _operation_case(
                "create",
                properties={
                    "request": _request(
                        SignalJobConfigurationCreateRequest,
                        "ETF signal and dedicated Job configuration.",
                    )
                },
                required=("request",),
            ),
            _operation_case(
                "update",
                properties={
                    "configuration_uid": _uid("ETF signal configuration"),
                    "request": _request(
                        SignalJobConfigurationUpdateRequest,
                        "Mutable ETF signal and Job fields.",
                    ),
                },
                required=("configuration_uid", "request"),
            ),
            _operation_case(
                "run",
                properties={"configuration_uid": _uid("ETF signal configuration")},
                required=("configuration_uid",),
            ),
            _operation_case(
                "delete",
                properties={
                    "configuration_uid": _uid("ETF signal configuration"),
                    "confirmation": _confirmation(),
                },
                required=("configuration_uid", "confirmation"),
            ),
        ),
        execution_mode="sequential",
        handler=manage_etf_signal,
    ),
    ProjectAgentTool(
        name="alpaca_query_rebalance_configurations",
        label="Query Rebalance Configurations",
        description="List or retrieve stored calendar-event portfolio rebalance configurations.",
        parameters=_operations(
            _operation_case("list", properties={**PAGE, **SEARCH, **ORDERING}),
            _operation_case(
                "get",
                properties={"configuration_uid": _uid("rebalance configuration")},
                required=("configuration_uid",),
            ),
        ),
        execution_mode="parallel",
        handler=query_rebalance_configurations,
    ),
    ProjectAgentTool(
        name="alpaca_manage_rebalance_configuration",
        label="Manage Rebalance Configuration",
        description="Create, update, or delete a calendar-event portfolio rebalance configuration.",
        parameters=_operations(
            _operation_case(
                "create",
                properties={
                    "request": _request(
                        PortfolioRebalanceConfigurationCreateRequest,
                        "New calendar-event rebalance configuration.",
                    )
                },
                required=("request",),
            ),
            _operation_case(
                "update",
                properties={
                    "configuration_uid": _uid("rebalance configuration"),
                    "request": _request(
                        PortfolioRebalanceConfigurationUpdateRequest,
                        "Mutable rebalance fields.",
                    ),
                },
                required=("configuration_uid", "request"),
            ),
            _operation_case(
                "delete",
                properties={
                    "configuration_uid": _uid("rebalance configuration"),
                    "confirmation": _confirmation(),
                },
                required=("configuration_uid", "confirmation"),
            ),
        ),
        execution_mode="sequential",
        handler=manage_rebalance_configuration,
    ),
    ProjectAgentTool(
        name="alpaca_query_etf_portfolios",
        label="Query ETF Portfolios",
        description=(
            "Read analytical ETF portfolio configurations, linked signal/bars/rebalance details, "
            "historical performance, statistics, and dedicated JobRun history."
        ),
        parameters=_operations(
            _operation_case(
                "list",
                properties={
                    **PAGE,
                    **SEARCH,
                    **ORDERING,
                    "signal_configuration_uid": _uid("ETF signal configuration"),
                    "bars_configuration_uid": _uid("bar configuration"),
                },
            ),
            _operation_case(
                "get",
                properties={
                    "configuration_uid": _uid("ETF portfolio configuration"),
                    "observation_limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 5_000,
                        "default": 2_500,
                    },
                },
                required=("configuration_uid",),
            ),
            _operation_case(
                "runs",
                properties={
                    "configuration_uid": _uid("ETF portfolio configuration"),
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
                },
                required=("configuration_uid",),
            ),
        ),
        execution_mode="parallel",
        handler=query_etf_portfolios,
    ),
    ProjectAgentTool(
        name="alpaca_manage_etf_portfolio",
        label="Manage ETF Portfolio",
        description=(
            "Create, update, delete, or run an analytical ETF portfolio configuration and its "
            "dedicated scheduled Main Sequence Job."
        ),
        parameters=_operations(
            _operation_case(
                "create",
                properties={
                    "request": _request(
                        PortfolioConfigurationCreateRequest,
                        "ETF portfolio, data, rebalance, and dedicated Job configuration.",
                    )
                },
                required=("request",),
            ),
            _operation_case(
                "update",
                properties={
                    "configuration_uid": _uid("ETF portfolio configuration"),
                    "request": _request(
                        PortfolioConfigurationUpdateRequest,
                        "Mutable ETF portfolio and Job fields.",
                    ),
                },
                required=("configuration_uid", "request"),
            ),
            _operation_case(
                "run",
                properties={"configuration_uid": _uid("ETF portfolio configuration")},
                required=("configuration_uid",),
            ),
            _operation_case(
                "delete",
                properties={
                    "configuration_uid": _uid("ETF portfolio configuration"),
                    "confirmation": _confirmation(),
                },
                required=("configuration_uid", "confirmation"),
            ),
        ),
        execution_mode="sequential",
        handler=manage_etf_portfolio,
    ),
    ProjectAgentTool(
        name="alpaca_get_job_run_status",
        label="Get Alpaca JobRun Status",
        description=(
            "Read one branch-owned Alpaca bars, ETF signal, or ETF portfolio JobRun status and "
            "its sanitized failure summary."
        ),
        parameters={
            "type": "object",
            "properties": {"job_run_uid": _uid("JobRun")},
            "required": ["job_run_uid"],
            "additionalProperties": False,
        },
        execution_mode="parallel",
        handler=get_job_run_status,
    ),
)


PROJECT_AGENT_TOOL_NAMES = tuple(tool.name for tool in PROJECT_AGENT_TOOLS)


__all__ = [
    "PROJECT_AGENT_TOOLS",
    "PROJECT_AGENT_TOOL_NAMES",
    "ProjectAgentTool",
]
