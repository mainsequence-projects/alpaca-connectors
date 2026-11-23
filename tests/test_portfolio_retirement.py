"""Deleting a portfolio retires its ms-markets Portfolio (issue #33, ADR 0027).

Only its owner ends it through the API and the Agent; the CLI ends any portfolio for the operator.
"""

from __future__ import annotations

import datetime as dt
import uuid
from contextlib import ExitStack
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
    """Replace Markets and storage around an ending; return the recorder and the patches.

    The signal is the one revision 0029 gave the portfolio: its key is still the configuration
    UID, so its UID is ``provided_signal_uid``. Every write of the ending is one transaction
    (issue #46): ``calls.transaction`` receives its writes, in order, as ``OwnTableWrites``
    names them.
    """
    calls = MagicMock()
    calls.retire.side_effect = retire or (
        lambda uid: SimpleNamespace(uid=uid, retired_at=RETIRED_AT)
    )
    calls.followers.return_value = {}
    calls.inline.return_value = {}
    calls.named.return_value = set()
    calls.signals.side_effect = lambda signal_uids, kept=(): {
        uid: "kept" if uid in set(kept) else "deleted" for uid in signal_uids
    }

    def run(writes):
        calls.transaction(list(writes.entries))

    def retire_portfolios(uids):
        """One bulk retirement (MainSequenceMarkets#124); ``calls.retire`` sees each portfolio."""
        from alpaca_connectors.markets_api import Retirements

        calls.retire_portfolios(list(uids))
        retired, refused = {}, {}
        for uid in (str(uuid.UUID(str(uid))) for uid in uids):
            try:
                retired[uid] = calls.retire(uid)
            except MarketsConflictError as exc:
                refused[uid] = str(exc)
        return Retirements(retired=retired, missing=set(), refused=refused)

    patches = [
        patch(f"{CONFIGURATIONS}.get_portfolio_configuration", return_value=row),
        patch("alpaca_connectors.markets_api.retire_portfolios", retire_portfolios),
        patch(f"{CONFIGURATIONS}.OwnTableWrites.run", run),
        patch(f"{CONFIGURATIONS}.portfolio_counts_by_definition", calls.followers),
        patch(
            f"{DEFINITIONS}.signal_definitions_by_uids",
            return_value={str(DEFINITION_UID): DEFINITION},
        ),
        patch(f"{DEFINITIONS}.delete_signals_rows", calls.signals),
        # A shared rebalance rule: none was created with this portfolio (issue #42).
        patch(f"{CONFIGURATIONS}.inline_rebalance_configurations", calls.inline),
        patch(f"{CONFIGURATIONS}.rebalance_configuration_uids_named", calls.named),
    ]
    return calls, patches


def _entered(patches) -> ExitStack:
    stack = ExitStack()
    for item in patches:
        stack.enter_context(item)
    return stack


def _writes(calls) -> list[tuple[str, str, list]]:
    """The one transaction's writes, ``(kind, table, keys)``; an insert gives its portfolios."""
    [entries] = [recorded.args[0] for recorded in calls.transaction.call_args_list]
    return [
        (
            kind,
            table,
            [row["portfolio_uid"] for row in values] if kind == "insert" else list(values),
        )
        for kind, table, _, values in entries
    ]


def _recorded_rows(calls) -> list[dict]:
    """The records the transaction inserts, without their new uid and time."""
    [entries] = [recorded.args[0] for recorded in calls.transaction.call_args_list]
    return [
        {key: value for key, value in row.items() if key not in {"uid", "ended_at"}}
        for kind, _, _, values in entries
        if kind == "insert"
        for row in values
    ]


def test_the_owner_retires_the_portfolio_then_deletes_its_configuration() -> None:
    calls, patches = _removal(configuration())
    with _entered(patches):
        removal = delete_provided_weights_portfolio(CONFIGURATION_UID, owner=OWNER)

    # Retired first, so a refusal leaves everything as it was. Then one transaction records it
    # under its account, so the account's delete deletes the Portfolio and its signal (ADR 0028),
    # and deletes the configuration and the definition it followed (issue #38). Its signal's
    # rows stay with the retired Portfolio, which the record just written keeps: not read again.
    names = [name for name, *_ in calls.mock_calls]
    assert names[:2] == ["retire_portfolios", "retire"]
    assert names.index("transaction") < names.index("signals")
    assert _writes(calls) == [
        ("insert", "EndedPortfolioTable", [PORTFOLIO_UID]),
        ("delete", "AlpacaPortfolioConfigurationTable", [CONFIGURATION_UID]),
        ("delete", "SignalDefinitionTable", [DEFINITION_UID]),
    ]
    assert _recorded_rows(calls) == [
        {
            "account_uid": ACCOUNT_UID,
            "portfolio_uid": PORTFOLIO_UID,
            "portfolio_configuration_uid": CONFIGURATION_UID,
            "signal_uid": provided_signal_uid(CONFIGURATION_UID),
        }
    ]
    # Its followers are counted without the configuration being deleted, before the write.
    calls.followers.assert_called_once_with([str(DEFINITION_UID)], excluding=[CONFIGURATION_UID])
    calls.signals.assert_called_once_with(
        [provided_signal_uid(CONFIGURATION_UID)],
        kept=[provided_signal_uid(CONFIGURATION_UID)],
    )
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
    calls.followers.return_value = {str(DEFINITION_UID): 1}
    with _entered(patches):
        removal = delete_provided_weights_portfolio(CONFIGURATION_UID, owner=OWNER)

    assert ("delete", "SignalDefinitionTable", [DEFINITION_UID]) not in _writes(calls)
    assert removal.summary()["signal_definition"] == {
        "uid": str(DEFINITION_UID),
        "state": "followed",
    }
    assert removal.summary()["signal"] is None


def test_a_failed_write_raises_and_a_retry_can_finish_it() -> None:
    # Every write is one transaction: when it fails, nothing of it is stored, the configuration
    # is still there, and a retry retires the retired Portfolio again, which does nothing.
    calls, patches = _removal(configuration())
    calls.transaction.side_effect = ConnectionError("MetaTables is unavailable")
    with _entered(patches), pytest.raises(ConnectionError):
        delete_provided_weights_portfolio(CONFIGURATION_UID, owner=OWNER)

    calls.retire.assert_called_once_with(str(PORTFOLIO_UID))
    calls.signals.assert_not_called()


def test_a_portfolio_not_yet_adopted_has_no_definition_to_delete() -> None:
    calls, patches = _removal(
        configuration(account_uid=None, signal_definition_uid=None, bars_configuration_uid=None)
    )
    with _entered(patches):
        removal = delete_provided_weights_portfolio(CONFIGURATION_UID, owner=OWNER)

    assert [table for _, table, _ in _writes(calls)] == ["AlpacaPortfolioConfigurationTable"]
    assert removal.summary()["signal_definition"] is None


def test_a_portfolio_an_account_targets_is_refused_and_nothing_changes() -> None:
    refusal = MarketsConflictError("Portfolio 5555 cannot be retired: 2 accounts' current target")
    calls, patches = _removal(configuration(), retire=refusal)
    with _entered(patches), pytest.raises(ProvidedWeightsError) as raised:
        delete_provided_weights_portfolio(CONFIGURATION_UID, owner=OWNER)

    assert (raised.value.code, raised.value.status_code) == ("portfolio_in_account_target", 409)
    # Names no account and no count.
    assert "2" not in str(raised.value) and "5555" not in str(raised.value)
    calls.transaction.assert_not_called()


def test_several_portfolios_end_together_and_a_refusal_keeps_only_that_one() -> None:
    # The account delete ends its portfolios in one pass (issue #46): retiring is one call per
    # portfolio, made together, and every write one transaction for all. A refusal keeps that
    # portfolio unchanged; every other one ends, and the refusal is raised after.
    from alpaca_connectors.portfolios.configurations import OwnTableWrites
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
    # The refused portfolio still follows the shared definition, so the definition stays.
    calls.followers.return_value = {str(DEFINITION_UID): 1}
    callers_own = OwnTableWrites().delete_in(SimpleNamespace(__name__="Caller"), "uid", ["x"])
    with _entered(patches), pytest.raises(ProvidedWeightsError) as raised:
        end_portfolio_configurations([first, held, later], writes=callers_own)

    assert raised.value.code == "portfolio_in_account_target"
    # Every retire is attempted; they run together, so in no particular order.
    assert sorted(retired.args[0] for retired in calls.retire.call_args_list) == sorted(
        str(row.portfolio_uid) for row in (first, held, later)
    )
    # The caller's own writes go only when every portfolio ended: here they stay out.
    assert _writes(calls) == [
        ("insert", "EndedPortfolioTable", [PORTFOLIO_UID, later.portfolio_uid]),
        ("delete", "AlpacaPortfolioConfigurationTable", [CONFIGURATION_UID, later.uid]),
    ]
    calls.followers.assert_called_once_with(
        [str(DEFINITION_UID)], excluding=[CONFIGURATION_UID, later.uid]
    )


def test_the_callers_writes_join_the_transaction_when_every_portfolio_ends() -> None:
    from alpaca_connectors.portfolios.configurations import OwnTableWrites
    from alpaca_connectors.portfolios.provided_weights import end_portfolio_configurations

    calls, patches = _removal(configuration())
    callers_own = OwnTableWrites().delete_in(SimpleNamespace(__name__="Caller"), "uid", ["x"])
    with _entered(patches):
        end_portfolio_configurations(
            [configuration()], definitions=[DEFINITION], writes=callers_own
        )

    # First, in the same transaction; the definitions the caller passed are not read again.
    assert _writes(calls)[0] == ("delete", "Caller", ["x"])
    assert len(calls.transaction.call_args_list) == 1


def test_only_the_owner_ends_it() -> None:
    calls, patches = _removal(configuration())
    with _entered(patches), pytest.raises(PortfolioOwnedByAnotherError):
        delete_provided_weights_portfolio(
            CONFIGURATION_UID, owner=AccountOwner.main_sequence_user("someone-else")
        )

    assert calls.mock_calls == []


def test_a_configuration_without_a_portfolio_is_deleted_without_retiring() -> None:
    calls, patches = _removal(configuration(portfolio_uid=None))
    with _entered(patches):
        removal = delete_provided_weights_portfolio(CONFIGURATION_UID, owner=OWNER)

    calls.retire.assert_not_called()
    assert [kind for kind, *_ in _writes(calls)] == ["delete", "delete"]
    assert ("delete", "AlpacaPortfolioConfigurationTable", [CONFIGURATION_UID]) in _writes(calls)
    assert removal.summary()["portfolio"]["state"] == "absent"


def test_the_retire_composite_retires_them_in_one_call() -> None:
    from alpaca_connectors import markets_api

    held, missing = uuid.UUID(int=2), uuid.UUID(int=3)
    runtime = SimpleNamespace(client=MagicMock())
    runtime.client.bulk_retire_portfolios.return_value = SimpleNamespace(
        retired=[SimpleNamespace(uid=str(PORTFOLIO_UID), retired_at=RETIRED_AT)],
        failed=[
            SimpleNamespace(uid=str(held), detail=f"Portfolio {held} cannot be retired: ..."),
            SimpleNamespace(uid=str(missing), detail=f"Portfolio {missing} was not found."),
        ],
    )
    with patch("alpaca_connectors.runtime.use_markets_api", return_value=runtime):
        retirement = markets_api.retire_portfolios([PORTFOLIO_UID, held, missing])

    # One bulkRetirePortfolios for all of them (MainSequenceMarkets#124).
    runtime.client.bulk_retire_portfolios.assert_called_once_with(
        [str(PORTFOLIO_UID), str(held), str(missing)]
    )
    assert list(retirement.retired) == [str(PORTFOLIO_UID)]
    assert retirement.missing == {str(missing)}
    assert list(retirement.refused) == [str(held)]


def test_a_portfolio_markets_no_longer_has_ends_as_absent() -> None:
    from alpaca_connectors.markets_api import Retirements
    from alpaca_connectors.portfolios.provided_weights import end_portfolio_configurations

    calls, patches = _removal(configuration())
    gone = Retirements(retired={}, missing={str(PORTFOLIO_UID)}, refused={})
    with (
        _entered(patches),
        patch("alpaca_connectors.markets_api.retire_portfolios", return_value=gone),
    ):
        [removal] = end_portfolio_configurations([configuration()])

    # Nothing to record: the configuration and its definition go, as for one without a Portfolio.
    assert removal.portfolio_state == "absent"
    assert [table for _, table, _ in _writes(calls)] == [
        "AlpacaPortfolioConfigurationTable",
        "SignalDefinitionTable",
    ]


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
    with patch(f"{SERVICES}.get_portfolio_configuration", return_value=owned), _entered(patches):
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
    with patch(f"{SERVICES}.get_portfolio_configuration", return_value=owned), _entered(patches):
        response = api_client.delete(f"/v1/portfolio-configurations/{CONFIGURATION_UID}")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "portfolio_in_account_target"
    calls.transaction.assert_not_called()


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
        _entered(patches),
    ):
        assert (
            run_delete_command(Namespace(configuration_uid=str(CONFIGURATION_UID), execute=True))
            == 0
        )

    calls.retire.assert_called_once_with(str(PORTFOLIO_UID))
    assert [table for _, table, _ in _writes(calls)] == [
        "EndedPortfolioTable",
        "AlpacaPortfolioConfigurationTable",
        "SignalDefinitionTable",
    ]


# ------------------------------------------------------------------------------------------------
# A rebalance configuration created inline with the portfolio goes with it (issue #42)
# ------------------------------------------------------------------------------------------------
REBALANCE_UID = uuid.UUID("88888888-8888-4888-8888-888888888888")


def _end_with_inline_rebalance(*, named_by_another: bool = False):
    calls, patches = _removal(configuration())
    calls.inline.return_value = {str(CONFIGURATION_UID): SimpleNamespace(uid=REBALANCE_UID)}
    calls.named.return_value = {str(REBALANCE_UID)} if named_by_another else set()
    with _entered(patches):
        removal = delete_provided_weights_portfolio(CONFIGURATION_UID, owner=OWNER)
    return calls, removal.summary()["rebalance_configuration"]


def test_an_inline_rebalance_configuration_is_deleted_with_its_portfolio() -> None:
    calls, rebalance = _end_with_inline_rebalance()

    # Found by the portfolio it was created with; whether another portfolio names it is read
    # without that portfolio, before the transaction that deletes both.
    calls.inline.assert_called_once_with([CONFIGURATION_UID])
    calls.named.assert_called_once_with([REBALANCE_UID], excluding=[CONFIGURATION_UID])
    writes = _writes(calls)
    rule = ("delete", "PortfolioRebalanceConfigurationTable", [REBALANCE_UID])
    assert rule in writes
    assert writes.index(
        ("delete", "AlpacaPortfolioConfigurationTable", [CONFIGURATION_UID])
    ) < writes.index(rule)
    assert rebalance == {"uid": str(REBALANCE_UID), "state": "deleted"}


def test_an_inline_rebalance_configuration_another_portfolio_names_stays() -> None:
    calls, rebalance = _end_with_inline_rebalance(named_by_another=True)

    assert "PortfolioRebalanceConfigurationTable" not in [t for _, t, _ in _writes(calls)]
    assert rebalance == {"uid": str(REBALANCE_UID), "state": "followed"}
