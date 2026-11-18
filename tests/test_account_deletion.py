"""Deleting an account deletes everything that belongs to it (ADR 0028, decision 8).

ms-markets is replaced at the ``markets_api`` composites: what this connector controls is the order
of its calls, which the ``RESTRICT`` keys of ms-markets impose.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest
from msm.api.remote import MarketsConflictError

from alpaca_connectors.account import deletion
from alpaca_connectors.account.deletion import AccountDeleteRefused, delete_account

ACCOUNT_UID = str(uuid.UUID("55555555-5555-4555-8555-555555555555"))
OTHER_ACCOUNT = str(uuid.UUID("66666666-6666-4666-8666-666666666666"))
PORTFOLIO = uuid.UUID(int=10)
KEPT_PORTFOLIO = uuid.UUID(int=11)
CONFIGURATION = uuid.UUID(int=20)
KEPT_CONFIGURATION = uuid.UUID(int=21)
MARKETS = "alpaca_connectors.markets_api"
CONFIGURATIONS = "alpaca_connectors.portfolios.configurations"
DEFINITIONS = "alpaca_connectors.portfolios.signal_definitions"


def _record(portfolio, configuration, signal):
    return SimpleNamespace(
        uid=uuid.uuid4(),
        portfolio_uid=portfolio,
        portfolio_configuration_uid=configuration,
        signal_uid=signal,
    )


def _run(
    *,
    preflight_blockers=(),
    held_by=(),
    refuse_markets=False,
    registration=True,
    registration_values=None,
):
    """Delete the account with Markets and storage replaced; return the recorder."""
    calls = MagicMock()
    calls.secrets.return_value = (["ALPACA_CONNECTORS__API_KEY"], [])
    portfolio = SimpleNamespace(uid=CONFIGURATION, portfolio_uid=PORTFOLIO)
    definitions = [
        SimpleNamespace(uid="definition-followed", signal_key="followed"),
        SimpleNamespace(uid="definition-free", signal_key="free"),
    ]
    records = [
        _record(PORTFOLIO, CONFIGURATION, "signal-followed"),
        _record(KEPT_PORTFOLIO, KEPT_CONFIGURATION, "signal-kept"),
    ]
    calls.markets_delete.side_effect = (
        MarketsConflictError("refused by a RESTRICT key") if refuse_markets else None
    )
    calls.blockers.side_effect = lambda uid: ["held"] if uid == str(KEPT_PORTFOLIO) else []
    kept_signals = {"signal-followed", "signal-kept"}
    with (
        patch("alpaca_connectors.runtime.start_markets_engine", return_value=SimpleNamespace()),
        patch(
            "alpaca_connectors.account.services.get_account_registration",
            return_value=(
                {"account_unique_identifier": "ACCOUNT__ALPACA", **(registration_values or {})}
                if registration
                else None
            ),
        ),
        patch(
            "alpaca_connectors.account.legacy_secrets.delete_legacy_managed_secrets",
            calls.secrets,
        ),
        patch(
            f"{MARKETS}.account_delete_preflight",
            return_value={"blockers": list(preflight_blockers), "warnings": []},
        ),
        patch(f"{MARKETS}.current_target_account_uids", return_value=list(held_by)),
        patch(
            f"{MARKETS}.account_target_positions",
            return_value=SimpleNamespace(positions=[object()]),
        ),
        patch(f"{MARKETS}.clear_account_target", calls.clear_target),
        patch(f"{MARKETS}.delete_account", calls.markets_delete),
        patch(f"{MARKETS}.portfolio_delete_blockers", calls.blockers),
        patch(f"{MARKETS}.delete_portfolio", calls.delete_portfolio),
        patch(f"{CONFIGURATIONS}.portfolio_configurations_of_account", return_value=[portfolio]),
        patch(f"{CONFIGURATIONS}.ended_portfolios_of_account", return_value=records),
        patch(f"{CONFIGURATIONS}.forget_ended_portfolio", calls.forget_record),
        patch(
            "alpaca_connectors.market_data.configurations.bar_configurations_for_account",
            return_value=[object()],
        ),
        patch.object(deletion, "_account_definitions", return_value=definitions),
        patch(
            f"{DEFINITIONS}.definition_signal_uid",
            side_effect=lambda definition: f"signal-{definition.signal_key}",
        ),
        patch(
            f"{DEFINITIONS}.signal_kept_by_ended_portfolio",
            side_effect=lambda signal: signal in kept_signals,
        ),
        patch(
            f"{DEFINITIONS}.delete_signal_rows",
            side_effect=lambda signal: (
                calls.delete_signal(signal),
                "kept" if signal == "signal-kept" else "deleted",
            )[1],
        ),
        patch(
            "alpaca_connectors.portfolios.provided_weights.end_portfolio_configuration",
            calls.end_portfolio,
        ),
        patch.object(deletion, "delete_account_orders_and_trades", calls.orders),
        patch.object(deletion, "delete_portfolio_series", calls.series),
    ):
        result = delete_account(ACCOUNT_UID)
    return result, calls


def test_the_account_and_all_it_owns_go_in_the_order_markets_keys_impose() -> None:
    result, calls = _run()
    order = [name for name, *_ in calls.mock_calls]

    # Its own target first, so retiring its portfolios is not refused; then each portfolio ends.
    assert order.index("clear_target") < order.index("end_portfolio")
    calls.end_portfolio.assert_called_once()
    # A signal no retired Portfolio follows goes before the account; the followed one waits.
    assert call.delete_signal("signal-free") in calls.mock_calls
    assert order.index("delete_signal") < order.index("markets_delete")
    # Orders and trades in this application's instances refuse the account delete: first.
    calls.orders.assert_called_once_with("ACCOUNT__ALPACA")
    assert order.index("orders") < order.index("markets_delete")
    calls.markets_delete.assert_called_once_with(ACCOUNT_UID)
    # A retired Portfolio nothing else names: its series, then the Portfolio, then its record.
    calls.series.assert_called_once_with("ALPACA_PORTFOLIO__" + CONFIGURATION.hex.upper())
    calls.delete_portfolio.assert_called_once_with(str(PORTFOLIO))
    assert order.index("series") < order.index("delete_portfolio")
    assert order.index("markets_delete") < order.index("series")
    # Another account's history keeps KEPT_PORTFOLIO, and so its signal.
    assert result.kept_portfolio_uids == [str(KEPT_PORTFOLIO)]
    assert result.deleted_portfolio_uids == [str(PORTFOLIO)]
    assert "signal-kept" in result.kept_signal_uids
    assert {"signal-free", "signal-followed"} <= set(result.deleted_signal_uids)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"preflight_blockers": ["Other accounts' orders use its order managers."]}, "orders"),
        ({"held_by": [OTHER_ACCOUNT]}, "another account's current target"),
    ],
)
def test_a_refused_delete_changes_nothing(kwargs, message) -> None:
    with pytest.raises(AccountDeleteRefused) as refused:
        _run(**kwargs)
    assert message in str(refused.value)
    # Another account is never named (ADR 0019).
    assert OTHER_ACCOUNT not in str(refused.value)


def test_the_accounts_own_target_never_blocks_its_delete() -> None:
    # Its own current target names its portfolios; only another account's refuses.
    result, calls = _run(held_by=[ACCOUNT_UID])
    calls.markets_delete.assert_called_once()
    assert result.deleted_portfolio_uids


def test_a_retry_after_the_account_is_gone_finishes_its_portfolios_and_signals() -> None:
    result, calls = _run(registration=False)
    calls.end_portfolio.assert_not_called()
    calls.markets_delete.assert_not_called()
    calls.delete_portfolio.assert_called_once_with(str(PORTFOLIO))
    assert "signal-followed" in result.deleted_signal_uids


def test_a_registration_from_before_encrypted_keys_takes_its_own_secrets_with_it() -> None:
    # Removing the keys deletes the Secrets this application created; deleting does too.
    legacy = {
        "api_key_secret_name": "ALPACA_CONNECTORS__API_KEY",
        "secret_key_secret_name": "ALPACA_CONNECTORS__SECRET_KEY",
        "credential_source": "managed",
    }
    result, calls = _run(registration_values=legacy)
    order = [name for name, *_ in calls.mock_calls]

    calls.secrets.assert_called_once()
    assert calls.secrets.call_args.args[0].api_key_secret_name == "ALPACA_CONNECTORS__API_KEY"
    # Only once the account is gone: a refused delete keeps the keys it runs with.
    assert order.index("markets_delete") < order.index("secrets")
    assert result.summary()["deleted_secrets"] == ["ALPACA_CONNECTORS__API_KEY"]


def test_a_migrated_registration_names_no_secret_to_delete() -> None:
    result, calls = _run()
    calls.secrets.assert_not_called()
    assert result.summary()["deleted_secrets"] == []
    assert result.summary()["warnings"] == []


def test_markets_refusing_the_account_delete_is_reported() -> None:
    with pytest.raises(AccountDeleteRefused):
        _run(refuse_markets=True)


# ------------------------------------------------------------------------------------------------
# The Markets calls
# ------------------------------------------------------------------------------------------------
def test_the_preflight_and_the_signal_delete_call_their_ms_markets_routes() -> None:
    from alpaca_connectors import markets_api

    client = MagicMock()
    client.request.return_value = {"blockers": []}
    rows = [SimpleNamespace(uid="metadata-row")]
    with (
        patch(
            "alpaca_connectors.runtime.use_markets_api", return_value=SimpleNamespace(client=client)
        ),
        patch("msm_portfolios.api.market_metadata.SignalMetadata.filter", return_value=rows),
        patch("msm_portfolios.api.datasets.PortfolioDatasets.unregister_signal") as unbind,
    ):
        markets_api.account_delete_preflight(ACCOUNT_UID)
        markets_api.delete_signal_metadata("signal-a")

    selection = {"selection": {"mode": "explicit", "uids": [ACCOUNT_UID]}, "options": {}}
    assert client.request.call_args_list == [
        call("POST", "/api/v1/account/bulk-delete/preflight/", json=selection),
        call("DELETE", "/api/v1/portfolio-signal/metadata-row/"),
    ]
    unbind.assert_called_once_with(signal_uid="signal-a")


def test_current_targets_are_read_in_pages() -> None:
    from alpaca_connectors import markets_api

    def page(items, more):
        return SimpleNamespace(
            items=[SimpleNamespace(account_uid=uid) for uid in items],
            page_info=SimpleNamespace(has_next_page=more),
        )

    client = MagicMock()
    client.portfolio_account_targets.side_effect = [
        page([ACCOUNT_UID], True),
        page([OTHER_ACCOUNT], False),
    ]
    with patch(
        "alpaca_connectors.runtime.use_markets_api", return_value=SimpleNamespace(client=client)
    ):
        assert markets_api.current_target_account_uids("p") == [ACCOUNT_UID, OTHER_ACCOUNT]
    assert client.portfolio_account_targets.call_args_list == [
        call("p", limit=markets_api.MARKETS_BATCH_SIZE, offset=0),
        call("p", limit=markets_api.MARKETS_BATCH_SIZE, offset=markets_api.MARKETS_BATCH_SIZE),
    ]
