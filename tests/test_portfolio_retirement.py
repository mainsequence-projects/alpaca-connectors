"""Deleting a portfolio retires its ms-markets Portfolio (issue #33, ADR 0027).

Only its owner ends it through the API and the Agent; the CLI ends any portfolio for the operator.
"""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest
from msm.api.remote import MarketsConflictError

from alpaca_connectors.account.ownership import AccountOwner
from alpaca_connectors.portfolios.configurations import (
    AlpacaPortfolioConfiguration,
    PortfolioRebalanceConfiguration,
)
from alpaca_connectors.portfolios.provided_weights import (
    PortfolioOwnedByAnotherError,
    ProvidedWeightsError,
    delete_provided_weights_portfolio,
    end_portfolio_configuration,
    provided_signal_uid,
)

CONFIGURATION_UID = uuid.UUID("11111111-1111-4111-8111-111111111111")
PORTFOLIO_UID = uuid.UUID("55555555-5555-4555-8555-555555555555")
OWNER = AccountOwner.main_sequence_user("owner-uid")
RETIRED_AT = dt.datetime(2026, 11, 16, 15, tzinfo=dt.UTC)
ACCOUNT_UID = uuid.UUID("66666666-6666-4666-8666-666666666666")
DEFINITION_UID = uuid.UUID("77777777-7777-4777-8777-777777777777")
CONFIGURATIONS = "alpaca_connectors.portfolios.configurations"
SERVICES = "alpaca_connectors.api.app.services.portfolio_configurations"


def configuration(**overrides) -> AlpacaPortfolioConfiguration:
    now = dt.datetime(2026, 11, 16, 9, tzinfo=dt.UTC)
    values = {
        "uid": CONFIGURATION_UID,
        "name": "Thesis basket",
        "description": None,
        "owner_gateway_uid": OWNER.gateway_uid,
        "owner_user_uid": OWNER.user_uid,
        "account_uid": ACCOUNT_UID,
        "signal_definition_uid": DEFINITION_UID,
        "bars_configuration_uid": None,
        "rebalance_configuration_uid": uuid.uuid4(),
        "portfolio_uid": PORTFOLIO_UID,
        "scheduled": True,
        "upsample_frequency_id": "1d",
        "intraday_bar_interpolation_rule": "ffill",
        "valuation_column": "close",
        "valuation_maximum_staleness_seconds": 86_400,
        "fail_on_missing_prices": True,
        "commission_fee": 0.00018,
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return AlpacaPortfolioConfiguration.model_validate(values)


DEFINITIONS = "alpaca_connectors.portfolios.signal_definitions"
# The definition revision 0029 gave the portfolio: its signal's key is still the configuration UID.
DEFINITION = SimpleNamespace(uid=DEFINITION_UID, signal_key=str(CONFIGURATION_UID))


def _removal(row, *, retire=None):
    """Run the removal with Markets and storage replaced; return the recorder of their calls.

    The signal is the one revision 0029 gave the portfolio: its key is still the configuration
    UID, so its UID is ``provided_signal_uid``. Each kind is one set-based call (issue #46).
    """
    calls = MagicMock()
    calls.retire.side_effect = retire or (
        lambda uid: SimpleNamespace(uid=uid, retired_at=RETIRED_AT)
    )
    patches = (
        patch(f"{CONFIGURATIONS}.get_portfolio_configuration", return_value=row),
        patch("alpaca_connectors.markets_api.retire_portfolio", calls.retire),
        patch(f"{CONFIGURATIONS}.record_ended_portfolios", calls.record),
        patch(f"{CONFIGURATIONS}.delete_portfolio_configuration_rows", calls.delete),
        patch(
            f"{DEFINITIONS}.signal_definitions_by_uids",
            return_value={str(DEFINITION_UID): DEFINITION},
        ),
        patch(f"{DEFINITIONS}.end_signal_definitions", calls.definition),
        # A shared rebalance rule: none was created with this portfolio (issue #42).
        patch(f"{CONFIGURATIONS}.inline_rebalance_configurations", return_value={}),
    )
    calls.definition.return_value = {
        str(DEFINITION_UID): {
            "state": "deleted",
            "signal": {"uid": provided_signal_uid(CONFIGURATION_UID), "state": "kept"},
        }
    }
    return calls, patches


def _recorded():
    """The record the account delete finishes: the retired Portfolio and its signal."""
    return call.record(
        [
            {
                "account_uid": str(ACCOUNT_UID),
                "portfolio_uid": str(PORTFOLIO_UID),
                "portfolio_configuration_uid": CONFIGURATION_UID,
                "signal_uid": provided_signal_uid(CONFIGURATION_UID),
            }
        ]
    )


def test_the_owner_retires_the_portfolio_then_deletes_its_configuration() -> None:
    calls, patches = _removal(configuration())
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        removal = delete_provided_weights_portfolio(CONFIGURATION_UID, owner=OWNER)

    # Retired first, so a refusal leaves everything as it was; recorded under its account, so the
    # account's delete deletes the Portfolio and its signal (ADR 0028). Then the definition it
    # followed goes with it (issue #38); its signal's rows stay with the retired Portfolio.
    assert calls.mock_calls == [
        call.retire(str(PORTFOLIO_UID)),
        _recorded(),
        call.delete([CONFIGURATION_UID]),
        call.definition([DEFINITION]),
    ]
    assert removal.summary() == {
        "configuration_uid": str(CONFIGURATION_UID),
        "deleted": True,
        "portfolio": {
            "uid": str(PORTFOLIO_UID),
            "unique_identifier": "ALPACA_PORTFOLIO__11111111111141118111111111111111",
            "state": "retired",
            "retired_at": RETIRED_AT,
        },
        "signal_definition": {"uid": str(DEFINITION_UID), "state": "deleted"},
        "signal": {"uid": provided_signal_uid(CONFIGURATION_UID), "state": "kept"},
        # A shared rule stays: none was created with this portfolio (issue #42).
        "rebalance_configuration": None,
    }


def test_a_definition_another_portfolio_follows_stays() -> None:
    calls, patches = _removal(configuration())
    calls.definition.return_value = {str(DEFINITION_UID): {"state": "followed", "signal": None}}
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        removal = delete_provided_weights_portfolio(CONFIGURATION_UID, owner=OWNER)

    assert removal.summary()["signal_definition"] == {
        "uid": str(DEFINITION_UID),
        "state": "followed",
    }
    assert removal.summary()["signal"] is None


def test_a_definition_whose_delete_fails_never_fails_the_portfolio_delete() -> None:
    # The configuration row is gone by then, so a raised error could not be retried: say it.
    calls, patches = _removal(configuration())
    calls.definition.side_effect = ConnectionError("Markets is unavailable")
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        removal = delete_provided_weights_portfolio(CONFIGURATION_UID, owner=OWNER)

    assert calls.delete.call_args == call([CONFIGURATION_UID])
    assert removal.summary()["portfolio"]["state"] == "retired"
    assert removal.summary()["signal_definition"] == {
        "uid": str(DEFINITION_UID),
        "state": "failed",
    }
    assert removal.summary()["signal"] is None


def test_a_portfolio_not_yet_adopted_has_no_definition_to_delete() -> None:
    calls, patches = _removal(
        configuration(account_uid=None, signal_definition_uid=None, bars_configuration_uid=None)
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        removal = delete_provided_weights_portfolio(CONFIGURATION_UID, owner=OWNER)

    calls.definition.assert_not_called()
    assert removal.summary()["signal_definition"] is None


def test_a_portfolio_an_account_targets_is_refused_and_nothing_changes() -> None:
    refusal = MarketsConflictError("Portfolio 5555 cannot be retired: 2 accounts' current target")
    calls, patches = _removal(configuration(), retire=refusal)
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patches[5],
        pytest.raises(ProvidedWeightsError) as raised,
    ):
        delete_provided_weights_portfolio(CONFIGURATION_UID, owner=OWNER)

    assert (raised.value.code, raised.value.status_code) == ("portfolio_in_account_target", 409)
    # Names no account and no count.
    assert "2" not in str(raised.value) and "5555" not in str(raised.value)
    calls.record.assert_not_called()
    calls.delete.assert_not_called()


def test_several_portfolios_end_together_and_a_refusal_keeps_only_that_one() -> None:
    # The account delete ends its portfolios in one pass (issue #46): retiring is one call per
    # portfolio, made together, and everything else one call for all. A refusal keeps that
    # portfolio unchanged; every other one ends, and the refusal is raised after.
    from alpaca_connectors.portfolios.provided_weights import end_portfolio_configurations

    first = configuration()
    held = configuration(uid=uuid.UUID(int=2), portfolio_uid=uuid.UUID(int=3))
    later = configuration(uid=uuid.UUID(int=4), portfolio_uid=uuid.UUID(int=5))
    refusal = MarketsConflictError("an account's current target names it")

    def retire(uid):
        if uid == str(held.portfolio_uid):
            raise refusal
        return SimpleNamespace(uid=uid, retired_at=RETIRED_AT)

    calls, patches = _removal(first, retire=retire)
    with (
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patches[5],
        patches[6],
        pytest.raises(ProvidedWeightsError) as raised,
    ):
        end_portfolio_configurations([first, held, later])

    assert raised.value.code == "portfolio_in_account_target"
    # Every retire is attempted; they run together, so in no particular order.
    assert sorted(retired.args[0] for retired in calls.retire.call_args_list) == sorted(
        str(row.portfolio_uid) for row in (first, held, later)
    )
    [recorded] = _recorded().args
    assert calls.record.call_args == call(
        [
            *recorded,
            {
                **recorded[0],
                "portfolio_uid": str(later.portfolio_uid),
                "portfolio_configuration_uid": later.uid,
            },
        ]
    )
    calls.delete.assert_called_once_with([CONFIGURATION_UID, later.uid])
    calls.definition.assert_called_once_with([DEFINITION])


def test_only_the_owner_ends_it() -> None:
    calls, patches = _removal(configuration())
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patches[5],
        pytest.raises(PortfolioOwnedByAnotherError),
    ):
        delete_provided_weights_portfolio(
            CONFIGURATION_UID, owner=AccountOwner.main_sequence_user("someone-else")
        )

    assert calls.mock_calls == []


def test_a_configuration_without_a_portfolio_is_deleted_without_retiring() -> None:
    calls, patches = _removal(configuration(portfolio_uid=None))
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        removal = delete_provided_weights_portfolio(CONFIGURATION_UID, owner=OWNER)

    calls.retire.assert_not_called()
    calls.record.assert_not_called()
    calls.delete.assert_called_once_with([CONFIGURATION_UID])
    assert removal.summary()["portfolio"]["state"] == "absent"


def test_the_retire_composite_calls_the_markets_client() -> None:
    from alpaca_connectors import markets_api

    runtime = SimpleNamespace(client=MagicMock())
    with patch("alpaca_connectors.runtime.use_markets_api", return_value=runtime):
        markets_api.retire_portfolio(str(PORTFOLIO_UID))

    runtime.client.retire_portfolio.assert_called_once_with(str(PORTFOLIO_UID))


def test_forgetting_a_signals_assets_is_one_delete() -> None:
    from alpaca_connectors.portfolios import signal_assets

    statements = []
    with (
        patch.object(signal_assets, "_start_runtime", return_value=SimpleNamespace(context="c")),
        patch(
            "msm.repositories.base.compile_markets_statement",
            side_effect=lambda statement, **kwargs: (
                statements.append((statement, kwargs)) or statement
            ),
        ),
        patch("msm.repositories.base.execute_markets_operation") as execute,
    ):
        signal_assets.forget_signal_assets("signal-a")

    [(statement, kwargs)] = statements
    assert kwargs["operation"] == "delete"
    assert "DELETE FROM alpaca_connectors__signal_asset" in str(statement)
    execute.assert_called_once()


# ------------------------------------------------------------------------------------------------
# The route
# ------------------------------------------------------------------------------------------------
def test_delete_route_retires_the_portfolio(api_client, local_caller) -> None:
    owned = configuration(owner_gateway_uid=local_caller.uid, owner_user_uid=local_caller.uid)
    calls, patches = _removal(owned)
    with (
        patch(f"{SERVICES}.get_portfolio_configuration", return_value=owned),
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patches[5],
    ):
        response = api_client.delete(f"/v1/portfolio-configurations/{CONFIGURATION_UID}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted"] is True
    assert body["portfolio"]["state"] == "retired"
    assert body["portfolio"]["uid"] == str(PORTFOLIO_UID)
    calls.retire.assert_called_once_with(str(PORTFOLIO_UID))


def test_delete_route_answers_409_while_an_account_targets_it(api_client, local_caller) -> None:
    owned = configuration(owner_gateway_uid=local_caller.uid, owner_user_uid=local_caller.uid)
    calls, patches = _removal(owned, retire=MarketsConflictError("named by account targets"))
    with (
        patch(f"{SERVICES}.get_portfolio_configuration", return_value=owned),
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patches[5],
    ):
        response = api_client.delete(f"/v1/portfolio-configurations/{CONFIGURATION_UID}")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "portfolio_in_account_target"
    calls.delete.assert_not_called()


def test_the_agent_ends_a_portfolio_through_the_same_delete() -> None:
    from alpaca_connectors import agent_tools

    with (
        patch("alpaca_connectors.agent_tools._acting_user_uid", return_value="person-uid"),
        patch(f"{SERVICES}.delete_configuration", return_value={"deleted": True}) as delete,
    ):
        agent_tools.manage_portfolio(
            {
                "operation": "delete",
                "configuration_uid": str(CONFIGURATION_UID),
                "confirmation": f"DELETE {CONFIGURATION_UID}",
            }
        )

    delete.assert_called_once()
    assert delete.call_args.args == (str(CONFIGURATION_UID),)


def test_the_cli_ends_any_portfolio_for_the_operator() -> None:
    from argparse import Namespace

    from alpaca_connectors.cli.portfolios import run_delete_command

    # The operator acts for no owner; the same ending retires the Portfolio first.
    calls, patches = _removal(configuration(owner_user_uid="someone-else"))
    with (
        patch(
            "alpaca_connectors.portfolios.get_portfolio_configuration", return_value=configuration()
        ),
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patches[5],
    ):
        assert (
            run_delete_command(Namespace(configuration_uid=str(CONFIGURATION_UID), execute=True))
            == 0
        )

    assert calls.mock_calls == [
        call.retire(str(PORTFOLIO_UID)),
        _recorded(),
        call.delete([CONFIGURATION_UID]),
        call.definition([DEFINITION]),
    ]


# ------------------------------------------------------------------------------------------------
# A rebalance configuration created inline with the portfolio goes with it (issue #42)
# ------------------------------------------------------------------------------------------------
REBALANCE_UID = uuid.UUID("88888888-8888-4888-8888-888888888888")


def _end_with_inline_rebalance(*, named_by_another: bool = False, delete_error=None):
    calls, patches = _removal(configuration())
    calls.delete_rebalance.side_effect = delete_error
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patches[5],
        patch(f"{CONFIGURATIONS}.inline_rebalance_configurations", calls.rebalances),
        patch(f"{CONFIGURATIONS}.rebalance_configuration_uids_named", calls.portfolios),
        patch(f"{CONFIGURATIONS}.delete_rebalance_configuration_rows", calls.delete_rebalance),
    ):
        calls.rebalances.return_value = {str(CONFIGURATION_UID): SimpleNamespace(uid=REBALANCE_UID)}
        calls.portfolios.return_value = {str(REBALANCE_UID)} if named_by_another else set()
        removal = delete_provided_weights_portfolio(CONFIGURATION_UID, owner=OWNER)
    return calls, removal.summary()["rebalance_configuration"]


def test_an_inline_rebalance_configuration_is_deleted_with_its_portfolio() -> None:
    calls, rebalance = _end_with_inline_rebalance()

    # Found by the portfolio it was created with, after the configuration row is gone.
    calls.rebalances.assert_called_once_with([CONFIGURATION_UID])
    calls.portfolios.assert_called_once_with([REBALANCE_UID])
    calls.delete_rebalance.assert_called_once_with([REBALANCE_UID])
    assert rebalance == {"uid": str(REBALANCE_UID), "state": "deleted"}
    names = [entry[0] for entry in calls.mock_calls]
    assert names.index("delete") < names.index("delete_rebalance")


def test_an_inline_rebalance_configuration_another_portfolio_names_stays() -> None:
    calls, rebalance = _end_with_inline_rebalance(named_by_another=True)

    calls.delete_rebalance.assert_not_called()
    assert rebalance == {"uid": str(REBALANCE_UID), "state": "followed"}


def test_a_failed_rebalance_delete_never_fails_the_portfolio_s_delete() -> None:
    calls, rebalance = _end_with_inline_rebalance(delete_error=RuntimeError("storage unavailable"))

    calls.delete.assert_called_once_with([CONFIGURATION_UID])
    assert rebalance == {"uid": str(REBALANCE_UID), "state": "failed"}
