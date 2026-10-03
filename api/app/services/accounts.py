"""API shaping for reusable account services."""

from __future__ import annotations

import mainsequence.client as msc
from src.account.services import (
    get_account_registration,
    list_account_registrations,
    plan_alpaca_account,
    refresh_alpaca_account,
    register_alpaca_account,
    remove_account_registration,
    update_account_registration,
)

from ..schemas import (
    AccountRegistrationRequest,
    AccountResponse,
    AccountSecretNameRegistrationRequest,
    AccountSecretNameUpdateRequest,
    AccountUpdateRequest,
    ManagedAlpacaCredentials,
)
from .common import collection_response

RegistrationRequest = AccountRegistrationRequest | AccountSecretNameRegistrationRequest
UpdateRequest = AccountUpdateRequest | AccountSecretNameUpdateRequest


def _credential_input(request: RegistrationRequest):
    """Translate a registration request into the domain credential input."""
    from src.account.credentials import AlpacaSecretNames, submitted_alpaca_credentials

    credentials = (
        request.credentials if isinstance(request, AccountRegistrationRequest) else request
    )
    if isinstance(credentials, ManagedAlpacaCredentials):
        return submitted_alpaca_credentials(
            api_key=credentials.api_key.get_secret_value(),
            secret_key=credentials.secret_key.get_secret_value(),
        )
    return AlpacaSecretNames(
        api_key_secret_name=credentials.api_key_secret_name,
        secret_key_secret_name=credentials.secret_key_secret_name,
    )


def list_accounts(
    *,
    limit: int,
    offset: int,
    search: str | None,
    active: bool | None,
    is_paper: bool | None,
    ordering: str,
):
    items, total = list_account_registrations(
        limit=limit,
        offset=offset,
        search=search,
        active=active,
        is_paper=is_paper,
        ordering=ordering,
    )
    safe_items = [AccountResponse.model_validate(item) for item in items]
    return collection_response(items=safe_items, total=total, limit=limit, offset=offset)


def list_secret_references(*, limit: int, offset: int, search: str | None):
    """Return visible Secret names without ever serializing their values."""
    search_term = (search or "").strip().casefold()
    names = sorted(
        {
            secret.name.strip()
            for secret in msc.Secret.filter()
            if secret.name.strip() and (not search_term or search_term in secret.name.casefold())
        }
    )
    return collection_response(
        items=[{"name": name} for name in names[offset : offset + limit]],
        total=len(names),
        limit=limit,
        offset=offset,
    )


def preflight_account_registration(request: RegistrationRequest) -> dict:
    return plan_alpaca_account(
        credentials=_credential_input(request),
        paper=request.environment == "paper",
    )


def create_account_registration(
    request: RegistrationRequest,
    *,
    actor_user_uid: str | None = None,
) -> AccountResponse:
    result = register_alpaca_account(
        credentials=_credential_input(request),
        paper=request.environment == "paper",
        account_name=request.account_name,
        actor_user_uid=actor_user_uid,
    )
    account = get_account_registration(result.account_uid)
    if account is None:
        raise RuntimeError("Registered account could not be read back.")
    return AccountResponse.model_validate(account)


def get_account(account_uid: str) -> AccountResponse | None:
    row = get_account_registration(account_uid)
    return AccountResponse.model_validate(row) if row else None


def update_account(
    account_uid: str,
    request: UpdateRequest,
    *,
    actor_user_uid: str | None = None,
) -> AccountResponse:
    from src.account.credentials import submitted_alpaca_credentials

    changes = {
        "account_name": request.account_name,
        "account_is_active": request.account_is_active,
    }
    if isinstance(request, AccountSecretNameUpdateRequest):
        changes.update(
            api_key_secret_name=request.api_key_secret_name,
            secret_key_secret_name=request.secret_key_secret_name,
        )
    elif isinstance(request.credentials, ManagedAlpacaCredentials):
        changes["credential_values"] = submitted_alpaca_credentials(
            api_key=request.credentials.api_key.get_secret_value(),
            secret_key=request.credentials.secret_key.get_secret_value(),
        )
    elif request.credentials is not None:
        changes.update(
            api_key_secret_name=request.credentials.api_key_secret_name,
            secret_key_secret_name=request.credentials.secret_key_secret_name,
        )
    row = update_account_registration(
        account_uid,
        actor_user_uid=actor_user_uid,
        **{key: value for key, value in changes.items() if value is not None},
    )
    return AccountResponse.model_validate(row)


def refresh_account(account_uid: str) -> AccountResponse:
    return AccountResponse.model_validate(refresh_alpaca_account(account_uid))


def remove_account(account_uid: str) -> dict:
    return remove_account_registration(account_uid)


def account_delete_blockers(account_uid: str) -> list[str]:
    from src.operations.signal_job_configurations import signal_job_configurations_for_account

    return [
        f"Account {account_uid} is referenced by signal Job configuration "
        f"{configuration.name} ({configuration.uid!s})."
        for configuration in signal_job_configurations_for_account(account_uid)
    ]


__all__ = [
    "create_account_registration",
    "account_delete_blockers",
    "get_account",
    "list_accounts",
    "list_secret_references",
    "preflight_account_registration",
    "refresh_account",
    "remove_account",
    "update_account",
]
