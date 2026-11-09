"""Account sync answered as an observable operation (issue #14).

The API is called through the SDK's real local request identity. The operation rows go through
the real `alpaca_connectors.operations.account_sync` functions into an in-memory stand-in for the
MetaTable; only the holdings capture is replaced.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import logging
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from msm.api.http import ObservableOperation
from test_account_ownership import (  # noqa: F401 - `vouching` is a fixture used by name
    GATEWAY_UID,
    OTHER_PERSON_ID,
    PERSON,
    PERSON_ID,
    baggage,
    registration,
    vouching,
)

from alpaca_connectors.account.credentials import AlpacaCredentialsRejectedError
from alpaca_connectors.account.ownership import AccountOwner
from alpaca_connectors.api.app.main import app
from alpaca_connectors.api.app.schemas import AccountSyncOperationResponse
from alpaca_connectors.operations import account_sync
from alpaca_connectors.operations.account_sync import (
    AccountSyncOperation,
    AccountSyncOperationTable,
)

ROOT = Path(__file__).resolve().parents[1]
ACCOUNT_UID = "account-uid"
HOLDINGS_SET_UID = "99999999-9999-4999-8999-999999999999"
SNAPSHOT_TIME = dt.datetime(2026, 11, 9, 15, 30, tzinfo=dt.timezone.utc)
KEY_VALUE = "PKSYNCTESTKEYVALUE"
CAPTURED = {
    "account_uid": ACCOUNT_UID,
    "holdings_set_uid": HOLDINGS_SET_UID,
    "time_index": SNAPSHOT_TIME,
    "holdings_rows": 3,
    "unresolved_symbols": [],
}


class OperationRows:
    """In-memory stand-in for the account sync MetaTable."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def create(self, values: dict[str, Any]) -> AccountSyncOperation:
        row = dict(values)
        self.rows[str(row["uid"])] = row
        return AccountSyncOperation.model_validate(row)

    def update(self, uid: uuid.UUID | str, values: dict[str, Any]) -> AccountSyncOperation:
        row = self.rows[str(uid)]
        row.update(values)
        return AccountSyncOperation.model_validate(row)

    def get_by_uid(self, uid: uuid.UUID | str) -> AccountSyncOperation | None:
        row = self.rows.get(str(uid))
        return AccountSyncOperation.model_validate(row) if row else None


@pytest.fixture
def rows() -> OperationRows:
    store = OperationRows()
    with (
        patch.object(account_sync, "_start_engine"),
        patch.object(AccountSyncOperation, "create", side_effect=store.create),
        patch.object(AccountSyncOperation, "update", side_effect=store.update),
        patch.object(AccountSyncOperation, "get_by_uid", side_effect=store.get_by_uid),
    ):
        yield store


@pytest.fixture
def client(local_caller) -> TestClient:
    return TestClient(app, headers=local_caller.headers)


def _sync(client: TestClient, *, captured: Any = CAPTURED, registered: Any = None, **kwargs):
    capture = (
        patch(
            "alpaca_connectors.api.app.services.account_sync.capture_holdings",
            side_effect=captured,
        )
        if isinstance(captured, Exception)
        else patch(
            "alpaca_connectors.api.app.services.account_sync.capture_holdings",
            return_value=captured,
        )
    )
    with (
        patch(
            "alpaca_connectors.api.app.routers.accounts.get_account",
            return_value=registration(None) if registered is None else registered,
        ),
        capture as capture_holdings,
    ):
        response = client.post(f"/v1/accounts/{ACCOUNT_UID}/actions/sync", **kwargs)
    return response, capture_holdings


# ------------------------------------------------------------------------------------------------
# Success and reading it again
# ------------------------------------------------------------------------------------------------
def test_a_sync_publishes_one_snapshot_and_answers_succeeded(client, rows) -> None:
    response, capture = _sync(client)

    assert response.status_code == 200
    capture.assert_called_once_with(ACCOUNT_UID)
    body = response.json()
    operation = AccountSyncOperationResponse.model_validate(body)
    assert isinstance(operation, ObservableOperation)
    assert (operation.action, operation.status, operation.error) == (
        "account_sync",
        "succeeded",
        None,
    )
    assert operation.request.account_uid == ACCOUNT_UID
    assert operation.result.model_dump() == {
        "account_uid": ACCOUNT_UID,
        "holdings_set_uid": HOLDINGS_SET_UID,
        "time_index": SNAPSHOT_TIME,
        "holdings_rows": 3,
        "extra": {"unresolved_symbols": []},
    }
    [step] = operation.steps
    assert (step.key, step.status) == ("capture_holdings", "succeeded")
    assert operation.current_step is None
    assert operation.completed_at is not None
    assert list(rows.rows) == [operation.operation_uid]


def test_the_operation_is_read_again_by_its_owner(client, rows) -> None:
    synced, _ = _sync(client)
    uid = synced.json()["operation_uid"]

    again = client.get(f"/v1/operations/{uid}")

    assert again.status_code == 200
    assert again.json() == synced.json()
    assert again.headers["cache-control"] == "no-store"


def test_another_caller_and_an_unknown_uid_get_404(client, local_caller, rows) -> None:
    synced, _ = _sync(client)
    uid = synced.json()["operation_uid"]

    other = client.get(f"/v1/operations/{uid}", headers=local_caller.other_headers)
    unknown = client.get(f"/v1/operations/{uuid.uuid4()}")
    malformed = client.get("/v1/operations/not-a-uid")

    assert {other.status_code, unknown.status_code, malformed.status_code} == {404}
    assert other.json() == unknown.json()


# ------------------------------------------------------------------------------------------------
# Failures
# ------------------------------------------------------------------------------------------------
def test_a_capture_that_fails_answers_a_failed_operation(client, rows) -> None:
    response, _ = _sync(client, captured=AlpacaCredentialsRejectedError())

    assert response.status_code == 200
    operation = AccountSyncOperationResponse.model_validate(response.json())
    assert operation.status == "failed"
    assert operation.result is None
    assert operation.error.code == "alpaca_credentials_rejected"
    assert operation.error.retryable is False
    [step] = operation.steps
    assert (step.status, step.message) == ("failed", operation.error.message)
    assert operation.current_step == "capture_holdings"

    again = client.get(f"/v1/operations/{operation.operation_uid}")
    assert again.json() == response.json()


def test_an_unexpected_failure_is_sanitized(client, rows) -> None:
    private = f"provider trace with {KEY_VALUE}"
    response, _ = _sync(client, captured=RuntimeError(private))

    body = response.json()
    assert body["status"] == "failed"
    assert body["error"] == {
        "code": "dependency_unavailable",
        "message": "The operation could not be completed by a required service.",
        "retryable": True,
    }
    assert KEY_VALUE not in response.text
    assert KEY_VALUE not in repr(rows.rows)


def test_an_unknown_account_is_refused_and_nothing_runs(client, rows) -> None:
    with patch("alpaca_connectors.api.app.routers.accounts.get_account", return_value=None):
        with patch("alpaca_connectors.api.app.services.account_sync.capture_holdings") as capture:
            response = client.post(f"/v1/accounts/{ACCOUNT_UID}/actions/sync")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "not_found"
    capture.assert_not_called()
    assert rows.rows == {}


def test_an_operation_that_cannot_be_recorded_runs_no_capture(client) -> None:
    with (
        patch.object(account_sync, "_start_engine"),
        patch.object(AccountSyncOperation, "create", side_effect=RuntimeError("store down")),
    ):
        response, capture = _sync(client)

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "dependency_unavailable"
    capture.assert_not_called()


def test_the_log_names_the_owner_and_error_code_never_the_message(client, rows, caplog) -> None:
    caplog.set_level(logging.INFO, logger="alpaca_connectors.api.app.services.accounts")
    _sync(client, captured=RuntimeError(KEY_VALUE))

    [record] = [r for r in caplog.records if r.getMessage() == "Account sync failed."]
    assert record.account_uid == ACCOUNT_UID
    assert (record.owner_gateway_uid, record.owner_user_uid) == (record.owner_user_uid,) * 2
    assert record.error_code == "dependency_unavailable"
    assert KEY_VALUE not in repr(record.__dict__)


# ------------------------------------------------------------------------------------------------
# Owners (ADR 0019)
# ------------------------------------------------------------------------------------------------
def test_an_external_person_syncs_and_reads_back_only_their_own_account(vouching, rows) -> None:
    own = registration(PERSON)
    with patch(
        "alpaca_connectors.api.app.services.accounts.get_account_registration", return_value=own
    ):
        response, _ = _sync(vouching.client, registered=own, headers=vouching.headers(**baggage()))

    assert response.status_code == 200
    uid = response.json()["operation_uid"]
    assert (rows.rows[uid]["owner_gateway_uid"], rows.rows[uid]["owner_user_uid"]) == (
        GATEWAY_UID,
        PERSON_ID,
    )
    mine = vouching.client.get(f"/v1/operations/{uid}", headers=vouching.headers(**baggage()))
    other_person = vouching.client.get(
        f"/v1/operations/{uid}", headers=vouching.headers(**baggage(person=OTHER_PERSON_ID))
    )
    the_caller_itself = vouching.client.get(f"/v1/operations/{uid}", headers=vouching.headers())
    assert mine.status_code == 200
    assert (other_person.status_code, the_caller_itself.status_code) == (404, 404)


def test_an_external_person_is_refused_on_another_persons_account(vouching, rows) -> None:
    others = registration(AccountOwner(gateway_uid=GATEWAY_UID, user_uid=OTHER_PERSON_ID))
    with patch(
        "alpaca_connectors.api.app.services.accounts.get_account_registration",
        return_value=others,
    ):
        response, capture = _sync(
            vouching.client, registered=others, headers=vouching.headers(**baggage())
        )

    assert response.status_code == 404
    capture.assert_not_called()
    assert rows.rows == {}


# ------------------------------------------------------------------------------------------------
# Revision 0023
# ------------------------------------------------------------------------------------------------
def _revision():
    path = (
        ROOT
        / "src/alpaca_connectors/migrations/versions/alpaca_connectors"
        / "0023_add_account_sync_operations.py"
    )
    spec = importlib.util.spec_from_file_location("revision_0023", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_0023_creates_the_table_the_model_declares() -> None:
    revision = _revision()
    table = AccountSyncOperationTable.__table__
    assert (revision.revision, revision.down_revision) == ("0023", "0022")
    assert revision.TABLE_NAME == table.name

    op = MagicMock()
    op.f.side_effect = lambda name: name
    with patch.object(revision, "op", op):
        revision.upgrade()
    [created] = op.create_table.call_args_list
    name, *columns = created.args
    assert name == table.name
    declared = {
        column.name: (type(column.type).__name__, getattr(column.type, "length", None))
        for column in columns
        if hasattr(column, "type")
    }
    assert declared == {
        column.name: (type(column.type).__name__, getattr(column.type, "length", None))
        for column in table.columns
    }
    nullable = {column.name: column.nullable for column in columns if hasattr(column, "type")}
    assert nullable == {column.name: column.nullable for column in table.columns}
    assert created.kwargs["info"] == table.info
    assert {(call.args[0], tuple(call.args[2])) for call in op.create_index.call_args_list} == {
        (index.name, tuple(c.name for c in index.columns)) for index in table.indexes
    }

    op = MagicMock()
    op.f.side_effect = lambda name: name
    with patch.object(revision, "op", op):
        revision.downgrade()
    op.drop_table.assert_called_once_with(table.name)
    assert len(op.drop_index.call_args_list) == len(table.indexes)
