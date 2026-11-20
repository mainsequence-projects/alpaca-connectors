"""Signal definitions: the one kind of signal this connector makes, owned by one account (ADR 0028)."""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

from alpaca_connectors.account.ownership import AccountOwner
from alpaca_connectors.portfolios import signal_definitions
from alpaca_connectors.portfolios.signal_definitions import (
    SignalDefinition,
    SignalDefinitionError,
    account_signal_key,
    create_signal_definition,
    delete_signal_definition,
    delete_signal_rows,
    delete_signals_rows,
    end_signal_definitions,
    normalize_key,
    signal_uid_for_key,
)
from alpaca_connectors.portfolios.signal_observations import SignalObservationBounds

ACCOUNT_UID = uuid.UUID("55555555-5555-4555-8555-555555555555")
DEFINITION_UID = uuid.UUID("66666666-6666-4666-8666-666666666666")
NOW = dt.datetime(2026, 11, 18, 9, tzinfo=dt.UTC)
MODULE = "alpaca_connectors.portfolios.signal_definitions"


def definition(**overrides) -> SignalDefinition:
    values = {
        "uid": DEFINITION_UID,
        "account_uid": ACCOUNT_UID,
        "key": "momentum",
        "signal_key": account_signal_key(ACCOUNT_UID, "momentum"),
        "name": "Momentum",
        "description": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return SignalDefinition.model_validate(values)


# ------------------------------------------------------------------------------------------------
# Identity
# ------------------------------------------------------------------------------------------------
def test_the_key_is_the_applications_and_never_quoted_back() -> None:
    assert normalize_key("momentum/v2") == "momentum/v2"
    for invalid in ("", "   ", " padded", "x" * 256):
        with pytest.raises(SignalDefinitionError) as raised:
            normalize_key(invalid)
        assert raised.value.code == "invalid_signal_key"
        assert "padded" not in str(raised.value)


def test_two_accounts_never_share_a_signal() -> None:
    other = uuid.UUID(int=7)
    assert signal_uid_for_key(account_signal_key(ACCOUNT_UID, "k")) != signal_uid_for_key(
        account_signal_key(other, "k")
    )


# ------------------------------------------------------------------------------------------------
# Create
# ------------------------------------------------------------------------------------------------
def _create(*, existing=None, last_observation=None, **values):
    stored = {}

    def create_model(_context, *, model, values):
        stored.update(values)
        return {"rows": [values]}

    with (
        patch(f"{MODULE}._start_runtime"),
        patch(f"{MODULE}.account_signal_definition", return_value=existing),
        patch(
            "alpaca_connectors.portfolios.signal_observations.read_signal_observation_bounds",
            side_effect=lambda *, signal_uid: SignalObservationBounds(
                signal_uid, last_observation, last_observation
            ),
        ),
        patch("msm.bootstrap.resolve_runtime", return_value=SimpleNamespace(context="ctx")),
        patch("msm.repositories.crud.create_model", side_effect=create_model),
        patch("msm.api.base.operation_result_rows", side_effect=lambda result: result["rows"]),
    ):
        created = create_signal_definition(
            account_uid=ACCOUNT_UID, key="momentum", name=" Momentum ", **values
        )
    return created, stored


def test_a_new_definitions_signal_is_keyed_by_its_account_and_key() -> None:
    created, stored = _create()

    assert stored["account_uid"] == ACCOUNT_UID
    assert stored["key"] == "momentum"
    assert stored["signal_key"] == f"{ACCOUNT_UID}/momentum"
    assert stored["name"] == "Momentum"
    assert created.signal_key == stored["signal_key"]


def test_a_key_is_unique_within_its_account() -> None:
    with pytest.raises(SignalDefinitionError) as raised:
        _create(existing=definition())
    assert (raised.value.code, raised.value.status_code) == ("signal_key_exists", 409)


def test_a_key_whose_signal_a_retired_portfolio_keeps_is_never_given_again() -> None:
    with pytest.raises(SignalDefinitionError) as raised:
        _create(last_observation=NOW)
    assert (raised.value.code, raised.value.status_code) == ("signal_key_taken", 409)


def test_an_adopted_portfolio_keeps_its_signals_key() -> None:
    # Revision 0029 and the adopt rollout keep the configuration UID as the key (decision 9).
    legacy = str(uuid.UUID(int=1))
    _, stored = _create(signal_key=legacy, last_observation=NOW)
    assert stored["signal_key"] == legacy


# ------------------------------------------------------------------------------------------------
# Delete
# ------------------------------------------------------------------------------------------------
def test_a_followed_definition_is_not_deleted() -> None:
    with (
        patch(f"{MODULE}.get_signal_definition", return_value=definition()),
        patch(
            "alpaca_connectors.portfolios.configurations.AlpacaPortfolioConfiguration.filter",
            return_value=[object()],
        ),
        patch.object(SignalDefinition, "delete") as delete,
        pytest.raises(SignalDefinitionError) as raised,
    ):
        delete_signal_definition(DEFINITION_UID)

    assert (raised.value.code, raised.value.status_code) == ("signal_followed", 409)
    delete.assert_not_called()


def test_deleting_a_definition_deletes_its_signal_in_the_order_markets_allows() -> None:
    calls = MagicMock()
    with (
        patch(f"{MODULE}.get_signal_definition", return_value=definition()),
        patch(
            "alpaca_connectors.portfolios.configurations.AlpacaPortfolioConfiguration.filter",
            return_value=[],
        ),
        patch.object(SignalDefinition, "delete", calls.definition),
        patch("alpaca_connectors.portfolios.configurations.ended_signal_uids", return_value=set()),
        patch("alpaca_connectors.portfolios.signal_assets.forget_signals_assets", calls.assets),
        patch("alpaca_connectors.markets_api.end_signals", calls.end_signals),
    ):
        result = delete_signal_definition(DEFINITION_UID)

    signal_uid = signal_uid_for_key(definition().signal_key)
    # Its assets here, then Markets deletes its weights and metadata in one call (ADR 0031).
    assert calls.mock_calls == [
        call.definition(DEFINITION_UID),
        call.assets([signal_uid]),
        call.end_signals([signal_uid]),
    ]
    assert result["signal"] == {"uid": signal_uid, "state": "deleted"}


def test_a_signal_a_retired_portfolio_follows_stays_with_it() -> None:
    with (
        patch(
            "alpaca_connectors.portfolios.configurations.ended_signal_uids",
            return_value={"signal-a"},
        ),
        patch("alpaca_connectors.portfolios.signal_assets.forget_signals_assets") as assets,
        patch("alpaca_connectors.markets_api.end_signals") as end_signals,
    ):
        assert delete_signal_rows("signal-a") == "kept"
    assets.assert_not_called()
    end_signals.assert_not_called()


def test_several_signals_rows_go_in_one_pass() -> None:
    # One read of which signals a retired Portfolio keeps, then one delete of the others' assets
    # and one Markets pass over their signals (issue #46).
    calls = MagicMock()
    with (
        patch(
            "alpaca_connectors.portfolios.configurations.ended_signal_uids",
            calls.kept,
        ),
        patch("alpaca_connectors.portfolios.signal_assets.forget_signals_assets", calls.assets),
        patch("alpaca_connectors.markets_api.end_signals", calls.end_signals),
    ):
        calls.kept.return_value = {"signal-b"}
        states = delete_signals_rows(["signal-a", "signal-b", "signal-c"])

    assert states == {"signal-a": "deleted", "signal-b": "kept", "signal-c": "deleted"}
    assert calls.mock_calls == [
        call.kept(["signal-a", "signal-b", "signal-c"]),
        call.assets(["signal-a", "signal-c"]),
        call.end_signals(["signal-a", "signal-c"]),
    ]


def test_ending_definitions_deletes_only_those_no_portfolio_follows() -> None:
    followed = definition(uid=uuid.UUID(int=41), key="followed", signal_key="followed")
    free = definition(uid=uuid.UUID(int=42), key="free", signal_key="free")
    with (
        patch(
            "alpaca_connectors.portfolios.configurations.portfolio_counts_by_definition",
            return_value={str(followed.uid): 1},
        ) as counts,
        patch("alpaca_connectors.portfolios.configurations.delete_rows_in") as delete,
        patch(
            f"{MODULE}.delete_signals_rows",
            side_effect=lambda signals: dict.fromkeys(signals, "deleted"),
        ) as rows,
    ):
        answers = end_signal_definitions([followed, free])

    counts.assert_called_once_with([str(followed.uid), str(free.uid)])
    assert delete.call_args.args[2] == [free.uid]
    free_signal = signal_uid_for_key("free")
    assert list(rows.call_args.args[0]) == [free_signal]
    assert answers == {
        str(followed.uid): {"state": "followed", "signal": None},
        str(free.uid): {"state": "deleted", "signal": {"uid": free_signal, "state": "deleted"}},
    }


# ------------------------------------------------------------------------------------------------
# API
# ------------------------------------------------------------------------------------------------
SERVICE = "alpaca_connectors.api.app.services.signal_definitions"


def _owned_by(uid: str) -> dict:
    return {"account_uid": str(ACCOUNT_UID), "owner_gateway_uid": uid, "owner_user_uid": uid}


def test_only_the_accounts_owner_creates_its_signal(api_client, local_caller) -> None:
    body = {"account_uid": str(ACCOUNT_UID), "key": "momentum", "name": "Momentum"}
    accounts = "alpaca_connectors.api.app.services.accounts.get_account_registration"
    with (
        patch(accounts, return_value=_owned_by("someone-else")),
        patch(f"{SERVICE}.create_signal_definition") as create,
    ):
        refused = api_client.post("/v1/signal-definitions", json=body)
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "account_owned_by_another"
    create.assert_not_called()

    with (
        patch(accounts, return_value=_owned_by(local_caller.uid)),
        patch(f"{SERVICE}.create_signal_definition", return_value=definition()) as create,
        patch(f"{SERVICE}.portfolio_counts_by_definition", return_value={}),
    ):
        created = api_client.post("/v1/signal-definitions", json=body)
    assert created.status_code == 201, created.text
    assert created.json()["signal_uid"] == signal_uid_for_key(definition().signal_key)
    assert "signal_key" not in created.json()
    assert create.call_args.kwargs == {**body, "description": None}


def test_the_owner_publishes_weights_to_the_signal(api_client, local_caller) -> None:
    from alpaca_connectors.portfolios.provided_weights import PublishedWeights

    published = PublishedWeights("signal-a", 1, 1, NOW, NOW)
    with (
        patch(f"{SERVICE}.get_signal_definition", return_value=definition()),
        patch(
            "alpaca_connectors.api.app.services.accounts.get_account_registration",
            return_value=_owned_by(local_caller.uid),
        ),
        patch(f"{SERVICE}.publish_definition_weights", return_value=published) as publish,
    ):
        answer = api_client.post(
            f"/v1/signal-definitions/{DEFINITION_UID}/weights",
            json={"observations": [{"observed_at": NOW.isoformat(), "weights": {"ALPACA::a": 1}}]},
        )
    assert answer.status_code == 201, answer.text
    assert answer.json()["signal_definition_uid"] == str(DEFINITION_UID)
    assert publish.call_args.args[0] == definition()


def test_an_external_person_lists_only_their_own_accounts_signals() -> None:
    from alpaca_connectors.api.app.services.signal_definitions import list_definitions

    person = AccountOwner(gateway_uid="gateway", user_uid="person")
    with pytest.raises(Exception) as raised:
        list_definitions(limit=10, offset=0, search=None, account_uid=None, owner=person)
    assert getattr(raised.value, "status_code", None) == 404
