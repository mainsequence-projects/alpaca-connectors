"""Registration and holdings sync each make one ``registerAccount`` call (ms-markets ADR 0051).

``MarketsDouble`` stands in for the Markets API: ``msm.api`` row calls reach it as ms-markets'
remote backend and ``registerAccount`` through that backend's client, so every Markets call is
counted. ``OwnTables`` stands in for this project's own tables at the MetaTables operation level
and counts those operations too. Alpaca is the repository's stub trading client; nothing here
touches the network, a broker or a hosted Environment.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from test_account_services import (
    EXTERNAL_NAMES,
    StubTradingClient,
    _imported,
    _legacy_registration,
    _stub_account,
)
from test_alpaca_us_equities import alpaca_catalog_asset

from alpaca_connectors.account.alpaca_account_details import AlpacaAccountDetails
from alpaca_connectors.account.ownership import AccountOwnedByAnotherError, AccountOwner
from alpaca_connectors.account.services import register_alpaca_account
from alpaca_connectors.assets.alpaca_asset_details import AlpacaAssetDetailsTable
from alpaca_connectors.holdings.services import (
    AccountHoldingsRegistryError,
    AccountRegistrationConflictError,
    capture_alpaca_account_holdings,
)

UNIQUE_IDENTIFIER = "010203ABCD__ALPACA_PAPER"
ACCOUNT_UID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
AAPL_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
SPY_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
AAPL = f"ALPACA::{AAPL_ID}"
SPY = f"ALPACA::{SPY_ID}"
WHEN = dt.datetime(2026, 11, 12, 15, 30, tzinfo=dt.UTC)
OWNER = AccountOwner.main_sequence_user("33333333-3333-4333-8333-333333333333")
ASSET_TYPE_CONFLICT = (
    "Existing assets keep their asset_type; change it through the asset routes or send the "
    f"stored type: {AAPL!r} is 'etf', not 'equity'."
)


def _position(symbol: str, asset_id: uuid.UUID, qty: str, side: str = "long") -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        asset_class=SimpleNamespace(value="us_equity"),
        side=SimpleNamespace(value=side),
        qty=qty,
        avg_entry_price="150",
        market_value="1600",
        cost_basis="1500",
        unrealized_pl="100",
        current_price="160",
        exchange=SimpleNamespace(value="NASDAQ"),
        asset_id=asset_id,
    )


def _client(*positions: SimpleNamespace, cash: str = "1000.50") -> StubTradingClient:
    account = SimpleNamespace(**{**vars(_stub_account()), "id": "aid-uuid", "cash": cash})
    catalog = [alpaca_catalog_asset("AAPL", AAPL_ID), alpaca_catalog_asset("SPY", SPY_ID)]
    return StubTradingClient(account, list(positions), raw={"cash": cash}, catalog=catalog)


class MarketsDouble:
    """The Markets API: answers ``registerAccount`` and the account row read, records each call."""

    def __init__(self, *, conflict: str | None = None) -> None:
        from msm.api.accounts import Account

        self.calls: list[str] = []
        self.requests: list[Any] = []
        self.conflict = conflict
        self.asset_uids: dict[str, uuid.UUID] = {}
        self.account = Account(
            uid=ACCOUNT_UID,
            unique_identifier=UNIQUE_IDENTIFIER,
            account_name="Paper",
            is_paper=True,
            account_is_active=True,
        )

    @property
    def client(self) -> MarketsDouble:
        return self

    def call(self, resource: str, operation: str, arguments: Any) -> Any:
        self.calls.append(f"{resource}.{operation}")
        if (resource, operation) == ("account", "get_by_uid"):
            return self.account.model_dump(mode="json")
        raise AssertionError(f"Unexpected Markets row call {resource}.{operation}.")

    def register_account(self, request: Any) -> Any:
        from msm.api.accounts import AccountRegistrationRequest, AccountRegistrationResponse
        from msm.api.remote import MarketsConflictError

        self.calls.append("account.registration")
        payload = AccountRegistrationRequest.model_validate(request)
        json.dumps(payload.model_dump(mode="json"))  # what the client sends
        self.requests.append(payload)
        if self.conflict is not None:
            raise MarketsConflictError(self.conflict)
        changes = {
            name: value
            for name, value in payload.account.model_dump().items()
            if value is not None and name != "unique_identifier"
        }
        self.account = self.account.model_copy(update=changes)
        identifiers = [
            *(asset.unique_identifier for asset in payload.assets),
            *([payload.cash_currency] if payload.cash_currency else []),
            *(position.asset_identifier for position in payload.positions),
        ]
        return AccountRegistrationResponse(
            account=self.account,
            asset_uids={
                identifier: self.asset_uids.setdefault(identifier, uuid.uuid4())
                for identifier in identifiers
            },
            holdings_set_uid=uuid.uuid4(),
            holdings_date=payload.holdings_date,
        )


class OwnTables:
    """This project's tables: the details rows read and the upserts written, each counted."""

    def __init__(
        self,
        *,
        account_details: dict[str, Any] | None = None,
        described_asset_ids: tuple[uuid.UUID, ...] = (),
    ) -> None:
        self.account_details = account_details
        self.described_asset_ids = described_asset_ids
        self.operations: list[str] = []
        self.writes: list[dict[str, Any]] = []

    def compile(self, statement: Any, *, context: Any, operation: str, dialect: Any = None):
        return SimpleNamespace(statement=statement, operation=operation)

    def execute(self, compiled: Any, *, context: Any) -> dict[str, Any]:
        (table,) = compiled.statement.get_final_froms()
        self.operations.append(f"select {table.name}")
        if table is AlpacaAccountDetails.__table__:
            return {"rows": [self.account_details] if self.account_details else []}
        if table is AlpacaAssetDetailsTable.__table__:
            return {"rows": [{"alpaca_asset_id": str(i)} for i in self.described_asset_ids]}
        raise AssertionError(f"Unexpected read of {table.name}.")

    def upsert(self, context: Any, *, model: Any, values: Any, conflict_columns: Any) -> dict:
        self.operations.append(f"upsert {model.__table__.name}")
        self.writes.append(
            {"table": model.__table__.name, "values": values, "conflict": conflict_columns}
        )
        return {"rows": []}


ACCOUNT_DETAILS = AlpacaAccountDetails.__table__.name
ASSET_DETAILS = AlpacaAssetDetailsTable.__table__.name


@pytest.fixture
def markets(monkeypatch):
    from msm.api import remote

    double = MarketsDouble()
    monkeypatch.setattr(remote, "_remote_backend", double)
    return double


@pytest.fixture
def own_tables():
    return OwnTables()


@pytest.fixture(autouse=True)
def _offline(own_tables):
    with (
        patch(
            "alpaca_connectors.runtime.start_markets_engine",
            return_value=SimpleNamespace(context=SimpleNamespace()),
        ),
        patch("msm.bootstrap.resolve_runtime", return_value=SimpleNamespace(context=None)),
        patch("msm.repositories.base.compile_markets_statement", own_tables.compile),
        patch("msm.repositories.base.execute_markets_operation", own_tables.execute),
        patch("msm.repositories.crud.upsert_model", own_tables.upsert),
        patch("msm.repositories.crud.bulk_upsert_model", own_tables.upsert),
    ):
        yield


def _register(client: StubTradingClient, **kwargs: Any):
    with _imported():
        return register_alpaca_account(
            credentials=EXTERNAL_NAMES, client=client, snapshot_time=WHEN, **kwargs
        )


def _known_account(own_tables: OwnTables) -> None:
    own_tables.account_details = {
        "account_uid": str(ACCOUNT_UID),
        "account_unique_identifier": UNIQUE_IDENTIFIER,
        "alpaca_account_id": "aid-uuid",
        "is_paper": True,
        "account_name": "Paper",
        "credential_source": "managed",
        "credentials_stored": True,
        "owner_gateway_uid": OWNER.gateway_uid,
        "owner_user_uid": OWNER.user_uid,
    }
    own_tables.described_asset_ids = (AAPL_ID, SPY_ID)


# ------------------------------------------------------------------------------------------------
# How many calls
# ------------------------------------------------------------------------------------------------
def test_registering_a_known_account_is_one_markets_call(markets, own_tables) -> None:
    _known_account(own_tables)
    client = _client(_position("AAPL", AAPL_ID, "10"), _position("SPY", SPY_ID, "3", "short"))

    result = _register(client, owner=OWNER, account_name="Paper")

    # The registration read, one registerAccount, the details-row write: nothing else.
    assert markets.calls == ["account.registration"]
    assert own_tables.operations == [
        f"select {ACCOUNT_DETAILS}",
        f"select {ASSET_DETAILS}",
        f"upsert {ACCOUNT_DETAILS}",
    ]
    assert result.account_uid == str(ACCOUNT_UID)
    assert result.holdings_rows == 3


def test_a_sync_is_one_registration_read_and_one_markets_call(markets, own_tables) -> None:
    _known_account(own_tables)
    client = _client(_position("AAPL", AAPL_ID, "10"), _position("SPY", SPY_ID, "3"))

    result = capture_alpaca_account_holdings(ACCOUNT_UID, snapshot_time=WHEN, client=client)

    # One registration read (the Account and its details row), one registerAccount, no writes:
    # no held asset is registered again and no cash asset is ensured.
    assert markets.calls == ["account.get_by_uid", "account.registration"]
    assert own_tables.operations == [f"select {ACCOUNT_DETAILS}", f"select {ASSET_DETAILS}"]
    assert own_tables.writes == []
    assert result.holdings_rows == 3
    assert result.holdings_set_uid is not None


def test_new_held_assets_get_their_details_rows_after_the_call(markets, own_tables) -> None:
    own_tables.described_asset_ids = (AAPL_ID,)
    client = _client(_position("AAPL", AAPL_ID, "10"), _position("SPY", SPY_ID, "3"))

    _register(client)

    assert markets.calls == ["account.registration"]
    assert own_tables.operations == [
        f"select {ACCOUNT_DETAILS}",
        f"select {ASSET_DETAILS}",
        f"upsert {ASSET_DETAILS}",
        f"upsert {ACCOUNT_DETAILS}",
    ]
    (asset_write,) = [write for write in own_tables.writes if write["table"] == ASSET_DETAILS]
    # Only SPY is new to this project, keyed by the uid Markets answered for it.
    assert [row["symbol"] for row in asset_write["values"]] == ["SPY"]
    assert asset_write["values"][0]["asset_uid"] == str(markets.asset_uids[SPY])
    assert asset_write["conflict"] == ("asset_uid",)


# ------------------------------------------------------------------------------------------------
# What one call carries
# ------------------------------------------------------------------------------------------------
def test_the_call_carries_the_account_every_held_asset_cash_and_the_positions(
    markets, own_tables
) -> None:
    client = _client(_position("AAPL", AAPL_ID, "10"), _position("SPY", SPY_ID, "3", "short"))

    _register(client, account_name="Paper")

    (request,) = markets.requests
    assert request.account.model_dump() == {
        "unique_identifier": UNIQUE_IDENTIFIER,
        "account_name": "Paper",
        "is_paper": True,
        "account_is_active": True,
    }
    assets = {asset.unique_identifier: asset for asset in request.assets}
    assert set(assets) == {AAPL, SPY}
    assert assets[AAPL].asset_type.asset_type == "equity"
    assert assets[AAPL].snapshot.model_dump() == {
        "name": "AAPL Name",
        "ticker": "AAPL",
        "exchange_code": "NASDAQ",
        "asset_ticker_group_id": None,
    }
    assert request.cash_currency == "USD"
    assert request.holdings_date == WHEN
    # A holdings publish always replaced a snapshot at the same time; registration still does.
    assert request.overwrite is True
    positions = {position.asset_identifier: position for position in request.positions}
    assert (positions[AAPL].quantity, positions[AAPL].direction) == (Decimal("10"), 1)
    assert (positions[SPY].quantity, positions[SPY].direction) == (Decimal("3"), -1)
    assert (positions["USD"].quantity, positions["USD"].extra_details) == (
        Decimal("1000.50"),
        {"kind": "cash"},
    )


def test_a_sync_keeps_the_account_as_it_is(markets, own_tables) -> None:
    _known_account(own_tables)

    capture_alpaca_account_holdings(
        ACCOUNT_UID, snapshot_time=WHEN, client=_client(_position("AAPL", AAPL_ID, "10"))
    )

    (request,) = markets.requests
    # The current name, and no flag, so Markets has nothing of the account's to change.
    assert request.account.model_dump() == {
        "unique_identifier": UNIQUE_IDENTIFIER,
        "account_name": "Paper",
        "is_paper": None,
        "account_is_active": None,
    }
    assert [asset.unique_identifier for asset in request.assets] == [AAPL]


def test_the_response_is_built_from_what_was_written(markets, own_tables) -> None:
    from alpaca_connectors.api.app.schemas import AccountResponse

    _known_account(own_tables)

    result = _register(_client(_position("AAPL", AAPL_ID, "10")), account_name="Renamed")

    response = AccountResponse.model_validate(result.registration)
    assert (response.uid, response.account_uid) == (str(ACCOUNT_UID), str(ACCOUNT_UID))
    assert (response.unique_identifier, response.account_name) == (UNIQUE_IDENTIFIER, "Renamed")
    assert response.credential_source == "imported"
    assert response.credentials_stored is True
    assert str(response.cash) == "1000.50"
    assert (response.owner.gateway_uid, response.owner.user_uid) == (
        OWNER.gateway_uid,
        OWNER.user_uid,
    )
    # The keys reached only the sealed upsert, never the answer.
    assert "PKTEST" not in str(result.registration)
    assert "SKTEST" not in str(result.registration)
    assert {"api_key", "secret_key"}.isdisjoint(result.registration)


def test_an_account_that_holds_nothing_is_written_alone(markets, own_tables) -> None:
    from msm.api.accounts import Account

    with patch.object(Account, "upsert", return_value=markets.account) as upsert:
        result = _register(_client(cash="0"))

    upsert.assert_called_once_with(
        unique_identifier=UNIQUE_IDENTIFIER,
        account_name=UNIQUE_IDENTIFIER,
        is_paper=True,
        account_is_active=True,
    )
    assert markets.requests == []
    assert result.holdings_rows == 0


# ------------------------------------------------------------------------------------------------
# Refusals and repeats
# ------------------------------------------------------------------------------------------------
def test_an_asset_of_another_type_is_a_readable_conflict_that_writes_nothing(own_tables) -> None:
    from msm.api import remote

    markets = MarketsDouble(conflict=ASSET_TYPE_CONFLICT)
    with patch.object(remote, "_remote_backend", markets):
        with pytest.raises(AccountRegistrationConflictError) as raised:
            _register(_client(_position("AAPL", AAPL_ID, "10")))

    message = str(raised.value)
    assert "different asset type" in message
    assert "Nothing was written" in message
    # Markets' detail stays the cause; the answer names no stored asset or type.
    assert AAPL not in message and "etf" not in message
    assert own_tables.writes == []


def test_another_markets_conflict_is_reported_in_the_connectors_words(own_tables) -> None:
    from msm.api import remote

    _known_account(own_tables)
    markets = MarketsDouble(conflict=f"A different snapshot exists for {UNIQUE_IDENTIFIER!r}.")
    with patch.object(remote, "_remote_backend", markets):
        with pytest.raises(AccountRegistrationConflictError) as raised:
            capture_alpaca_account_holdings(
                ACCOUNT_UID, snapshot_time=WHEN, client=_client(_position("AAPL", AAPL_ID, "10"))
            )

    assert str(raised.value) == AccountRegistrationConflictError.MESSAGE
    assert own_tables.writes == []


def test_the_conflict_answers_409_with_the_connectors_message() -> None:
    from alpaca_connectors.api.app.errors import api_http_error

    error = api_http_error(AccountRegistrationConflictError.from_markets(Exception("asset_type")))

    assert error.status_code == 409
    assert error.detail["message"] == AccountRegistrationConflictError.ASSET_TYPE_MESSAGE


def test_an_unresolved_position_writes_nothing(markets, own_tables) -> None:
    stranger = uuid.UUID("99999999-9999-4999-8999-999999999999")

    with pytest.raises(AccountHoldingsRegistryError, match=str(stranger)):
        _register(_client(_position("AAPL", AAPL_ID, "10"), _position("ZZZ", stranger, "1")))

    assert markets.calls == []
    assert own_tables.writes == []


def test_another_owners_account_is_refused_before_any_markets_call(markets, own_tables) -> None:
    _known_account(own_tables)

    with pytest.raises(AccountOwnedByAnotherError):
        _register(
            _client(_position("AAPL", AAPL_ID, "10")),
            owner=AccountOwner.main_sequence_user("66666666-6666-4666-8666-666666666666"),
        )

    assert markets.calls == []
    assert own_tables.operations == [f"select {ACCOUNT_DETAILS}"]


def test_the_one_registration_read_finds_legacy_secret_references(markets, own_tables) -> None:
    own_tables.account_details = {
        **_legacy_registration(),
        "account_unique_identifier": UNIQUE_IDENTIFIER,
    }

    with patch(
        "alpaca_connectors.account.services.delete_legacy_managed_secrets", return_value=([], [])
    ) as delete_legacy:
        _register(_client(_position("AAPL", AAPL_ID, "10")))

    assert own_tables.operations.count(f"select {ACCOUNT_DETAILS}") == 1
    (references,) = delete_legacy.call_args.args
    assert references.api_key_secret_uid == "managed-api-uid"


def test_sending_a_registration_again_succeeds_and_updates_the_same_row(
    markets, own_tables
) -> None:
    client = _client(_position("AAPL", AAPL_ID, "10"))

    first = _register(client)
    _known_account(own_tables)
    second = _register(client)

    assert first.account_uid == second.account_uid == str(ACCOUNT_UID)
    assert markets.calls == ["account.registration", "account.registration"]
    # The same holdings_date: Markets writes nothing the second time (ADR 0051).
    assert markets.requests[0].holdings_date == markets.requests[1].holdings_date
    account_writes = [write for write in own_tables.writes if write["table"] == ACCOUNT_DETAILS]
    assert len(account_writes) == 2
    # Both upsert on the account uid, so the repeat updates the row the first one wrote.
    assert {write["conflict"] for write in account_writes} == {("account_uid",)}
    assert {str(write["values"]["account_uid"]) for write in account_writes} == {str(ACCOUNT_UID)}
