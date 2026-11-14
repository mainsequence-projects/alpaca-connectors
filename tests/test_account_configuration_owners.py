"""Configurations never stand between an account's owner and its removal (ADR 0022, issue #25).

- Removing a registration disables the configurations that run with its keys instead of being
  refused by them (the service is covered in ``test_account_services.AccountRemovalTests``).
- Only an account's owner configures it once it has one.
- An external person never claims an account without an owner while enabled configurations,
  which another principal made, run with its keys.

The API is called through the SDK's real local request identity, as in
``test_account_ownership``: ``vouching.headers()`` is a Main Sequence caller, and
``vouching.headers(**baggage())`` the external person it vouches for.
"""

from __future__ import annotations

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from conftest import CALLER_UID
from test_account_ownership import (  # noqa: F401  (``vouching`` is a fixture)
    EXTERNAL_CREDENTIALS,
    GATEWAY_UID,
    OTHER_PERSON_ID,
    PERSON,
    _register,
    baggage,
    registration,
    vouching,
)

from alpaca_connectors.account.ownership import (
    AccountInUseError,
    AccountOwnedByAnotherError,
    AccountOwner,
    is_claim_by_external_person,
    require_owner_may_configure,
)
from alpaca_connectors.account.services import (
    AccountConfigurations,
    _claimed_account_in_use,
    enabled_account_configurations,
)

CALLER = AccountOwner.main_sequence_user(CALLER_UID)
ANOTHER_USER = AccountOwner.main_sequence_user("99999999-9999-4999-8999-999999999999")
ACCOUNT_UID = "account-uid"
SIGNAL_SERVICES = "alpaca_connectors.api.app.services.signal_jobs"
BAR_SERVICES = "alpaca_connectors.api.app.services.bar_configurations"
SIGNAL_BODY = {"name": "Signal", "universe_uid": "universe-uid", "account_uid": ACCOUNT_UID}
BAR_BODY = {
    "name": "Daily bars",
    "account_uid": ACCOUNT_UID,
    "asset_source": "account_holdings",
    "frequency_id": "1d",
    "feed": "sip",
    "adjustment": "all",
}


class Reached(TypeError):
    """Raised by a stubbed configuration service: the owner check let the call through.

    The API answers it ``400``, apart from a refusal's ``409`` and another person's ``404``.
    """

    def __init__(self) -> None:
        super().__init__("reached the configuration service")


# ------------------------------------------------------------------------------------------------
# The rules
# ------------------------------------------------------------------------------------------------
def test_another_owners_account_cannot_be_configured() -> None:
    with pytest.raises(AccountOwnedByAnotherError) as refused:
        require_owner_may_configure(registration(PERSON), CALLER)
    assert GATEWAY_UID not in str(refused.value)
    for open_to_the_caller in (registration(None), registration(CALLER), None):
        require_owner_may_configure(open_to_the_caller, CALLER)


@pytest.mark.parametrize(
    ("existing", "owner", "claim"),
    [
        (None, PERSON, True),
        (registration(None), PERSON, True),
        (registration(PERSON), PERSON, False),
        (registration(None), CALLER, False),
        (None, CALLER, False),
        (registration(None), None, False),
    ],
    ids=["person-new", "person-unowned", "person-own", "user-unowned", "user-new", "cli"],
)
def test_only_an_external_persons_claim_of_an_unowned_account_is_checked(
    existing, owner, claim
) -> None:
    assert is_claim_by_external_person(existing, owner) is claim


def test_only_enabled_configurations_run_with_an_account() -> None:
    signals = [
        SimpleNamespace(uid="s-on", enabled=True),
        SimpleNamespace(uid="s-paused", enabled=False),
    ]
    bars = [SimpleNamespace(uid="b-off", enabled=False)]
    with (
        patch(
            "alpaca_connectors.operations.signal_job_configurations.signal_job_configurations_for_account",
            return_value=signals,
        ) as read_signals,
        patch(
            "alpaca_connectors.market_data.configurations.bar_configurations_for_account",
            return_value=bars,
        ) as read_bars,
    ):
        found = enabled_account_configurations(ACCOUNT_UID)
    read_signals.assert_called_once_with(ACCOUNT_UID)
    read_bars.assert_called_once_with(ACCOUNT_UID)
    assert found == AccountConfigurations(signal_uids=("s-on",), bar_uids=())
    assert found and len(found) == 1
    assert not AccountConfigurations(signal_uids=(), bar_uids=())


# ------------------------------------------------------------------------------------------------
# An external person's claim
# ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("existing", [None, registration(None)], ids=["new", "unowned"])
def test_an_external_person_cannot_claim_an_account_that_configurations_run_on(existing) -> None:
    result = _register(PERSON, existing, in_use=True)
    assert isinstance(result.error, AccountInUseError)
    assert "configurations" in str(result.error)
    result.claimed_in_use.assert_called_once_with(existing, "010203ABCD__ALPACA_PAPER")
    # Refused before any Markets or detail write, like another owner's account (ADR 0019).
    result.resolve_holdings.assert_not_called()
    result.register_holdings.assert_not_called()
    result.upsert_model.assert_not_called()


@pytest.mark.parametrize(
    ("owner", "existing"),
    [(PERSON, registration(PERSON)), (CALLER, registration(None)), (None, registration(None))],
    ids=["the-owner-again", "main-sequence-user", "cli"],
)
def test_other_registrations_never_ask_about_configurations(owner, existing) -> None:
    result = _register(owner, existing, in_use=True)
    assert result.error is None
    result.claimed_in_use.assert_not_called()
    result.upsert_model.assert_called_once()


def test_a_claim_reads_the_existing_rows_account_or_asks_markets_by_identity() -> None:
    reads = "alpaca_connectors.account.services.enabled_account_configurations"
    in_use = AccountConfigurations(signal_uids=("s-on",))
    with (
        patch(reads, return_value=in_use) as configurations,
        patch("msm.api.accounts.Account") as markets_account,
    ):
        assert _claimed_account_in_use(registration(None, uid="row-account"), "ID")
        configurations.assert_called_once_with("row-account")
        markets_account.get_by_unique_identifier.assert_not_called()

        # A registration removed earlier leaves the Account, and configurations can name it.
        configurations.reset_mock()
        markets_account.get_by_unique_identifier.return_value = SimpleNamespace(uid="old-account")
        assert _claimed_account_in_use(None, "ID")
        markets_account.get_by_unique_identifier.assert_called_once_with("ID")
        configurations.assert_called_once_with("old-account")

        configurations.reset_mock()
        markets_account.get_by_unique_identifier.return_value = None
        assert not _claimed_account_in_use(None, "ID")
        configurations.assert_not_called()


def test_a_claim_of_an_account_in_use_answers_409_naming_nothing(vouching) -> None:
    with patch(
        "alpaca_connectors.api.app.routers.accounts.create_account_registration",
        side_effect=AccountInUseError(),
    ):
        answer = vouching.client.post(
            "/v1/accounts",
            json={"environment": "paper", "credentials": EXTERNAL_CREDENTIALS},
            headers=vouching.headers(**baggage()),
        )
    assert answer.status_code == 409
    assert answer.json()["detail"]["code"] == "account_in_use"


# ------------------------------------------------------------------------------------------------
# Configurations on an owned account
# ------------------------------------------------------------------------------------------------
def _configuration_calls(client, headers) -> dict[str, int]:
    """Each write that makes a configuration run with ``account-uid``'s keys."""
    signal = "/v1/signal-jobs"
    bars = "/v1/market-data/bar-configurations"
    return {
        "signal create": client.post(signal, json=SIGNAL_BODY, headers=headers).status_code,
        "signal update": client.patch(
            f"{signal}/signal-uid", json={"name": "Renamed"}, headers=headers
        ).status_code,
        "signal resume": client.post(
            f"{signal}/signal-uid/actions/resume", headers=headers
        ).status_code,
        "bars create": client.post(bars, json=BAR_BODY, headers=headers).status_code,
        "bars update": client.patch(
            f"{bars}/bars-uid", json={"name": "Renamed"}, headers=headers
        ).status_code,
    }


def _configured_account(owner: AccountOwner | None):
    """The account is owned by ``owner``; both configurations already run with it."""
    configured = SimpleNamespace(account_uid=ACCOUNT_UID)
    return (
        patch(
            "alpaca_connectors.api.app.services.accounts.get_account_registration",
            return_value=registration(owner, uid=ACCOUNT_UID),
        ),
        patch(f"{SIGNAL_SERVICES}.get_signal_job_configuration", return_value=configured),
        patch(f"{BAR_SERVICES}.get_bar_configuration", return_value=configured),
        patch(f"{SIGNAL_SERVICES}.create_signal_job_configuration", side_effect=Reached()),
        patch(f"{SIGNAL_SERVICES}.update_signal_job_configuration", side_effect=Reached()),
        patch(f"{SIGNAL_SERVICES}.resume_signal_job_configuration", side_effect=Reached()),
        patch(f"{BAR_SERVICES}.create_bar_configuration", side_effect=Reached()),
        patch(f"{BAR_SERVICES}.update_bar_configuration", side_effect=Reached()),
    )


def _call(vouching, owner: AccountOwner | None, headers) -> tuple[dict[str, int], list]:
    with ExitStack() as stack:
        _, _, _, *services = [stack.enter_context(item) for item in _configured_account(owner)]
        answers = _configuration_calls(vouching.client, headers)
    return answers, services


def test_a_main_sequence_user_cannot_configure_another_owners_account(vouching) -> None:
    for owner in (PERSON, ANOTHER_USER):
        answers, services = _call(vouching, owner, vouching.headers())
        assert set(answers.values()) == {409}, (owner, answers)
        for service in services:
            service.assert_not_called()


def test_the_owner_and_every_caller_of_an_unowned_account_configure_it(vouching) -> None:
    for owner, headers in (
        (CALLER, vouching.headers()),
        (None, vouching.headers()),
        (PERSON, vouching.headers(**baggage())),
    ):
        answers, services = _call(vouching, owner, headers)
        assert set(answers.values()) == {400}, (owner, answers)
        for service in services:
            service.assert_called_once()


def test_an_owner_refusal_names_the_code_not_the_owner(vouching) -> None:
    read, *_ = _configured_account(PERSON)
    with read:
        answer = vouching.client.post(
            "/v1/signal-jobs", json=SIGNAL_BODY, headers=vouching.headers()
        )
    assert answer.json()["detail"]["code"] == "account_owned_by_another"
    assert GATEWAY_UID not in answer.text


def test_an_external_person_reaches_no_other_persons_configurations(vouching) -> None:
    # An update that names no account checks the configuration's own account (ADR 0022).
    others = AccountOwner(gateway_uid=GATEWAY_UID, user_uid=OTHER_PERSON_ID)
    answers, services = _call(vouching, others, vouching.headers(**baggage()))
    assert set(answers.values()) == {404}, answers
    for service in services:
        service.assert_not_called()


# ------------------------------------------------------------------------------------------------
# Removal
# ------------------------------------------------------------------------------------------------
def test_removal_preflight_warns_instead_of_blocking(vouching) -> None:
    selection = {"selection": {"mode": "explicit", "uids": [ACCOUNT_UID]}, "options": {}}
    with (
        patch(
            "alpaca_connectors.api.app.routers.accounts.get_account",
            return_value=registration(None),
        ),
        patch(
            "alpaca_connectors.api.app.routers.accounts.configurations_disabled_by_removal",
            return_value=2,
        ),
        patch("alpaca_connectors.holdings.list_account_holdings", return_value=([], 0)),
    ):
        answer = vouching.client.post(
            "/v1/accounts/actions/remove/preflight", json=selection, headers=vouching.headers()
        )
    body = answer.json()
    assert answer.status_code == 200
    assert body["allowed"] is True
    assert body["blockers"] == []
    assert any("2 enabled signal or bar configurations" in item for item in body["warnings"])


def test_the_owner_removes_an_account_that_configurations_run_on(vouching) -> None:
    removed = {
        "account_uid": ACCOUNT_UID,
        "registration_removed": True,
        "disabled_signal_configuration_uids": ["s-on"],
        "disabled_bar_configuration_uids": [],
    }
    with (
        patch(
            "alpaca_connectors.api.app.services.accounts.get_account_registration",
            return_value=registration(PERSON),
        ),
        patch(
            "alpaca_connectors.api.app.routers.accounts.remove_account", return_value=removed
        ) as remove,
    ):
        answer = vouching.client.delete(
            f"/v1/accounts/{ACCOUNT_UID}", headers=vouching.headers(**baggage())
        )
    assert answer.status_code == 200
    assert answer.json()["disabled_signal_configuration_uids"] == ["s-on"]
    remove.assert_called_once_with(ACCOUNT_UID)


# ------------------------------------------------------------------------------------------------
# The Agent
# ------------------------------------------------------------------------------------------------
def test_the_agent_configures_and_removes_for_the_person_it_works_for() -> None:
    from alpaca_connectors import agent_tools

    person = AccountOwner.main_sequence_user("person-uid")
    with (
        patch("alpaca_connectors.agent_tools._acting_user_uid", return_value="person-uid"),
        patch(f"{SIGNAL_SERVICES}.create_configuration") as create_signal,
        patch(f"{SIGNAL_SERVICES}.update_configuration") as update_signal,
        patch(f"{BAR_SERVICES}.create_configuration") as create_bars,
        patch(f"{BAR_SERVICES}.update_configuration") as update_bars,
        patch("alpaca_connectors.api.app.services.accounts.remove_account") as remove,
    ):
        agent_tools.manage_etf_signal({"operation": "create", "request": SIGNAL_BODY})
        agent_tools.manage_etf_signal(
            {"operation": "update", "configuration_uid": "s", "request": {"scheduled": True}}
        )
        agent_tools.manage_bar_configuration({"operation": "create", "request": BAR_BODY})
        agent_tools.manage_bar_configuration(
            {"operation": "update", "configuration_uid": "b", "request": {"name": "Renamed"}}
        )
        agent_tools.manage_account(
            {
                "operation": "delete",
                "account_uid": ACCOUNT_UID,
                "confirmation": "DELETE account-uid",
            }
        )
    for call in (create_signal, update_signal, create_bars, update_bars):
        assert call.call_args.kwargs["owner"] == person
    # Configurations no longer refuse the Agent's removal; the service disables them.
    remove.assert_called_once_with(ACCOUNT_UID)


def test_the_cli_dry_run_names_the_configurations_it_would_disable(capsys) -> None:
    from argparse import Namespace

    from alpaca_connectors.cli.account import run_account_remove_command

    services = "alpaca_connectors.account.services"
    with (
        patch(f"{services}.get_account_registration", return_value=registration(None)),
        patch("alpaca_connectors.holdings.services.list_account_holdings", return_value=([], 3)),
        patch(
            f"{services}.enabled_account_configurations",
            return_value=AccountConfigurations(signal_uids=("s-on",), bar_uids=("b-on",)),
        ),
        patch(f"{services}.remove_account_registration") as remove,
    ):
        assert run_account_remove_command(Namespace(account_uid=ACCOUNT_UID, execute=False)) == 0
    remove.assert_not_called()
    printed = capsys.readouterr().out
    assert '"disables_signal_configuration_uids"' in printed
    assert "s-on" in printed and "b-on" in printed
