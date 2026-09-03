"""Assets application services."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from alpaca.common.exceptions import APIError as AlpacaAPIError
from requests import ConnectionError as RequestsConnectionError
from requests import HTTPError, JSONDecodeError
from requests import Timeout as RequestsTimeout

from src.assets import (
    build_alpaca_us_equity_registration_plan,
    get_asset,
    list_assets,
    register_alpaca_us_equity_assets,
    resolve_alpaca_us_equity_registration_plan,
)
from src.operations import (
    complete_asset_registration_operation,
    create_asset_registration_operation,
    fail_asset_registration_operation,
    get_asset_registration_operation,
    set_asset_registration_step,
)
from src.universes import EtfExpansionRequest, expand_etf_seed_symbols

from ..errors import api_http_error
from ..schemas import (
    AssetRegistrationDiscoveryResponse,
    AssetRegistrationExecuteResponse,
    AssetRegistrationOperationResponse,
    AssetRegistrationRequest,
)
from .common import collection_response, serialize_asset_mapping

ProgressCallback = Callable[[str, str, str | None], None]


def list_asset_resources(*, limit: int, offset: int, search: str | None, ordering: str):
    items, total = list_assets(limit=limit, offset=offset, search=search, ordering=ordering)
    return collection_response(items=items, total=total, limit=limit, offset=offset)


def get_asset_resource(asset_uid: str):
    return get_asset(asset_uid)


def _resolve_asset_registration_symbols(
    request: AssetRegistrationRequest,
) -> tuple[list[str] | None, Any | None]:
    if request.symbols:
        return request.symbols, None

    expansion_result = expand_etf_seed_symbols(
        EtfExpansionRequest(
            seed_tickers=request.seed_tickers or [],
            component_provider=request.component_provider or "",
            timeout=request.timeout,
        )
    )
    return expansion_result.symbols_for_registration, expansion_result


def _asset_registration_plan_summary(plan: Any, expansion_result: Any | None) -> dict[str, Any]:
    summary = plan.summary()
    if expansion_result is None:
        return summary

    return {
        **summary,
        "seed_symbols": expansion_result.universe.seed_symbols,
        "component_provider": expansion_result.component_provider,
        "expanded_candidate_symbols": expansion_result.universe.expanded_symbols,
        "expanded_component_symbols_by_seed": expansion_result.universe.component_symbols_by_seed,
        "unsupported_seed_symbols": expansion_result.universe.unsupported_seed_symbols,
    }


def _emit_progress(
    progress: ProgressCallback | None,
    step_key: str,
    status: str,
    message: str | None = None,
) -> None:
    if progress is not None:
        progress(step_key, status, message)


def _prepare_asset_registration(
    request: AssetRegistrationRequest,
    *,
    progress: ProgressCallback | None,
) -> tuple[Any, Any, Any]:
    _emit_progress(progress, "prepare_scope", "running", "Preparing registration inputs.")
    symbols, expansion_result = _resolve_asset_registration_symbols(request)
    scope_message = (
        f"Expanded {len(request.seed_tickers or [])} ETF seed(s) into {len(symbols or [])} symbol(s)."
        if expansion_result is not None
        else f"Prepared {len(symbols or [])} exact symbol(s)."
    )
    _emit_progress(progress, "prepare_scope", "succeeded", scope_message)

    plan = build_alpaca_us_equity_registration_plan(
        symbols=symbols,
        include_non_tradable=request.include_non_tradable,
        timeout=request.timeout,
        progress=progress,
    )

    _emit_progress(
        progress,
        "check_existing_assets",
        "running",
        "Checking resolved FIGIs against registered Main Sequence assets.",
    )
    resolution = resolve_alpaca_us_equity_registration_plan(plan, timeout=request.timeout)
    _emit_progress(
        progress,
        "check_existing_assets",
        "succeeded",
        (
            f"Found {len(resolution.existing_assets_by_symbol)} existing asset(s) and "
            f"{len(resolution.missing_matches)} asset(s) eligible for registration."
        ),
    )
    return plan, resolution, expansion_result


def build_asset_registration_discovery(
    request: AssetRegistrationRequest,
    *,
    progress: ProgressCallback | None = None,
) -> AssetRegistrationDiscoveryResponse:
    plan, resolution, expansion_result = _prepare_asset_registration(
        request,
        progress=progress,
    )
    _emit_progress(progress, "finalize_plan", "running", "Building the registration plan result.")
    result = AssetRegistrationDiscoveryResponse(
        request=request,
        plan_summary=_asset_registration_plan_summary(plan, expansion_result),
        resolution_summary=resolution.summary(),
        can_register=not bool(plan.unresolved_symbols or plan.missing_symbols_from_alpaca),
        unresolved_symbols=list(plan.unresolved_symbols),
        missing_symbols_from_alpaca=list(plan.missing_symbols_from_alpaca),
        missing_symbols_to_register=sorted(match.symbol for match in resolution.missing_matches),
        warnings_by_symbol=dict(sorted(plan.warnings_by_symbol.items())),
    )
    _emit_progress(
        progress,
        "finalize_plan",
        "succeeded",
        "Registration plan is ready." if result.can_register else "Registration plan has blockers.",
    )
    return result


def execute_asset_registration(
    request: AssetRegistrationRequest,
    *,
    progress: ProgressCallback | None = None,
) -> AssetRegistrationExecuteResponse:
    plan, resolution, expansion_result = _prepare_asset_registration(
        request,
        progress=progress,
    )
    _emit_progress(
        progress,
        "register_assets",
        "running",
        f"Registering {len(resolution.missing_matches)} missing asset(s).",
    )
    registration_result = register_alpaca_us_equity_assets(
        registration_resolution=resolution,
        timeout=request.timeout,
    )
    _emit_progress(
        progress,
        "register_assets",
        "succeeded",
        f"Registered {len(registration_result['created_assets'])} new asset(s).",
    )
    _emit_progress(
        progress,
        "finalize_result",
        "running",
        "Building the registration execution result.",
    )
    result = AssetRegistrationExecuteResponse(
        request=request,
        plan_summary=_asset_registration_plan_summary(plan, expansion_result),
        resolution_summary=resolution.summary(),
        assets_by_symbol=serialize_asset_mapping(registration_result["assets"]),
        existing_asset_uids_by_symbol=serialize_asset_mapping(
            registration_result["existing_assets"]
        ),
        created_asset_uids_by_symbol=serialize_asset_mapping(registration_result["created_assets"]),
        unresolved_symbols=list(registration_result["unresolved_symbols"]),
        not_registered_missing_figi_symbols=list(
            registration_result["not_registered_missing_figi_symbols"]
        ),
        not_registered_missing_alpaca_symbols=list(
            registration_result["not_registered_missing_alpaca_symbols"]
        ),
        warnings_by_symbol=dict(sorted(registration_result["warnings_by_symbol"].items())),
    )
    _emit_progress(
        progress,
        "finalize_result",
        "succeeded",
        "Registration execution is complete.",
    )
    return result


def _operation_response(operation: Any) -> AssetRegistrationOperationResponse:
    return AssetRegistrationOperationResponse(
        operation_uid=str(operation.uid),
        action=operation.action,
        status=operation.status,
        current_step=operation.current_step,
        steps=operation.steps,
        request=operation.request,
        result=operation.result,
        error=operation.error,
        created_at=operation.created_at,
        started_at=operation.started_at,
        updated_at=operation.updated_at,
        completed_at=operation.completed_at,
    )


def start_asset_registration_operation(
    *,
    action: str,
    request: AssetRegistrationRequest,
    owner_uid: str | None,
) -> AssetRegistrationOperationResponse:
    operation = create_asset_registration_operation(
        action=action,
        request=request.model_dump(mode="json"),
        owner_uid=owner_uid,
    )
    return _operation_response(operation)


def get_asset_registration_operation_status(
    operation_uid: str,
    *,
    owner_uid: str | None,
) -> AssetRegistrationOperationResponse:
    operation = get_asset_registration_operation(operation_uid)
    if operation is None or operation.owner_uid != owner_uid:
        raise LookupError(f"Asset registration operation {operation_uid} does not exist.")
    return _operation_response(operation)


def _exception_chain(exc: Exception) -> list[BaseException]:
    chain: list[BaseException] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def _asset_registration_public_error(
    exc: Exception,
    *,
    step_key: str,
    timeout: float,
) -> dict[str, Any]:
    chain = _exception_chain(exc)
    exception_names = {type(item).__name__ for item in chain}
    message = str(exc)

    if step_key == "load_alpaca_assets":
        if message.startswith("Failed to retrieve MainSequence secret"):
            if "NameResolutionError" in exception_names or "gaierror" in exception_names:
                return {
                    "code": "credential_service_dns_failure",
                    "message": (
                        "DNS could not resolve the configured Main Sequence backend while "
                        "retrieving the Alpaca credential secret. OpenFIGI was not called."
                    ),
                    "retryable": True,
                }
            return {
                "code": "credential_lookup_failed",
                "message": (
                    "Main Sequence could not retrieve the configured Alpaca credential secret. "
                    "OpenFIGI was not called. Check backend connectivity and secret access."
                ),
                "retryable": True,
            }
        if message.startswith("Missing Alpaca"):
            return {
                "code": "alpaca_credentials_missing",
                "message": (
                    "Alpaca credentials are not configured. Set the ALPACA_API_KEY and "
                    "ALPACA_SECRET_KEY secrets before retrying. OpenFIGI was not called."
                ),
                "retryable": False,
            }
        if isinstance(exc, AlpacaAPIError):
            status_code = getattr(exc, "status_code", None)
            if status_code in {401, 403}:
                detail = "Alpaca rejected the configured credentials. OpenFIGI was not called."
            elif status_code == 429:
                detail = "Alpaca rate-limited the asset-catalog request. OpenFIGI was not called."
            else:
                suffix = f" (HTTP {status_code})" if status_code else ""
                detail = (
                    f"Alpaca rejected the asset-catalog request{suffix}. OpenFIGI was not called."
                )
            return {"code": "alpaca_catalog_failed", "message": detail, "retryable": True}
        return {
            "code": "alpaca_catalog_unavailable",
            "message": (
                "The Alpaca asset catalog could not be loaded, so OpenFIGI was not called. "
                "Check Alpaca connectivity and credentials."
            ),
            "retryable": True,
        }

    if step_key == "resolve_openfigi_identities":
        if any(isinstance(item, RequestsTimeout) for item in chain):
            return {
                "code": "openfigi_timeout",
                "message": f"OpenFIGI did not respond within the configured {timeout:g}-second timeout.",
                "retryable": True,
            }
        http_error = next((item for item in chain if isinstance(item, HTTPError)), None)
        if http_error is not None:
            status_code = (
                http_error.response.status_code if http_error.response is not None else None
            )
            if status_code in {401, 403}:
                detail = "OpenFIGI rejected the configured API key."
            elif status_code == 429:
                detail = "OpenFIGI rate-limited the mapping request after its retry window."
            else:
                suffix = f" (HTTP {status_code})" if status_code else ""
                detail = f"OpenFIGI rejected the mapping request{suffix}."
            return {"code": "openfigi_request_failed", "message": detail, "retryable": True}
        if any(isinstance(item, JSONDecodeError) for item in chain):
            return {
                "code": "openfigi_invalid_response",
                "message": "OpenFIGI returned a response that was not valid JSON.",
                "retryable": True,
            }
        if any(isinstance(item, RequestsConnectionError) for item in chain):
            detail = (
                "DNS could not resolve api.openfigi.com."
                if "NameResolutionError" in exception_names or "gaierror" in exception_names
                else "The API could not connect to api.openfigi.com."
            )
            return {"code": "openfigi_unavailable", "message": detail, "retryable": True}
        return {
            "code": "openfigi_resolution_failed",
            "message": "OpenFIGI identity resolution failed unexpectedly.",
            "retryable": True,
        }

    if step_key == "check_existing_assets":
        return {
            "code": "mainsequence_asset_lookup_failed",
            "message": "Main Sequence could not check the resolved FIGIs against registered assets.",
            "retryable": True,
        }
    if step_key == "register_assets":
        return {
            "code": "mainsequence_asset_registration_failed",
            "message": "Main Sequence could not register the resolved missing assets.",
            "retryable": True,
        }

    public_error = api_http_error(exc).detail
    return (
        dict(public_error)
        if isinstance(public_error, dict)
        else {
            "code": "registration_failed",
            "message": str(public_error),
            "retryable": False,
        }
    )


def run_asset_registration_operation(
    operation_uid: str,
    *,
    action: str,
    request: AssetRegistrationRequest,
) -> None:
    current_step = "prepare_scope"

    def progress(step_key: str, status: str, message: str | None) -> None:
        nonlocal current_step
        current_step = step_key
        set_asset_registration_step(
            operation_uid,
            step_key=step_key,
            status=status,
            message=message,
        )

    try:
        if action == "plan":
            result = build_asset_registration_discovery(request, progress=progress)
        else:
            result = execute_asset_registration(request, progress=progress)
    except Exception as exc:
        public_error = _asset_registration_public_error(
            exc,
            step_key=current_step,
            timeout=request.timeout,
        )
        fail_asset_registration_operation(
            operation_uid,
            step_key=current_step,
            error=public_error,
        )
        return

    complete_asset_registration_operation(
        operation_uid,
        result=result.model_dump(mode="json"),
    )
