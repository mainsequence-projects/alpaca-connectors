"""API shaping for signal definitions: the one kind of signal this connector makes (ADR 0028).

A definition belongs to one account; only the account's owner creates it, changes it, gives it
weights or deletes it, and only the account's portfolios follow it. An external person reaches
only their own accounts' definitions (ADR 0019).
"""

from __future__ import annotations

from typing import Any

from alpaca_connectors.account.ownership import AccountOwner
from alpaca_connectors.portfolios.configurations import portfolio_counts_by_definition
from alpaca_connectors.portfolios.provided_weights import publish_definition_weights
from alpaca_connectors.portfolios.signal_definitions import (
    SignalDefinition,
    create_signal_definition,
    definition_signal_uid,
    delete_signal_definition,
    get_signal_definition,
    list_signal_definitions,
    update_signal_definition,
)

from ..errors import not_found
from ..schemas import (
    PortfolioWeightsRequest,
    PublishedWeightsResponse,
    SignalDefinitionCreateRequest,
    SignalDefinitionResponse,
    SignalDefinitionUpdateRequest,
    SignalObservationsResponse,
)
from .accounts import require_account_access, require_account_owned_by
from .common import collection_response


def _responses(rows: list[SignalDefinition]) -> list[SignalDefinitionResponse]:
    counts = portfolio_counts_by_definition([row.uid for row in rows])
    return [
        SignalDefinitionResponse.model_validate(
            {
                **row.model_dump(mode="json", exclude={"signal_key"}),
                "signal_uid": definition_signal_uid(row),
                "portfolio_count": counts.get(str(row.uid), 0),
            }
        )
        for row in rows
    ]


def _definition(definition_uid: str, owner: AccountOwner) -> SignalDefinition:
    """The definition, answered 404 to an external person who does not own its account."""
    definition = get_signal_definition(definition_uid)
    if definition is None:
        raise LookupError(f"Signal definition {definition_uid!s} does not exist.")
    require_account_access(str(definition.account_uid), owner)
    return definition


def list_definitions(
    *,
    limit: int,
    offset: int,
    search: str | None,
    account_uid: str | None,
    owner: AccountOwner,
    uids: list[str] | None = None,
):
    if owner.is_external:
        # An external person names their own account (ADR 0019 section 6).
        if account_uid is None:
            raise not_found("Name one of your accounts with account_uid.")
        require_account_access(account_uid, owner)
    rows, total = list_signal_definitions(
        account_uids=[account_uid] if account_uid is not None else None,
        limit=limit,
        offset=offset,
        search=search,
        uids=uids,
    )
    return collection_response(items=_responses(rows), total=total, limit=limit, offset=offset)


def get_definition(definition_uid: str, *, owner: AccountOwner) -> SignalDefinitionResponse:
    return _responses([_definition(definition_uid, owner)])[0]


def create_definition(
    request: SignalDefinitionCreateRequest,
    *,
    owner: AccountOwner,
) -> SignalDefinitionResponse:
    require_account_owned_by(request.account_uid, owner)
    return _responses([create_signal_definition(**request.model_dump())])[0]


def update_definition(
    definition_uid: str,
    request: SignalDefinitionUpdateRequest,
    *,
    owner: AccountOwner,
) -> SignalDefinitionResponse:
    definition = _definition(definition_uid, owner)
    require_account_owned_by(definition.account_uid, owner)
    values: dict[str, Any] = request.model_dump(exclude_unset=True)
    return _responses([update_signal_definition(definition.uid, **values)])[0]


def delete_definition(definition_uid: str, *, owner: AccountOwner) -> dict[str, Any]:
    definition = _definition(definition_uid, owner)
    require_account_owned_by(definition.account_uid, owner)
    return delete_signal_definition(definition.uid)


def give_definition_weights(
    definition_uid: str,
    request: PortfolioWeightsRequest,
    *,
    owner: AccountOwner,
) -> PublishedWeightsResponse:
    """Publish one or more observations to the definition's signal; only the owner may."""
    definition = _definition(definition_uid, owner)
    require_account_owned_by(definition.account_uid, owner)
    published = publish_definition_weights(
        definition, [observation.model_dump() for observation in request.observations]
    )
    return PublishedWeightsResponse(
        signal_definition_uid=str(definition.uid),
        signal_uid=published.signal_uid,
        observation_count=published.observation_count,
        asset_count=published.asset_count,
        first_observed_at=published.first_observed_at,
        last_observed_at=published.last_observed_at,
    )


def definition_weights(
    definition_uid: str,
    *,
    limit: int,
    owner: AccountOwner,
) -> SignalObservationsResponse:
    """The stored observations of the definition's signal, newest first."""
    from alpaca_connectors.portfolios import read_signal_observation_matrix

    from .portfolio_configurations import observations_response

    definition = _definition(definition_uid, owner)
    matrix = read_signal_observation_matrix(
        signal_uid=definition_signal_uid(definition), observation_limit=limit
    )
    response = observations_response(None, matrix)
    return response.model_copy(update={"signal_definition_uid": str(definition.uid)})


__all__ = [
    "create_definition",
    "definition_weights",
    "delete_definition",
    "get_definition",
    "give_definition_weights",
    "list_definitions",
    "update_definition",
]
