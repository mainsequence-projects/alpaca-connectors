"""Account owners vouched for by trusted applications (ADR 0019).

The API is called through the SDK's real local request identity (``local_caller``), which also
attaches the request's ``baggage`` header to the OpenTelemetry context once the caller is
verified. ``CALLER_UID`` is the vouching caller on the allow-list; ``OTHER_CALLER_UID`` is not.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml
from conftest import CALLER_UID, OTHER_CALLER_UID
from fastapi.testclient import TestClient
from test_account_services import (
    EXTERNAL_NAMES,
    StubTradingClient,
    _imported,
    _markets_account,
    _registration_holdings,
    _stub_account,
)

from alpaca_connectors.account.credentials import stored_credential_values
from alpaca_connectors.account.ownership import (
    AccountOwnedByAnotherError,
    AccountOwner,
    registered_owner,
    require_owner_may_register,
)
from alpaca_connectors.account.services import register_alpaca_account
from alpaca_connectors.api.app.caller_context import (
    VOUCHING_CALLERS_ENV,
    VouchingCallersConfigurationError,
    parse_vouching_callers,
)
from alpaca_connectors.api.app.identity import acting_owner
from alpaca_connectors.api.app.main import create_app
from alpaca_connectors.api.app.schemas import AccountResponse
from alpaca_connectors.api.app.services.common import collection_response

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_UID = "77777777-7777-4777-8777-777777777777"
UNTRUSTED_GATEWAY_UID = "88888888-8888-4888-8888-888888888888"
PERSON_ID = "person-0001"
OTHER_PERSON_ID = "person-0002"
SEGMENT = "acme"
GATEWAY_KEY = f"mainsequence.{SEGMENT}.external_user_gateway"
USER_KEY = f"mainsequence.{SEGMENT}.external_user_uid"
PERSON = AccountOwner(gateway_uid=GATEWAY_UID, user_uid=PERSON_ID)
ALLOW_LIST = json.dumps(
    [{"caller_uid": CALLER_UID, "segment": SEGMENT, "gateway_uids": [GATEWAY_UID]}]
)
EXTERNAL_CREDENTIALS = {
    "source": "external",
    "api_key_secret_name": "ALPACA_API_KEY",
    "secret_key_secret_name": "ALPACA_SECRET_KEY",
}


def baggage(gateway: str | None = GATEWAY_UID, person: str | None = PERSON_ID) -> dict[str, str]:
    members = [
        f"{key}={value}" for key, value in ((GATEWAY_KEY, gateway), (USER_KEY, person)) if value
    ]
    return {"baggage": ",".join(members)} if members else {}


def registration(owner: AccountOwner | None, uid: str = "account-uid") -> dict[str, Any]:
    return {
        "uid": uid,
        "account_uid": uid,
        "unique_identifier": "123__ALPACA_PAPER",
        "account_name": "Paper",
        "is_paper": True,
        "account_is_active": True,
        **(owner.column_values() if owner else {}),
    }


@pytest.fixture
def vouching(monkeypatch: pytest.MonkeyPatch, local_caller) -> SimpleNamespace:
    """The local caller is on the allow-list for one gateway; the other local caller is not."""
    monkeypatch.setenv(VOUCHING_CALLERS_ENV, ALLOW_LIST)
    app = create_app()

    @app.get("/test/acting-owner")
    def probe() -> dict[str, str]:
        owner = acting_owner()
        return {"gateway_uid": owner.gateway_uid, "user_uid": owner.user_uid}

    return SimpleNamespace(
        client=TestClient(app),
        headers=lambda **extra: {**local_caller.headers, **extra},
        other_headers=lambda **extra: {**local_caller.other_headers, **extra},
    )


# ------------------------------------------------------------------------------------------------
# The owner pair
# ------------------------------------------------------------------------------------------------
def test_a_main_sequence_user_is_never_an_external_person() -> None:
    assert not AccountOwner.main_sequence_user(CALLER_UID).is_external
    assert PERSON.is_external
    assert PERSON.column_values() == {"owner_gateway_uid": GATEWAY_UID, "owner_user_uid": PERSON_ID}


def test_registered_owner_reads_both_columns_or_none() -> None:
    assert registered_owner(registration(PERSON)) == PERSON
    assert registered_owner(registration(None)) is None
    assert registered_owner({"owner_gateway_uid": GATEWAY_UID, "owner_user_uid": None}) is None
    assert registered_owner(None) is None


@pytest.mark.parametrize(
    "existing", [None, registration(None), registration(PERSON)], ids=["new", "legacy", "same"]
)
def test_a_new_unowned_or_same_owner_registration_proceeds(existing) -> None:
    require_owner_may_register(existing, PERSON)


def test_another_owners_account_is_refused_without_naming_the_owner() -> None:
    with pytest.raises(AccountOwnedByAnotherError) as refused:
        require_owner_may_register(registration(PERSON), AccountOwner.main_sequence_user("u-1"))
    assert GATEWAY_UID not in str(refused.value)
    assert PERSON_ID not in str(refused.value)


def test_account_response_reports_the_owner_pair() -> None:
    assert AccountResponse.model_validate(registration(PERSON)).owner.model_dump() == {
        "gateway_uid": GATEWAY_UID,
        "user_uid": PERSON_ID,
    }
    assert AccountResponse.model_validate(registration(None)).owner is None


def test_a_rotation_never_writes_the_owner() -> None:
    values = stored_credential_values(
        SimpleNamespace(api_key="PKTEST", secret_key="SKTEST"),
        source="managed",
        updated_at=None,
        actor_user_uid=CALLER_UID,
    )
    assert "owner_gateway_uid" not in values
    assert "owner_user_uid" not in values


# ------------------------------------------------------------------------------------------------
# The allow-list
# ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("raw", [None, "", "  ", "[]"])
def test_an_unset_or_empty_allow_list_trusts_no_caller(raw) -> None:
    assert parse_vouching_callers(raw) == {}


def test_an_allow_list_entry_names_its_keys_by_segment() -> None:
    [entry] = parse_vouching_callers(ALLOW_LIST).values()
    assert entry.caller_uid == CALLER_UID
    assert entry.gateway_uids == frozenset({GATEWAY_UID})
    assert (entry.gateway_key, entry.user_key) == (GATEWAY_KEY, USER_KEY)


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "{}",
        json.dumps([{"caller_uid": CALLER_UID, "segment": SEGMENT}]),
        json.dumps(
            [
                {
                    "caller_uid": CALLER_UID,
                    "segment": SEGMENT,
                    "gateway_uids": [GATEWAY_UID],
                    "extra": 1,
                }
            ]
        ),
        json.dumps([{"caller_uid": CALLER_UID, "segment": "Bad-Segment", "gateway_uids": ["g"]}]),
        json.dumps([{"caller_uid": CALLER_UID, "segment": SEGMENT, "gateway_uids": []}]),
        json.dumps([{"caller_uid": "", "segment": SEGMENT, "gateway_uids": ["g"]}]),
        json.dumps([{"caller_uid": CALLER_UID, "segment": SEGMENT, "gateway_uids": ["g"]}] * 2),
    ],
)
def test_a_malformed_allow_list_is_refused_without_quoting_it(raw) -> None:
    with pytest.raises(VouchingCallersConfigurationError) as refused:
        parse_vouching_callers(raw)
    assert CALLER_UID not in str(refused.value)


def test_a_malformed_allow_list_stops_application_creation(monkeypatch, local_caller) -> None:
    monkeypatch.setenv(VOUCHING_CALLERS_ENV, "[{")
    with pytest.raises(VouchingCallersConfigurationError):
        create_app()


def test_the_workflow_declares_an_empty_allow_list() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".mainsequence/workflows/alpaca-connectors-api.yaml").read_text()
    )
    [api] = [resource for resource in workflow["resources"] if resource["key"] == "alpaca"]
    env = {item["name"]: item["value"] for item in api["spec"]["env_vars"]}
    assert parse_vouching_callers(env[VOUCHING_CALLERS_ENV]) == {}


# ------------------------------------------------------------------------------------------------
# The acting owner, through the SDK's request identity
# ------------------------------------------------------------------------------------------------
def test_an_allow_listed_caller_acts_for_the_person_it_names(vouching) -> None:
    answer = vouching.client.get("/test/acting-owner", headers=vouching.headers(**baggage()))
    assert answer.status_code == 200
    assert answer.json() == {"gateway_uid": GATEWAY_UID, "user_uid": PERSON_ID}


def test_an_allow_listed_caller_without_context_acts_for_itself(vouching) -> None:
    answer = vouching.client.get("/test/acting-owner", headers=vouching.headers())
    assert answer.json() == {"gateway_uid": CALLER_UID, "user_uid": CALLER_UID}


def test_context_from_a_caller_outside_the_allow_list_is_never_used(vouching) -> None:
    answer = vouching.client.get("/test/acting-owner", headers=vouching.other_headers(**baggage()))
    assert answer.json() == {"gateway_uid": OTHER_CALLER_UID, "user_uid": OTHER_CALLER_UID}


def test_a_gateway_the_entry_does_not_allow_is_refused(vouching) -> None:
    answer = vouching.client.get(
        "/test/acting-owner",
        headers=vouching.headers(**baggage(gateway=UNTRUSTED_GATEWAY_UID)),
    )
    assert answer.status_code == 403
    assert answer.json()["detail"]["code"] == "caller_context_untrusted_gateway"
    assert UNTRUSTED_GATEWAY_UID not in answer.text
    assert PERSON_ID not in answer.text


@pytest.mark.parametrize(
    "context",
    [
        baggage(person=None),
        baggage(gateway=None),
        baggage(person=GATEWAY_UID),
        baggage(person="x" * 256),
    ],
    ids=["gateway-only", "person-only", "equal-values", "oversized"],
)
def test_an_incomplete_or_invalid_person_is_refused_never_the_callers_own(
    vouching, context
) -> None:
    answer = vouching.client.get("/test/acting-owner", headers=vouching.headers(**context))
    assert answer.status_code == 400
    assert answer.json()["detail"]["code"] == "caller_context_invalid"
    assert PERSON_ID not in answer.text


def test_a_requester_bound_call_ignores_the_context(vouching) -> None:
    with patch("alpaca_connectors.api.app.identity.requester_uid", return_value="person-uid"):
        answer = vouching.client.get("/test/acting-owner", headers=vouching.headers(**baggage()))
    assert answer.json() == {"gateway_uid": "person-uid", "user_uid": "person-uid"}


# ------------------------------------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------------------------------------
def _register(owner: AccountOwner | None, existing: dict[str, Any] | None):
    runtime = SimpleNamespace(context=SimpleNamespace())
    client = StubTradingClient(_stub_account(), [], raw={"cash": "1000.50"})
    with (
        patch("alpaca_connectors.runtime.start_markets_engine", return_value=runtime),
        patch("msm.repositories.crud.upsert_model") as upsert_model,
        _imported(),
        patch(
            "alpaca_connectors.account.services._existing_registration", return_value=existing
        ) as read_existing,
        patch(
            "alpaca_connectors.holdings.services.resolve_account_registration_holdings",
            return_value=_registration_holdings("USD"),
        ) as resolve_holdings,
        patch(
            "alpaca_connectors.holdings.services.register_account_holdings",
            return_value=SimpleNamespace(account=_markets_account()),
        ) as register_holdings,
    ):
        error = None
        try:
            register_alpaca_account(
                credentials=EXTERNAL_NAMES, client=client, actor_user_uid=CALLER_UID, owner=owner
            )
        except AccountOwnedByAnotherError as exc:
            error = exc
    return SimpleNamespace(
        error=error,
        upsert_model=upsert_model,
        read_existing=read_existing,
        resolve_holdings=resolve_holdings,
        register_holdings=register_holdings,
        runtime=runtime,
    )


def test_registering_another_owners_account_writes_nothing() -> None:
    result = _register(AccountOwner.main_sequence_user(CALLER_UID), registration(PERSON))
    assert isinstance(result.error, AccountOwnedByAnotherError)
    result.read_existing.assert_called_once_with(result.runtime.context, "010203ABCD__ALPACA_PAPER")
    result.resolve_holdings.assert_not_called()
    result.register_holdings.assert_not_called()
    result.upsert_model.assert_not_called()


@pytest.mark.parametrize(
    "existing", [None, registration(None), registration(PERSON)], ids=["new", "legacy", "same"]
)
def test_a_registration_records_its_owner(existing) -> None:
    result = _register(PERSON, existing)
    assert result.error is None
    values = result.upsert_model.call_args.kwargs["values"]
    assert (values["owner_gateway_uid"], values["owner_user_uid"]) == (GATEWAY_UID, PERSON_ID)
    # The vouching caller stays the Main Sequence author of the keys.
    assert values["credentials_updated_by_user_uid"] == CALLER_UID


def test_a_registration_without_an_owner_leaves_the_recorded_owner_alone() -> None:
    result = _register(None, registration(PERSON))
    assert result.error is None
    # Read once all the same: the same row carries Secret references from before revision 0018.
    result.read_existing.assert_called_once()
    values = result.upsert_model.call_args.kwargs["values"]
    assert "owner_gateway_uid" not in values
    assert "owner_user_uid" not in values


def test_the_listing_filters_on_the_exact_owner_pair(monkeypatch) -> None:
    from alpaca_connectors.account.services import list_account_registrations

    statements: list[Any] = []
    monkeypatch.setattr(
        "alpaca_connectors.runtime.start_markets_engine",
        lambda **kwargs: SimpleNamespace(context=object()),
    )
    monkeypatch.setattr(
        "msm.repositories.base.compile_markets_statement",
        lambda statement, **kwargs: statements.append(statement) or statement,
    )
    results = iter([{"rows": []}, {"rows": [{"count": 0}]}])
    monkeypatch.setattr(
        "msm.repositories.base.execute_markets_operation", lambda operation, **kwargs: next(results)
    )

    list_account_registrations(owner=PERSON)

    sql = str(statements[0].compile(compile_kwargs={"literal_binds": True}))
    assert f"owner_gateway_uid = '{GATEWAY_UID}'" in sql
    assert f"owner_user_uid = '{PERSON_ID}'" in sql


# ------------------------------------------------------------------------------------------------
# The API: registration, listing and scope
# ------------------------------------------------------------------------------------------------
def test_the_api_registers_the_account_for_the_vouched_person(vouching) -> None:
    with patch(
        "alpaca_connectors.api.app.routers.accounts.create_account_registration",
        return_value=registration(PERSON),
    ) as create:
        answer = vouching.client.post(
            "/v1/accounts",
            json={"environment": "paper", "credentials": EXTERNAL_CREDENTIALS},
            headers=vouching.headers(**baggage()),
        )
    assert answer.status_code == 201
    assert create.call_args.kwargs["owner"] == PERSON
    assert create.call_args.kwargs["actor_user_uid"] == CALLER_UID
    assert answer.json()["owner"] == {"gateway_uid": GATEWAY_UID, "user_uid": PERSON_ID}


def test_a_main_sequence_users_registration_is_owned_by_that_user(vouching) -> None:
    with patch(
        "alpaca_connectors.api.app.routers.accounts.create_account_registration",
        return_value=registration(AccountOwner.main_sequence_user(OTHER_CALLER_UID)),
    ) as create:
        vouching.client.post(
            "/v1/accounts",
            json={"environment": "paper", "credentials": EXTERNAL_CREDENTIALS},
            headers=vouching.other_headers(**baggage()),
        )
    assert create.call_args.kwargs["owner"] == AccountOwner.main_sequence_user(OTHER_CALLER_UID)
    assert not create.call_args.kwargs["owner"].is_external


def test_another_owners_account_answers_409(vouching) -> None:
    with patch(
        "alpaca_connectors.api.app.routers.accounts.create_account_registration",
        side_effect=AccountOwnedByAnotherError(),
    ):
        answer = vouching.client.post(
            "/v1/accounts",
            json={"environment": "paper", "credentials": EXTERNAL_CREDENTIALS},
            headers=vouching.headers(**baggage()),
        )
    assert answer.status_code == 409
    assert answer.json()["detail"]["code"] == "account_owned_by_another"


def test_an_external_person_cannot_complete_a_key_request(vouching) -> None:
    with patch("alpaca_connectors.api.app.routers.accounts.create_account_registration") as create:
        answer = vouching.client.post(
            "/v1/accounts",
            json={
                "environment": "paper",
                "credentials": EXTERNAL_CREDENTIALS,
                "key_request_uid": "key-request",
            },
            headers=vouching.headers(**baggage()),
        )
    assert answer.status_code == 400
    assert answer.json()["detail"]["code"] == "caller_context_invalid"
    create.assert_not_called()


def test_an_external_person_lists_only_their_accounts(vouching) -> None:
    with patch(
        "alpaca_connectors.api.app.routers.accounts.list_accounts",
        return_value=collection_response(items=[], total=0, limit=25, offset=0),
    ) as listing:
        scoped = vouching.client.get("/v1/accounts", headers=vouching.headers(**baggage()))
        other = vouching.client.get(
            "/v1/accounts",
            params={"owner_gateway_uid": GATEWAY_UID, "owner_user_uid": OTHER_PERSON_ID},
            headers=vouching.headers(**baggage()),
        )
    assert scoped.status_code == other.status_code == 200
    assert listing.call_count == 1
    assert listing.call_args.kwargs["owner"] == PERSON
    assert other.json()["items"] == []


def test_a_main_sequence_caller_may_filter_by_owner(vouching) -> None:
    with patch(
        "alpaca_connectors.api.app.routers.accounts.list_accounts",
        return_value=collection_response(items=[], total=0, limit=25, offset=0),
    ) as listing:
        unfiltered = vouching.client.get("/v1/accounts", headers=vouching.other_headers())
        filtered = vouching.client.get(
            "/v1/accounts",
            params={"owner_gateway_uid": GATEWAY_UID, "owner_user_uid": PERSON_ID},
            headers=vouching.other_headers(),
        )
        half = vouching.client.get(
            "/v1/accounts",
            params={"owner_gateway_uid": GATEWAY_UID},
            headers=vouching.other_headers(),
        )
    assert (unfiltered.status_code, filtered.status_code, half.status_code) == (200, 200, 400)
    assert [call.kwargs["owner"] for call in listing.call_args_list] == [None, PERSON]


def _scoped_routes(client: TestClient, headers: dict[str, str]) -> dict[str, int]:
    selection = {"selection": {"mode": "explicit", "uids": ["account-uid"]}, "options": {}}
    order = {
        "batch_id": "batch-1",
        "orders": [
            {
                "client_order_id": "o-1",
                "asset_identifier": "ALPACA::11111111-1111-4111-8111-111111111111",
                "symbol": "AAPL",
                "side": "buy",
                "quantity": 1,
                "order_type": "market",
                "time_in_force": "day",
            }
        ],
    }
    return {
        "get": client.get("/v1/accounts/account-uid", headers=headers).status_code,
        "update": client.patch(
            "/v1/accounts/account-uid", json={"account_name": "x"}, headers=headers
        ).status_code,
        "delete": client.delete("/v1/accounts/account-uid", headers=headers).status_code,
        "refresh": client.post(
            "/v1/accounts/account-uid/actions/refresh", headers=headers
        ).status_code,
        "sync": client.post("/v1/accounts/account-uid/actions/sync", headers=headers).status_code,
        "remove": client.post(
            "/v1/accounts/actions/remove", json=selection, headers=headers
        ).status_code,
        "capture": client.post(
            "/v1/accounts/actions/capture-holdings", json=selection, headers=headers
        ).status_code,
        "holdings": client.get("/v1/accounts/account-uid/holdings", headers=headers).status_code,
        "orders": client.post(
            "/v1/accounts/account-uid/orders/actions/submit", json=order, headers=headers
        ).status_code,
        "universe": client.post(
            "/v1/universes/actions/run",
            json={
                "selection": {"mode": "explicit", "uids": ["universe-uid"]},
                "options": {"account_uid": "account-uid"},
            },
            headers=headers,
        ).status_code,
    }


def test_an_external_person_reaches_no_other_persons_account(vouching) -> None:
    others = registration(AccountOwner(gateway_uid=GATEWAY_UID, user_uid=OTHER_PERSON_ID))
    with (
        patch(
            "alpaca_connectors.api.app.services.accounts.get_account_registration",
            return_value=others,
        ),
        patch("alpaca_connectors.api.app.routers.accounts.update_account") as update,
        patch("alpaca_connectors.api.app.routers.accounts.remove_account") as remove,
        patch("alpaca_connectors.api.app.routers.accounts.refresh_account") as refresh,
        patch("alpaca_connectors.api.app.routers.accounts.sync_account") as sync,
        patch("alpaca_connectors.api.app.routers.accounts.capture_holdings") as capture,
        patch("alpaca_connectors.api.app.routers.orders.submit_orders") as submit,
        patch("alpaca_connectors.api.app.routers.universes.run_universe") as run,
    ):
        answers = _scoped_routes(vouching.client, vouching.headers(**baggage()))
    assert set(answers.values()) == {404}, answers
    for touched in (update, remove, refresh, sync, capture, submit, run):
        touched.assert_not_called()


def test_an_external_person_reaches_their_own_account(vouching) -> None:
    own = registration(PERSON)
    with (
        patch(
            "alpaca_connectors.api.app.services.accounts.get_account_registration",
            return_value=own,
        ),
        patch("alpaca_connectors.api.app.routers.accounts.get_account", return_value=own),
    ):
        answer = vouching.client.get(
            "/v1/accounts/account-uid", headers=vouching.headers(**baggage())
        )
    assert answer.status_code == 200
    assert answer.json()["owner"]["user_uid"] == PERSON_ID


def test_calls_for_main_sequence_users_are_not_scoped(vouching) -> None:
    others = registration(PERSON)
    with (
        patch(
            "alpaca_connectors.api.app.services.accounts.get_account_registration"
        ) as access_read,
        patch("alpaca_connectors.api.app.routers.accounts.get_account", return_value=others),
    ):
        answer = vouching.client.get("/v1/accounts/account-uid", headers=vouching.headers())
    assert answer.status_code == 200
    access_read.assert_not_called()


def test_the_agent_registers_for_the_person_it_works_for() -> None:
    from alpaca_connectors import agent_tools

    with (
        patch("alpaca_connectors.agent_tools._acting_user_uid", return_value="person-uid"),
        patch(
            "alpaca_connectors.api.app.services.accounts.create_account_registration",
            return_value=registration(None),
        ) as create,
    ):
        agent_tools.manage_account(
            {
                "operation": "create",
                "request": {
                    "environment": "paper",
                    "api_key_secret_name": "ALPACA_API_KEY",
                    "secret_key_secret_name": "ALPACA_SECRET_KEY",
                },
            }
        )
    assert create.call_args.kwargs["owner"] == AccountOwner.main_sequence_user("person-uid")


# ------------------------------------------------------------------------------------------------
# Revision 0022
# ------------------------------------------------------------------------------------------------
def _revision():
    path = (
        ROOT
        / "src/alpaca_connectors/migrations/versions/alpaca_connectors/0022_add_account_owner.py"
    )
    spec = importlib.util.spec_from_file_location("revision_0022", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_0022_adds_the_owner_and_backfills_it_from_the_key_author() -> None:
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateIndex

    from alpaca_connectors.account.alpaca_account_details import AlpacaAccountDetails

    revision = _revision()
    assert (revision.revision, revision.down_revision) == ("0022", "0021")
    [model_index] = [
        index
        for index in AlpacaAccountDetails.__table__.indexes
        if [column.name for column in index.columns] == ["owner_gateway_uid", "owner_user_uid"]
    ]
    compiled = str(CreateIndex(model_index).compile(dialect=postgresql.dialect()))
    assert revision.OWNER_INDEX in compiled

    op = MagicMock()
    with patch.object(revision, "op", op):
        revision.upgrade()
    added = {call.args[1].name: call.args[1].type.length for call in op.add_column.call_args_list}
    assert added == {"owner_gateway_uid": 64, "owner_user_uid": 255}
    op.create_index.assert_called_once_with(
        revision.OWNER_INDEX, revision.TABLE_NAME, ["owner_gateway_uid", "owner_user_uid"]
    )
    [backfill] = op.execute.call_args_list
    sql = str(backfill.args[0])
    assert "owner_gateway_uid = credentials_updated_by_user_uid" in sql
    assert "owner_user_uid = credentials_updated_by_user_uid" in sql
    assert "credentials_updated_by_user_uid IS NOT NULL" in sql

    op = MagicMock()
    with patch.object(revision, "op", op):
        revision.downgrade()
    assert [call.args[1] for call in op.drop_column.call_args_list] == [
        "owner_user_uid",
        "owner_gateway_uid",
    ]
