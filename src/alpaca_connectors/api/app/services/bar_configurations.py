"""API shaping for durable Alpaca bar configurations and update actions.

A bars feed belongs to its account and a portfolio's own configuration to its portfolio
(ADR 0028, decision 4). Only the account's owner creates or changes a feed, and only the
portfolio's owner changes the portfolio's own configuration, which is created and deleted with
the portfolio.
"""

from __future__ import annotations

from typing import Any

from alpaca_connectors.account.ownership import AccountOwner
from alpaca_connectors.market_data import (
    create_bar_configuration,
    delete_bar_configuration,
    get_bar_configuration,
    list_bar_configurations,
    resolve_market_data_update,
    update_bar_configuration,
)
from alpaca_connectors.operations import launch_alpaca_bars_update

from ..schemas import (
    BarConfigurationCreateRequest,
    BarConfigurationResolutionResponse,
    BarConfigurationResolveRequest,
    BarConfigurationResponse,
    BarConfigurationUpdateAcceptedResponse,
    BarConfigurationUpdateRequest,
)
from .accounts import require_account_owned_by
from .common import collection_response


def _configuration_response(row: object) -> BarConfigurationResponse:
    """Project the reusable typed row into the API-owned response model."""

    if not hasattr(row, "model_dump"):
        raise TypeError("Bar configuration rows must provide model_dump().")
    return BarConfigurationResponse.model_validate(row.model_dump(mode="json"))


def list_configurations(
    *,
    limit: int,
    offset: int,
    search: str | None,
    enabled: bool | None,
    asset_source: str | None,
    ordering: str,
    scheduled: bool | None = None,
    portfolio_owned: bool | None = None,
):
    items, total = list_bar_configurations(
        limit=limit,
        offset=offset,
        search=search,
        enabled=enabled,
        scheduled=scheduled,
        asset_source=asset_source,
        portfolio_owned=portfolio_owned,
        ordering=ordering,
    )
    return collection_response(items=items, total=total, limit=limit, offset=offset)


def _require_owner_of_configuration(
    configuration_uid: str,
    account_uid: Any,
    owner: AccountOwner,
) -> None:
    """Only the owner of what the configuration belongs to changes it.

    A feed belongs to its account, the new one too when the change moves it; a portfolio's own
    configuration to its portfolio, whose owner owns the portfolio's account.
    """
    from alpaca_connectors.portfolios.configurations import get_portfolio_configuration
    from alpaca_connectors.portfolios.provided_weights import require_portfolio_owner

    current = get_bar_configuration(configuration_uid)
    if current is None:
        return  # The configuration service reports the missing configuration.
    if current.portfolio_configuration_uid is not None:
        portfolio = get_portfolio_configuration(current.portfolio_configuration_uid)
        if portfolio is not None:
            require_portfolio_owner(portfolio, owner)
        return
    require_account_owned_by(current.account_uid, owner)
    if account_uid is not None and str(account_uid) != str(current.account_uid):
        require_account_owned_by(account_uid, owner)


def create_configuration(
    request: BarConfigurationCreateRequest,
    *,
    owner: AccountOwner,
) -> BarConfigurationResponse:
    """Create a feed of an account its owner owns (ADR 0028, decisions 3 and 4)."""
    require_account_owned_by(request.account_uid, owner)
    return _configuration_response(create_bar_configuration(**request.model_dump()))


def get_configuration(configuration_uid: str) -> BarConfigurationResponse | None:
    row = get_bar_configuration(configuration_uid)
    return _configuration_response(row) if row else None


def update_configuration(
    configuration_uid: str,
    request: BarConfigurationUpdateRequest,
    *,
    owner: AccountOwner,
) -> BarConfigurationResponse:
    """Update a configuration; anyone but the owner of what it belongs to is refused."""
    _require_owner_of_configuration(configuration_uid, request.account_uid, owner)
    return _configuration_response(
        update_bar_configuration(
            configuration_uid,
            **request.model_dump(exclude_unset=True),
        )
    )


def delete_configuration(configuration_uid: str, *, owner: AccountOwner) -> dict:
    """Delete a feed for its account's owner; a portfolio's own goes with its portfolio."""
    _require_owner_of_configuration(configuration_uid, None, owner)
    return delete_bar_configuration(configuration_uid)


def resolve_configuration(
    configuration_uid: str,
    request: BarConfigurationResolveRequest,
) -> BarConfigurationResolutionResponse:
    return BarConfigurationResolutionResponse.model_validate(
        resolve_market_data_update(
            configuration_uid=configuration_uid,
            hash_namespace=request.hash_namespace,
        )
    )


def submit_configuration_update(
    configuration_uid: str,
) -> BarConfigurationUpdateAcceptedResponse:
    submission = launch_alpaca_bars_update(configuration_uid)
    return BarConfigurationUpdateAcceptedResponse(
        configuration_uid=submission.configuration_uid,
        job_uid=submission.job_uid,
        job_run_uid=submission.job_run_uid,
        status=submission.status,
        status_url=f"/v1/operations/job-runs/{submission.job_run_uid}",
    )


__all__ = [
    "create_configuration",
    "delete_configuration",
    "get_configuration",
    "list_configurations",
    "resolve_configuration",
    "submit_configuration_update",
    "update_configuration",
]
