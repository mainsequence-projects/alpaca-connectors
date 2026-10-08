"""Account key requests: the person enters Alpaca keys on the site for an Agent's request (ADR 0015)."""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from alpaca_connectors.account import key_requests
from alpaca_connectors.account.key_requests import (
    KEY_REQUEST_TTL,
    AccountKeyRequest,
    cancel_account_key_request,
    create_account_key_request,
    effective_status,
    list_pending_account_key_requests,
    require_pending_account_key_request,
)

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
PERSON = "77777777-7777-4777-8777-777777777777"
OTHER_PERSON = "88888888-8888-4888-8888-888888888888"
MANAGED_CREDENTIALS = {"source": "managed", "api_key": "PKVALUE5566", "secret_key": "SKVALUE7788"}
REGISTRATION = {
    "uid": "account-uid",
    "account_uid": "account-uid",
    "unique_identifier": "123__ALPACA",
    "account_name": "Main",
    "is_paper": False,
    "account_is_active": True,
    "credential_source": "managed",
    "api_key_secret_name": "ALPACA_CONNECTORS__123__ALPACA__API_KEY",
    "secret_key_secret_name": "ALPACA_CONNECTORS__123__ALPACA__SECRET_KEY",
}


def key_request(**changes: object) -> AccountKeyRequest:
    values: dict[str, object] = {
        "uid": uuid.uuid4(),
        "requester_uid": PERSON,
        "action": "register",
        "account_name": "Main",
        "environment": "live",
        "account_uid": None,
        "status": "pending",
        "created_at": NOW,
        "expires_at": NOW + KEY_REQUEST_TTL,
        "completed_at": None,
        **changes,
    }
    return AccountKeyRequest.model_construct(**values)


@pytest.fixture
def stored(monkeypatch: pytest.MonkeyPatch) -> dict[str, AccountKeyRequest]:
    """An in-memory stand-in for the request rows."""
    rows: dict[str, AccountKeyRequest] = {}
    monkeypatch.setattr(key_requests, "_start_engine", lambda: None)

    def create(payload):
        row = key_request(**payload)
        rows[str(row.uid)] = row
        return row

    def update(uid, changes):
        row = rows[str(uid)].model_copy(update=changes)
        rows[str(uid)] = row
        return row

    def filter_rows(*, limit=500, **filters):
        return [
            row
            for row in rows.values()
            if all(getattr(row, key) == value for key, value in filters.items())
        ]

    monkeypatch.setattr(
        AccountKeyRequest, "create", classmethod(lambda cls, payload: create(payload))
    )
    monkeypatch.setattr(
        AccountKeyRequest, "update", classmethod(lambda cls, uid, changes: update(uid, changes))
    )
    monkeypatch.setattr(
        AccountKeyRequest, "get_by_uid", classmethod(lambda cls, uid: rows.get(str(uid)))
    )
    monkeypatch.setattr(
        AccountKeyRequest, "filter", classmethod(lambda cls, **kw: filter_rows(**kw))
    )
    return rows


def test_a_request_is_pending_for_its_lifetime_and_then_expired() -> None:
    request = key_request()

    assert effective_status(request, now=NOW) == "pending"
    assert effective_status(request, now=NOW + KEY_REQUEST_TTL) == "expired"
    assert effective_status(key_request(status="completed"), now=NOW + 2 * KEY_REQUEST_TTL) == (
        "completed"
    )


def test_create_validates_the_request_shape(stored) -> None:
    with pytest.raises(ValueError, match="person"):
        create_account_key_request(requester_uid="", action="register", environment="paper")
    with pytest.raises(ValueError, match="environment"):
        create_account_key_request(requester_uid=PERSON, action="register", environment="prod")
    with pytest.raises(ValueError, match="account whose keys"):
        create_account_key_request(requester_uid=PERSON, action="rotate", environment="paper")

    created = create_account_key_request(
        requester_uid=PERSON, action="register", environment="paper", now=NOW
    )
    assert created.status == "pending"
    assert created.expires_at == NOW + KEY_REQUEST_TTL


def test_only_the_owner_sees_and_submits_a_pending_request(stored) -> None:
    mine = create_account_key_request(
        requester_uid=PERSON, action="register", environment="live", now=NOW
    )
    create_account_key_request(
        requester_uid=OTHER_PERSON, action="register", environment="live", now=NOW
    )
    expired = create_account_key_request(
        requester_uid=PERSON, action="register", environment="live", now=NOW - 2 * KEY_REQUEST_TTL
    )

    assert [row.uid for row in list_pending_account_key_requests(PERSON, now=NOW)] == [mine.uid]
    with pytest.raises(LookupError):
        require_pending_account_key_request(
            mine.uid, requester_uid=OTHER_PERSON, action="register", now=NOW
        )
    with pytest.raises(ValueError, match="expired"):
        require_pending_account_key_request(
            expired.uid, requester_uid=PERSON, action="register", now=NOW
        )
    with pytest.raises(ValueError, match="live account"):
        require_pending_account_key_request(
            mine.uid, requester_uid=PERSON, action="register", environment="paper", now=NOW
        )
    with pytest.raises(ValueError, match="is for register"):
        require_pending_account_key_request(
            mine.uid, requester_uid=PERSON, action="rotate", account_uid="a", now=NOW
        )
    assert (
        require_pending_account_key_request(
            mine.uid, requester_uid=PERSON, action="register", environment="live", now=NOW
        )
        == mine
    )


def test_a_rotation_request_is_tied_to_one_account(stored) -> None:
    rotation = create_account_key_request(
        requester_uid=PERSON, action="rotate", environment="paper", account_uid="a1", now=NOW
    )

    with pytest.raises(ValueError, match="another account"):
        require_pending_account_key_request(
            rotation.uid, requester_uid=PERSON, action="rotate", account_uid="a2", now=NOW
        )
    assert (
        require_pending_account_key_request(
            rotation.uid, requester_uid=PERSON, action="rotate", account_uid="a1", now=NOW
        )
        == rotation
    )


def test_cancel_only_withdraws_the_owners_pending_request(stored) -> None:
    request = create_account_key_request(
        requester_uid=PERSON, action="register", environment="paper", now=NOW
    )

    with pytest.raises(LookupError):
        cancel_account_key_request(request.uid, requester_uid=OTHER_PERSON, now=NOW)
    assert cancel_account_key_request(request.uid, requester_uid=PERSON, now=NOW).status == (
        "cancelled"
    )
    with pytest.raises(ValueError, match="cancelled"):
        cancel_account_key_request(request.uid, requester_uid=PERSON, now=NOW)


# REST submission ------------------------------------------------------------------------------


def test_the_site_lists_only_the_signed_in_persons_requests(api_client, local_caller) -> None:
    with patch(
        "alpaca_connectors.api.app.services.account_key_requests.list_pending_account_key_requests",
        return_value=[
            key_request(
                requester_uid=local_caller.uid,
                created_at=key_requests.utc_now(),
                expires_at=key_requests.utc_now() + KEY_REQUEST_TTL,
            )
        ],
    ) as listed:
        response = api_client.get("/v1/accounts/key-requests")

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert listed.call_args.args == (local_caller.uid,)
    [item] = response.json()["items"]
    assert (item["action"], item["environment"], item["status"]) == ("register", "live", "pending")


def test_submitting_keys_completes_the_persons_registration_request(api_client, local_caller):
    request = key_request(requester_uid=local_caller.uid)
    with (
        patch(
            "alpaca_connectors.account.key_requests.require_pending_account_key_request",
            return_value=request,
        ) as required,
        patch(
            "alpaca_connectors.api.app.services.accounts.register_alpaca_account",
            return_value=SimpleNamespace(account_uid="account-uid"),
        ) as registered,
        patch("alpaca_connectors.account.key_requests.complete_account_key_request") as completed,
        patch(
            "alpaca_connectors.api.app.services.accounts.get_account_registration",
            return_value=REGISTRATION,
        ),
    ):
        response = api_client.post(
            "/v1/accounts",
            json={
                "environment": "live",
                "credentials": MANAGED_CREDENTIALS,
                "key_request_uid": str(request.uid),
            },
        )

    assert response.status_code == 201
    assert required.call_args.kwargs == {
        "requester_uid": local_caller.uid,
        "action": "register",
        "environment": "live",
    }
    assert registered.call_args.kwargs["account_name"] == "Main"
    assert registered.call_args.kwargs["actor_user_uid"] == local_caller.uid
    assert completed.call_args.args == (request.uid,)
    assert completed.call_args.kwargs == {"account_uid": "account-uid"}


def test_a_refused_request_writes_nothing(api_client) -> None:
    with (
        patch(
            "alpaca_connectors.account.key_requests.require_pending_account_key_request",
            side_effect=ValueError("Account key request r is expired."),
        ),
        patch("alpaca_connectors.api.app.services.accounts.register_alpaca_account") as registered,
    ):
        response = api_client.post(
            "/v1/accounts",
            json={
                "environment": "live",
                "credentials": MANAGED_CREDENTIALS,
                "key_request_uid": "r",
            },
        )

    assert response.status_code == 409
    registered.assert_not_called()
    assert "SKVALUE7788" not in response.text


def test_rotation_needs_credentials_and_completes_for_the_same_account(api_client, local_caller):
    without_keys = api_client.patch(
        "/v1/accounts/account-uid", json={"account_name": "Main", "key_request_uid": "r"}
    )
    request = key_request(
        requester_uid=local_caller.uid, action="rotate", account_uid="account-uid"
    )
    with (
        patch(
            "alpaca_connectors.account.key_requests.require_pending_account_key_request",
            return_value=request,
        ) as required,
        patch(
            "alpaca_connectors.api.app.services.accounts.update_account_registration",
            return_value=REGISTRATION,
        ),
        patch("alpaca_connectors.account.key_requests.complete_account_key_request") as completed,
    ):
        rotated = api_client.patch(
            "/v1/accounts/account-uid",
            json={"credentials": MANAGED_CREDENTIALS, "key_request_uid": str(request.uid)},
        )

    assert without_keys.status_code == 422
    assert rotated.status_code == 200
    assert required.call_args.kwargs == {
        "requester_uid": local_caller.uid,
        "action": "rotate",
        "account_uid": "account-uid",
    }
    assert completed.call_args.kwargs == {"account_uid": "account-uid"}
