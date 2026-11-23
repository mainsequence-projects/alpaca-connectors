"""Requests are set-based, never one per item in a loop (issue #46).

- Every list route takes a repeated ``uid`` filter, applied in the same page and count statements
  as ``IN (...)``, so a caller holding N uids makes one request, not N gets.
- The bulk account actions read all selected registrations at once (one details statement and the
  Accounts in Markets batches) and remove the keys of all of them in one update statement.
- An order batch reads its Alpaca assets concurrently; Alpaca has no read of several by id.
"""

from __future__ import annotations

import threading
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from conftest import CALLER_UID
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from alpaca_connectors.account.ownership import AccountOwner

FIRST = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
SECOND = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
CALLER = AccountOwner.main_sequence_user(CALLER_UID)
PERSON = AccountOwner(gateway_uid="77777777-7777-4777-8777-777777777777", user_uid="person-1")
OTHER_PERSON = AccountOwner(gateway_uid=PERSON.gateway_uid, user_uid="person-2")


def _sql(statement: Any) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))


def _params(statement: Any) -> list[Any]:
    values = statement.compile(dialect=postgresql.dialect()).params.values()
    return [item for value in values for item in (value if isinstance(value, list) else [value])]


class Statements:
    """Captures compiled statements; each execution answers the next queued result."""

    def __init__(self, *results: dict[str, Any]):
        self.compiled: list[Any] = []
        self.results = list(results)

    def compile(self, statement, **_):
        self.compiled.append(statement)
        return statement

    def execute(self, operation, **_):
        return self.results.pop(0) if self.results else {"rows": []}


def _assert_filtered_on_uids(statements: Statements) -> None:
    """The page and the count are the only statements, and both carry ``IN`` the uids."""
    assert len(statements.compiled) == 2
    for statement in statements.compiled:
        assert " IN (" in _sql(statement)
        assert {FIRST, SECOND} <= set(_params(statement))


# ------------------------------------------------------------------------------------------------
# The uid filter of every list, in its one page and one count statement
# ------------------------------------------------------------------------------------------------
def test_the_asset_list_filters_on_uids() -> None:
    from alpaca_connectors.assets.catalog import list_assets

    statements = Statements({"rows": []}, {"rows": [{"count": 0}]})
    with (
        patch(
            "alpaca_connectors.runtime.start_markets_engine",
            return_value=SimpleNamespace(context=object()),
        ),
        patch("msm.repositories.base.compile_markets_statement", statements.compile),
        patch("msm.repositories.base.execute_markets_operation", statements.execute),
    ):
        assert list_assets(uids=[str(FIRST), SECOND]) == ([], 0)
    _assert_filtered_on_uids(statements)


def test_the_account_list_filters_on_uids() -> None:
    from alpaca_connectors.account.services import list_account_registrations

    statements = Statements({"rows": []}, {"rows": [{"count": 0}]})
    with (
        patch(
            "alpaca_connectors.runtime.start_markets_engine",
            return_value=SimpleNamespace(context=object()),
        ),
        patch("msm.repositories.base.compile_markets_statement", statements.compile),
        patch("msm.repositories.base.execute_markets_operation", statements.execute),
    ):
        assert list_account_registrations(uids=[str(FIRST), str(SECOND)]) == ([], 0)
    _assert_filtered_on_uids(statements)


def test_the_bar_configuration_list_filters_on_uids() -> None:
    from alpaca_connectors.market_data.configurations import list_bar_configurations

    statements = Statements({"rows": []}, {"rows": [{"count": 0}]})
    with (
        patch("alpaca_connectors.runtime.account_runtime_models", return_value=[]),
        patch("alpaca_connectors.runtime.start_markets_engine"),
        patch("msm.bootstrap.resolve_runtime", return_value=SimpleNamespace(context=object())),
        patch("msm.repositories.base.compile_markets_statement", statements.compile),
        patch("msm.repositories.base.execute_markets_operation", statements.execute),
        patch(
            "alpaca_connectors.market_data.configurations."
            "_configuration_asset_uids_by_configuration",
            return_value={},
        ),
    ):
        assert list_bar_configurations(uids=[str(FIRST), str(SECOND)]) == ([], 0)
    _assert_filtered_on_uids(statements)


def test_the_signal_definition_list_filters_on_uids() -> None:
    from alpaca_connectors.portfolios import signal_definitions

    statements = Statements({"rows": []}, {"rows": [{"count": 0}]})
    with (
        patch.object(signal_definitions, "_start_runtime"),
        patch("msm.bootstrap.resolve_runtime", return_value=SimpleNamespace(context=None)),
        patch("msm.repositories.base.compile_markets_statement", statements.compile),
        patch("msm.repositories.base.execute_markets_operation", statements.execute),
    ):
        # A page past the end reads the count too, so both statements are checked.
        assert signal_definitions.list_signal_definitions(uids=[FIRST, SECOND], offset=25) == (
            [],
            0,
        )
    _assert_filtered_on_uids(statements)


@pytest.mark.parametrize(
    "list_name", ["list_portfolio_configurations", "list_rebalance_configurations"]
)
def test_the_portfolio_and_rebalance_lists_filter_on_uids(list_name: str) -> None:
    from alpaca_connectors.portfolios import configurations

    statements = Statements({"rows": []}, {"rows": [{"count": 0}]})
    with (
        patch.object(configurations, "_start_runtime"),
        patch("msm.bootstrap.resolve_runtime", return_value=SimpleNamespace(context=None)),
        patch("msm.repositories.base.compile_markets_statement", statements.compile),
        patch("msm.repositories.base.execute_markets_operation", statements.execute),
    ):
        # A page past the end reads the count too, so both statements are checked.
        assert getattr(configurations, list_name)(uids=[str(FIRST), str(SECOND)], offset=25) == (
            [],
            0,
        )
    _assert_filtered_on_uids(statements)


@pytest.mark.parametrize("module_name", ["signal_definitions", "configurations"])
def test_a_short_first_page_answers_its_own_count(module_name: str) -> None:
    # Fewer rows than the limit is the whole list: no count is read (issue #46).
    from alpaca_connectors.portfolios import configurations, signal_definitions

    module = {"signal_definitions": signal_definitions, "configurations": configurations}[
        module_name
    ]
    reader = {
        "signal_definitions": signal_definitions.list_signal_definitions,
        "configurations": configurations.list_portfolio_configurations,
    }[module_name]
    statements = Statements({"rows": []})
    with (
        patch.object(module, "_start_runtime"),
        patch("msm.bootstrap.resolve_runtime", return_value=SimpleNamespace(context=None)),
        patch("msm.repositories.base.compile_markets_statement", statements.compile),
        patch("msm.repositories.base.execute_markets_operation", statements.execute),
    ):
        assert reader(uids=[str(FIRST)]) == ([], 0)
    assert len(statements.compiled) == 1


@pytest.mark.parametrize(
    ("path", "service"),
    [
        ("/v1/accounts", "alpaca_connectors.api.app.routers.accounts.list_accounts"),
        (
            "/v1/market-data/bar-configurations",
            "alpaca_connectors.api.app.routers.bar_configurations.list_configurations",
        ),
        (
            "/v1/signal-definitions",
            "alpaca_connectors.api.app.routers.signal_definitions.list_definitions",
        ),
        ("/v1/assets", "alpaca_connectors.api.app.routers.assets.list_asset_resources"),
        (
            "/v1/portfolio-configurations",
            "alpaca_connectors.api.app.routers.portfolio_configurations.list_configurations",
        ),
        (
            "/v1/portfolio-rebalance-configurations",
            "alpaca_connectors.api.app.routers.portfolio_configurations.list_rebalances",
        ),
    ],
)
def test_a_list_route_takes_repeated_uids_in_one_request(api_client, path, service) -> None:
    from alpaca_connectors.api.app.services.common import collection_response

    page = collection_response(items=[], total=0, limit=25, offset=0)
    with patch(service, return_value=page) as listed:
        answer = api_client.get(path, params=[("uid", str(FIRST)), ("uid", str(SECOND).upper())])
        unfiltered = api_client.get(path)
        malformed = api_client.get(path, params={"uid": "not-a-uid"})
    assert answer.status_code == 200, answer.text
    assert listed.call_args_list[0].kwargs["uids"] == [str(FIRST), str(SECOND)]
    assert unfiltered.status_code == 200
    assert listed.call_args_list[1].kwargs["uids"] is None
    assert malformed.status_code == 422
    assert listed.call_count == 2


def test_the_agent_lists_accounts_and_bar_configurations_by_uids() -> None:
    from alpaca_connectors import agent_tools

    with (
        patch("alpaca_connectors.api.app.services.accounts.list_accounts") as accounts,
        patch("alpaca_connectors.api.app.services.bar_configurations.list_configurations") as bars,
    ):
        agent_tools.query_accounts({"operation": "list", "uids": [str(FIRST)]})
        agent_tools.query_bar_configurations({"operation": "list", "uids": [str(SECOND).upper()]})
        agent_tools.query_accounts({"operation": "list"})
        with pytest.raises(ValueError) as refused:
            agent_tools.query_accounts({"operation": "list", "uids": ["secret-looking-value"]})
    assert accounts.call_args_list[0].kwargs["uids"] == [str(FIRST)]
    assert bars.call_args.kwargs["uids"] == [str(SECOND)]
    assert accounts.call_args_list[1].kwargs["uids"] is None
    assert "secret-looking-value" not in str(refused.value)


# ------------------------------------------------------------------------------------------------
# Registrations of several accounts: one details statement and the Accounts in one batch
# ------------------------------------------------------------------------------------------------
def _detail(account_uid: uuid.UUID, owner: AccountOwner | None) -> dict[str, Any]:
    return {
        "account_uid": account_uid,
        "account_unique_identifier": f"{account_uid.hex[:4]}__ALPACA_PAPER",
        "is_paper": True,
        **(owner.column_values() if owner else {}),
    }


def _account(account_uid: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(
        uid=account_uid,
        model_dump=lambda mode="json": {
            "uid": str(account_uid),
            "account_name": "Paper",
            "account_is_active": True,
        },
    )


def test_registrations_of_several_accounts_are_read_together() -> None:
    from msm.api.accounts import Account

    from alpaca_connectors.account.services import get_account_registrations

    missing = uuid.uuid4()
    statements = Statements({"rows": [_detail(FIRST, CALLER), _detail(SECOND, CALLER)]})
    filters: list[dict[str, Any]] = []
    with (
        patch(
            "alpaca_connectors.runtime.start_markets_engine",
            return_value=SimpleNamespace(context=object()),
        ),
        patch("msm.repositories.base.compile_markets_statement", statements.compile),
        patch("msm.repositories.base.execute_markets_operation", statements.execute),
        patch.object(
            Account,
            "filter",
            classmethod(lambda cls, **kw: filters.append(kw) or [_account(FIRST)]),
        ),
        patch.object(Account, "get_by_uid", side_effect=AssertionError("one read per account")),
    ):
        registrations = get_account_registrations([str(FIRST).upper(), str(SECOND), str(missing)])

    assert len(statements.compiled) == 1
    sql = _sql(statements.compiled[0])
    assert " IN (" in sql
    assert "api_key AS api_key" not in sql
    assert {FIRST, SECOND, missing} <= set(_params(statements.compiled[0]))
    assert filters == [{"uid__in": [str(FIRST), str(SECOND)], "limit": 2}]
    # Keyed by the uid as given; an account without its Markets Account or details is absent.
    assert list(registrations) == [str(FIRST).upper()]
    assert registrations[str(FIRST).upper()]["account_name"] == "Paper"


def test_the_keys_of_several_accounts_are_removed_in_one_statement() -> None:
    from alpaca_connectors.account.services import remove_accounts_keys

    statements = Statements()
    with (
        patch(
            "alpaca_connectors.runtime.start_markets_engine",
            return_value=SimpleNamespace(context=object()),
        ),
        patch("msm.repositories.base.compile_markets_statement", statements.compile),
        patch("msm.repositories.base.execute_markets_operation", statements.execute),
        patch(
            "alpaca_connectors.account.services.get_account_registration",
            side_effect=AssertionError("nothing is read again"),
        ),
    ):
        results = remove_accounts_keys([str(FIRST), str(SECOND)], actor_user_uid="person")

    assert results == [
        {"account_uid": str(FIRST), "keys_removed": True},
        {"account_uid": str(SECOND), "keys_removed": True},
    ]
    assert len(statements.compiled) == 1
    statement = statements.compiled[0]
    sql = _sql(statement)
    assert sql.startswith("UPDATE ")
    assert " IN (" in sql
    assert {FIRST, SECOND} <= set(_params(statement))
    assert "api_key=" in sql.replace(" ", "")
    assert "person" in _params(statement)


def _registrations(*owners: tuple[uuid.UUID, AccountOwner | None]) -> dict[str, dict]:
    from alpaca_connectors.account.services import _registration_row

    return {
        str(uid): _registration_row(_account(uid).model_dump(), _detail(uid, owner))
        for uid, owner in owners
    }


def test_the_bulk_key_removal_reads_once_and_writes_once(api_client) -> None:
    selection = {
        "selection": {"mode": "explicit", "uids": [str(FIRST), str(SECOND)]},
        "options": {},
    }
    accounts = "alpaca_connectors.api.app.services.accounts"
    with (
        patch(
            f"{accounts}.get_account_registrations",
            return_value=_registrations((FIRST, CALLER), (SECOND, CALLER)),
        ) as read,
        patch(
            f"{accounts}.get_account_registration",
            side_effect=AssertionError("one read per account"),
        ),
        patch(
            "alpaca_connectors.account.services.remove_accounts_keys",
            return_value=[{"account_uid": str(FIRST), "keys_removed": True}],
        ) as remove,
        patch("alpaca_connectors.account.services.remove_account_keys") as remove_one,
    ):
        preflight = api_client.post("/v1/accounts/actions/remove-keys/preflight", json=selection)
        assert read.call_count == 1
        answer = api_client.post("/v1/accounts/actions/remove-keys", json=selection)

    assert preflight.status_code == 200, preflight.text
    assert preflight.json()["allowed"] is True
    assert preflight.json()["matched_count"] == 2
    assert answer.status_code == 200, answer.text
    # The action's preflight is its only read; the keys go in one statement.
    assert read.call_count == 2
    remove.assert_called_once_with([str(FIRST), str(SECOND)], actor_user_uid=CALLER_UID)
    remove_one.assert_not_called()


def test_the_bulk_capture_reads_the_registrations_once_and_passes_them_on(api_client) -> None:
    selection = {
        "selection": {"mode": "explicit", "uids": [str(FIRST), str(SECOND)]},
        "options": {},
    }
    registrations = _registrations((FIRST, CALLER), (SECOND, CALLER))
    accounts = "alpaca_connectors.api.app.services.accounts"
    router = "alpaca_connectors.api.app.routers.accounts"
    with (
        patch(f"{accounts}.get_account_registrations", return_value=registrations) as read,
        patch(
            f"{accounts}.get_account_registration",
            side_effect=AssertionError("one read per account"),
        ),
        patch(f"{router}.preflight_holdings", return_value={"allowed": True}) as plan,
        patch(f"{router}.capture_holdings", return_value={"holdings_rows": 1}) as capture,
    ):
        answer = api_client.post("/v1/accounts/actions/capture-holdings", json=selection)

    assert answer.status_code == 200, answer.text
    # One read for the access check, the preflight and the capture; Alpaca still once per
    # account (each has its own keys), with the registration already read.
    read.assert_called_once()
    for step in (plan, capture):
        assert [call.kwargs["registration"] for call in step.call_args_list] == [
            registrations[str(FIRST)],
            registrations[str(SECOND)],
        ]


def test_the_bulk_key_removal_reports_a_missing_account_and_refuses_anothers(api_client) -> None:
    selection = {
        "selection": {"mode": "explicit", "uids": [str(FIRST), str(SECOND)]},
        "options": {},
    }
    read = "alpaca_connectors.api.app.services.accounts.get_account_registrations"
    with patch(read, return_value=_registrations((FIRST, CALLER))):
        missing = api_client.post("/v1/accounts/actions/remove-keys/preflight", json=selection)
    with patch(read, return_value=_registrations((FIRST, CALLER), (SECOND, PERSON))):
        anothers = api_client.post("/v1/accounts/actions/remove-keys/preflight", json=selection)
    assert missing.status_code == 200
    assert missing.json()["allowed"] is False
    assert missing.json()["blockers"] == [f"Missing account: {SECOND}"]
    assert anothers.status_code == 409
    assert anothers.json()["detail"]["code"] == "account_owned_by_another"


def test_several_accounts_are_checked_for_an_external_person_with_one_read() -> None:
    from alpaca_connectors.api.app.services import accounts

    uids = [str(FIRST), str(SECOND)]
    with patch.object(
        accounts,
        "get_account_registrations",
        return_value=_registrations((FIRST, PERSON), (SECOND, PERSON)),
    ) as read:
        accounts.require_accounts_access(uids, PERSON)
        read.assert_called_once_with(uids)
        with pytest.raises(HTTPException) as refused:
            accounts.require_accounts_access(uids, OTHER_PERSON)
        assert refused.value.status_code == 404
        # A Main Sequence user's call reads nothing here, as for one account.
        accounts.require_accounts_access(uids, CALLER)
    assert read.call_count == 2


def test_an_external_persons_ownership_check_reads_the_registration_once() -> None:
    from alpaca_connectors.api.app.services import accounts

    own = _registrations((FIRST, PERSON))[str(FIRST)]
    with patch.object(accounts, "get_account_registration", return_value=own) as read:
        accounts.require_account_owned_by(FIRST, PERSON)
        read.assert_called_once_with(str(FIRST))
        with pytest.raises(HTTPException) as refused:
            accounts.require_account_owned_by(FIRST, OTHER_PERSON)
    assert refused.value.status_code == 404
    with patch.object(accounts, "get_account_registration", return_value=None):
        for owner in (PERSON, CALLER):
            with pytest.raises(HTTPException) as missing:
                accounts.require_account_owned_by(FIRST, owner)
            assert missing.value.status_code == 404


# ------------------------------------------------------------------------------------------------
# An order batch's Alpaca assets are read at once
# ------------------------------------------------------------------------------------------------
def test_an_order_batch_reads_its_alpaca_assets_concurrently() -> None:
    from test_execution import AAPL, MSFT, Harness, leg, submission

    from alpaca_connectors.execution.services import _resolve_assets

    harness = Harness()
    read_one = harness.client.get_asset
    # Each read waits for the other: read one after the other, the barrier times out.
    both = threading.Barrier(2, timeout=5)

    def get_asset(key):
        both.wait()
        return read_one(key)

    harness.client.get_asset = get_asset
    records, checks, _ = _resolve_assets(
        harness.deps,
        submission(leg("a", AAPL, "AAPL", "buy", 1), leg("m", MSFT, "MSFT", "sell", 1)),
    )
    assert [record.client_order_id for record in records] == ["a", "m"]
    assert checks == []


def test_a_concurrent_asset_read_still_rejects_refused_keys() -> None:
    from test_execution import AAPL, MSFT, Harness, alpaca_error, leg, submission

    from alpaca_connectors.account.credentials import AlpacaCredentialsRejectedError
    from alpaca_connectors.execution.services import _resolve_assets

    harness = Harness()

    def unauthorized(key):
        raise alpaca_error(401, "unauthorized", 40110000)

    harness.client.get_asset = unauthorized
    with pytest.raises(AlpacaCredentialsRejectedError):
        _resolve_assets(
            harness.deps,
            submission(leg("a", AAPL, "AAPL", "buy", 1), leg("m", MSFT, "MSFT", "sell", 1)),
        )
