"""The order-execution routes (ADR 0016) over the shared Command Center contracts."""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch

from alpaca_connectors.execution import (
    OrderBatchPreflight,
    OrderBatchResult,
    PreTradeCheck,
    PreTradeCheckError,
)
from alpaca_connectors.execution.orders import OrderRecord

ACCOUNT_UID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
NOW = dt.datetime(2026, 10, 26, 15, 0, tzinfo=dt.timezone.utc)
SERVICES = "alpaca_connectors.api.app.services.orders"
BODY = {
    "batch_id": "HUNCH_BATCH__1",
    "orders": [
        {
            "client_order_id": "c-1",
            "asset_identifier": "ALPACA::11111111-1111-4111-8111-111111111111",
            "symbol": "AAPL",
            "side": "buy",
            "quantity": 1.5,
            "order_type": "market",
            "time_in_force": "day",
        }
    ],
}


def _result(status="submitted") -> OrderBatchResult:
    return OrderBatchResult(
        batch_id="HUNCH_BATCH__1",
        account_uid=ACCOUNT_UID,
        status=status,
        orders=[
            OrderRecord(
                "c-1",
                "ALPACA::11111111-1111-4111-8111-111111111111",
                "AAPL",
                "buy",
                1.5,
                status=status,
                broker_order_id="broker-1",
                venue_status="accepted",
                submitted_at=NOW,
                updated_at=NOW,
            )
        ],
    )


def test_submit_returns_the_batch_and_its_orders(api_client) -> None:
    with patch(f"{SERVICES}.submit_order_batch", return_value=_result()) as submit:
        response = api_client.post(f"/v1/accounts/{ACCOUNT_UID}/orders/actions/submit", json=BODY)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["batch_id"] == "HUNCH_BATCH__1" and body["accepted"] is True
    assert body["status"] == "submitted" and body["holdings_set_uid"] is None
    order = body["orders"][0]
    assert order["client_order_id"] == "c-1" and order["broker_order_id"] == "broker-1"
    assert order["status"] == "submitted" and order["filled_quantity"] == 0.0
    assert order["average_fill_price"] is None and order["submitted_at"].startswith(
        "2026-10-26T15:00"
    )
    account_uid, submission = submit.call_args.args
    assert account_uid == ACCOUNT_UID and submission.batch_id == "HUNCH_BATCH__1"
    assert submission.legs[0].quantity == 1.5 and submission.queue_when_closed is False


def test_submit_reports_failed_pre_trade_checks_as_a_structured_conflict(api_client) -> None:
    error = PreTradeCheckError([PreTradeCheck("market_closed", "The market is closed.")])
    with patch(f"{SERVICES}.submit_order_batch", side_effect=error):
        response = api_client.post(f"/v1/accounts/{ACCOUNT_UID}/orders/actions/submit", json=BODY)

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "market_closed" and detail["retryable"] is False
    assert detail["message"] == "The market is closed."
    assert detail["checks"] == [{"code": "market_closed", "message": "The market is closed."}]


def test_submit_validates_the_wire_contract(api_client) -> None:
    bad = {**BODY, "orders": [{**BODY["orders"][0], "side": "hold"}]}
    response = api_client.post(f"/v1/accounts/{ACCOUNT_UID}/orders/actions/submit", json=bad)
    assert response.status_code == 422
    limit = {**BODY, "orders": [{**BODY["orders"][0], "order_type": "limit"}]}
    response = api_client.post(f"/v1/accounts/{ACCOUNT_UID}/orders/actions/submit", json=limit)
    assert response.status_code == 422
    response = api_client.post(
        f"/v1/accounts/{ACCOUNT_UID}/orders/actions/submit", json={"batch_id": "x", "orders": []}
    )
    assert response.status_code == 422


def test_sync_and_cancel_take_the_batch_id(api_client) -> None:
    with (
        patch(f"{SERVICES}.sync_order_batch", return_value=_result("filled")) as sync,
        patch(f"{SERVICES}.cancel_order_batch", return_value=_result("cancelled")) as cancel,
    ):
        synced = api_client.post(
            f"/v1/accounts/{ACCOUNT_UID}/orders/actions/sync", json={"batch_id": "HUNCH_BATCH__1"}
        )
        cancelled = api_client.post(
            f"/v1/accounts/{ACCOUNT_UID}/orders/actions/cancel", json={"batch_id": "HUNCH_BATCH__1"}
        )

    assert synced.status_code == 200 and synced.json()["orders"][0]["status"] == "filled"
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert sync.call_args.args == (ACCOUNT_UID, "HUNCH_BATCH__1")
    assert cancel.call_args.args == (ACCOUNT_UID, "HUNCH_BATCH__1")


def test_unknown_batch_or_account_is_not_found(api_client) -> None:
    with patch(
        f"{SERVICES}.sync_order_batch", side_effect=LookupError("Order batch x does not exist.")
    ):
        response = api_client.post(
            f"/v1/accounts/{ACCOUNT_UID}/orders/actions/sync", json={"batch_id": "x"}
        )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "not_found"


def test_orders_list_uses_the_collection_contract(api_client) -> None:
    rows = [
        {
            **_result("filled").orders[0].to_payload(),
            "batch_id": "HUNCH_BATCH__1",
            "batch_status": "filled",
        }
    ]
    with patch(f"{SERVICES}.list_account_orders", return_value=(rows, 1)) as listing:
        response = api_client.get(
            f"/v1/accounts/{ACCOUNT_UID}/orders", params={"batch_id": "HUNCH_BATCH__1", "limit": 25}
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pageInfo"]["totalItems"] == 1 and body["items"][0]["client_order_id"] == "c-1"
    assert body["items"][0]["batch_id"] == "HUNCH_BATCH__1"
    assert listing.call_args.kwargs == {"batch_id": "HUNCH_BATCH__1", "limit": 25, "offset": 0}


def test_preflight_runs_the_checks_without_submitting(api_client) -> None:
    preflight = OrderBatchPreflight(
        batch_id="HUNCH_BATCH__1",
        account_uid=ACCOUNT_UID,
        orders=_result().orders,
        blockers=[
            PreTradeCheck(
                "fractional_quantity_not_allowed", "MSFT trades in whole shares only.", "c-1"
            )
        ],
    )
    with (
        patch(f"{SERVICES}.preflight_order_batch", return_value=preflight) as check,
        patch(f"{SERVICES}.submit_order_batch") as submit,
    ):
        response = api_client.post(
            f"/v1/accounts/{ACCOUNT_UID}/orders/actions/submit/preflight", json=BODY
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["contract"] == "command-center.bulk_action_preflight@v1"
    assert body["allowed"] is False and body["matched_count"] == 1
    assert body["blockers"] == ["MSFT trades in whole shares only."]
    assert body["checks"][0]["code"] == "fractional_quantity_not_allowed"
    assert check.call_args.args[0] == ACCOUNT_UID
    submit.assert_not_called()


def test_capabilities_advertise_execution_as_an_available_feature(api_client) -> None:
    response = api_client.get("/v1/project-state/capabilities")

    assert response.status_code == 200
    body = response.json()
    assert {"id": "execution", "status": "available"} in body["features"]
    assert {feature["id"] for feature in body["features"]} == {
        c["key"] for c in body["capabilities"]
    }
    execution = next(c for c in body["capabilities"] if c["key"] == "execution")
    assert execution["surfaces"] == ["api"] and "submit" in execution["actions"]
