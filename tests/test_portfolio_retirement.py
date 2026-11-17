"""Deleting a portfolio retires its ms-markets Portfolio (issue #33, ADR 0027).

A provided-weights portfolio is ended only by its owner; an ETF portfolio, which has none, by any
caller, and the CLI ends either for the operator.
"""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest
from msm.api.remote import MarketsConflictError

from alpaca_connectors.account.ownership import AccountOwner
from alpaca_connectors.portfolios import provided_weights
from alpaca_connectors.portfolios.configurations import AlpacaETFPortfolioConfiguration
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
CONFIGURATIONS = "alpaca_connectors.portfolios.configurations"
SERVICES = "alpaca_connectors.api.app.services.portfolio_configurations"


def configuration(**overrides) -> AlpacaETFPortfolioConfiguration:
    now = dt.datetime(2026, 11, 16, 9, tzinfo=dt.UTC)
    values = {
        "uid": CONFIGURATION_UID,
        "name": "Thesis basket",
        "description": None,
        "weights_source": "provided",
        "signal_configuration_uid": None,
        "owner_gateway_uid": OWNER.gateway_uid,
        "owner_user_uid": OWNER.user_uid,
        "bars_configuration_uid": uuid.uuid4(),
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
    return AlpacaETFPortfolioConfiguration.model_validate(values)


def _removal(row, *, retire=None):
    """Run the removal with Markets and storage replaced; return the recorder of their calls."""
    calls = MagicMock()
    calls.retire.side_effect = retire or (
        lambda uid: SimpleNamespace(uid=uid, retired_at=RETIRED_AT)
    )
    patches = (
        patch(f"{CONFIGURATIONS}.get_portfolio_configuration", return_value=row),
        patch("alpaca_connectors.markets_api.retire_portfolio", calls.retire),
        patch("alpaca_connectors.portfolios.signal_assets.forget_signal_assets", calls.forget),
        patch(f"{CONFIGURATIONS}.delete_portfolio_configuration_row", calls.delete),
    )
    return calls, patches


def test_the_owner_retires_the_portfolio_then_deletes_its_configuration() -> None:
    calls, patches = _removal(configuration())
    with patches[0], patches[1], patches[2], patches[3]:
        removal = delete_provided_weights_portfolio(CONFIGURATION_UID, owner=OWNER)

    # Retired first, so a refusal leaves everything as it was.
    assert calls.mock_calls == [
        call.retire(str(PORTFOLIO_UID)),
        call.forget(provided_signal_uid(CONFIGURATION_UID)),
        call.delete(CONFIGURATION_UID),
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
    }


def test_a_portfolio_an_account_targets_is_refused_and_nothing_changes() -> None:
    refusal = MarketsConflictError("Portfolio 5555 cannot be retired: 2 accounts' current target")
    calls, patches = _removal(configuration(), retire=refusal)
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        pytest.raises(ProvidedWeightsError) as raised,
    ):
        delete_provided_weights_portfolio(CONFIGURATION_UID, owner=OWNER)

    assert (raised.value.code, raised.value.status_code) == ("portfolio_in_account_target", 409)
    # Names no account and no count.
    assert "2" not in str(raised.value) and "5555" not in str(raised.value)
    calls.forget.assert_not_called()
    calls.delete.assert_not_called()


def test_only_the_owner_ends_it() -> None:
    calls, patches = _removal(configuration())
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        pytest.raises(PortfolioOwnedByAnotherError),
    ):
        delete_provided_weights_portfolio(
            CONFIGURATION_UID, owner=AccountOwner.main_sequence_user("someone-else")
        )

    assert calls.mock_calls == []


def test_a_configuration_without_a_portfolio_is_deleted_without_retiring() -> None:
    calls, patches = _removal(configuration(portfolio_uid=None))
    with patches[0], patches[1], patches[2], patches[3]:
        removal = delete_provided_weights_portfolio(CONFIGURATION_UID, owner=OWNER)

    calls.retire.assert_not_called()
    calls.delete.assert_called_once_with(CONFIGURATION_UID)
    assert removal.summary()["portfolio"]["state"] == "absent"


def test_an_etf_portfolio_is_not_ended_by_the_provided_weights_path() -> None:
    etf = configuration(
        weights_source="etf_signal",
        signal_configuration_uid=uuid.uuid4(),
        owner_gateway_uid=None,
        owner_user_uid=None,
    )
    calls, patches = _removal(etf)
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        pytest.raises(ProvidedWeightsError) as raised,
    ):
        delete_provided_weights_portfolio(CONFIGURATION_UID, owner=OWNER)

    assert raised.value.code == "not_a_provided_weights_portfolio"
    assert calls.mock_calls == []


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
def test_delete_route_retires_a_provided_weights_portfolio(api_client, local_caller) -> None:
    owned = configuration(owner_gateway_uid=local_caller.uid, owner_user_uid=local_caller.uid)
    calls, patches = _removal(owned)
    with (
        patch(f"{SERVICES}.get_portfolio_configuration", return_value=owned),
        patches[0],
        patches[1],
        patches[2],
        patches[3],
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
    ):
        response = api_client.delete(f"/v1/portfolio-configurations/{CONFIGURATION_UID}")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "portfolio_in_account_target"
    calls.delete.assert_not_called()


def etf_configuration(**overrides) -> AlpacaETFPortfolioConfiguration:
    return configuration(
        weights_source="etf_signal",
        signal_configuration_uid=uuid.uuid4(),
        owner_gateway_uid=None,
        owner_user_uid=None,
        **overrides,
    )


def test_an_etf_portfolio_is_retired_too_and_its_signal_keeps_its_assets() -> None:
    calls, patches = _removal(etf_configuration())
    with patches[0], patches[1], patches[2], patches[3]:
        removal = end_portfolio_configuration(etf_configuration())

    # ADR 0027: every delete retires; the ETF signal outlives the portfolio, so its assets stay.
    assert calls.mock_calls == [call.retire(str(PORTFOLIO_UID)), call.delete(CONFIGURATION_UID)]
    assert removal.summary()["portfolio"] == {
        "uid": str(PORTFOLIO_UID),
        "unique_identifier": "ALPACA_ETF_PORTFOLIO__11111111111141118111111111111111",
        "state": "retired",
        "retired_at": RETIRED_AT,
    }


def test_an_etf_portfolio_that_never_ran_is_absent() -> None:
    calls, patches = _removal(etf_configuration(portfolio_uid=None))
    with patches[0], patches[1], patches[2], patches[3]:
        removal = end_portfolio_configuration(etf_configuration(portfolio_uid=None))

    calls.retire.assert_not_called()
    calls.delete.assert_called_once_with(CONFIGURATION_UID)
    assert removal.summary()["portfolio"]["state"] == "absent"


def test_an_etf_portfolio_an_account_targets_is_refused_and_nothing_changes() -> None:
    refusal = MarketsConflictError("named by account targets")
    calls, patches = _removal(etf_configuration(), retire=refusal)
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        pytest.raises(ProvidedWeightsError) as raised,
    ):
        end_portfolio_configuration(etf_configuration())

    assert (raised.value.code, raised.value.status_code) == ("portfolio_in_account_target", 409)
    calls.delete.assert_not_called()


def test_delete_route_retires_an_etf_portfolio_for_any_caller(api_client) -> None:
    etf = etf_configuration()
    calls, patches = _removal(etf)
    with (
        patch(f"{SERVICES}.get_portfolio_configuration", return_value=etf),
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patch.object(provided_weights, "require_portfolio_owner") as owner_check,
    ):
        response = api_client.delete(f"/v1/portfolio-configurations/{CONFIGURATION_UID}")

    assert response.status_code == 200, response.text
    assert response.json()["portfolio"]["state"] == "retired"
    calls.retire.assert_called_once_with(str(PORTFOLIO_UID))
    calls.delete.assert_called_once_with(CONFIGURATION_UID)
    calls.forget.assert_not_called()
    # An ETF portfolio has no owner (ADR 0024, decision 6).
    owner_check.assert_not_called()


def test_delete_route_answers_409_for_an_etf_portfolio_an_account_targets(api_client) -> None:
    etf = etf_configuration()
    calls, patches = _removal(etf, retire=MarketsConflictError("named by account targets"))
    with (
        patch(f"{SERVICES}.get_portfolio_configuration", return_value=etf),
        patches[0],
        patches[1],
        patches[2],
        patches[3],
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
        agent_tools.manage_etf_portfolio(
            {
                "operation": "delete",
                "configuration_uid": str(CONFIGURATION_UID),
                "confirmation": f"DELETE {CONFIGURATION_UID}",
            }
        )

    delete.assert_called_once()
    assert delete.call_args.args == (str(CONFIGURATION_UID),)
