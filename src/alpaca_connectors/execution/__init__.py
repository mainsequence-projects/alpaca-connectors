"""Order execution on registered Alpaca accounts for authorised applications (ADR 0016).

Import the capability from here. ``orders`` holds the pure contract and ledger-row logic;
``services`` places the orders with the account's stored credentials and keeps the ms-markets
execution ledger (``OrderManager``, ``OrdersTS``, ``TradesTS``).
"""

from .orders import (
    ORDER_STATUSES,
    TERMINAL_ORDER_STATUSES,
    OrderBatchSubmission,
    OrderLegSpec,
    OrderRecord,
    PreTradeCheck,
    PreTradeCheckError,
    batch_status,
    map_alpaca_order_status,
)
from .services import (
    ExecutionDependencies,
    ExecutionLedger,
    MarketsExecutionLedger,
    OrderBatchPreflight,
    OrderBatchResult,
    cancel_order_batch,
    list_account_orders,
    preflight_order_batch,
    submit_order_batch,
    sync_order_batch,
)


def execution_runtime_models() -> list[type]:
    """The execution tables this capability writes at runtime: its orders and trades instances.

    Orders and trades live in this application's ``OrdersTS`` / ``TradesTS`` instances
    (``alpaca_connectors.instances``, ms-markets ADR 0047), migrated by its own provider. Order
    managers are Markets registry rows reached through the Markets API, so no Markets table is
    attached.
    """
    from alpaca_connectors.instances import execution_instance_models

    return execution_instance_models()


__all__ = [
    "ORDER_STATUSES",
    "TERMINAL_ORDER_STATUSES",
    "ExecutionDependencies",
    "ExecutionLedger",
    "MarketsExecutionLedger",
    "OrderBatchPreflight",
    "OrderBatchResult",
    "OrderBatchSubmission",
    "OrderLegSpec",
    "OrderRecord",
    "PreTradeCheck",
    "PreTradeCheckError",
    "batch_status",
    "cancel_order_batch",
    "execution_runtime_models",
    "list_account_orders",
    "map_alpaca_order_status",
    "preflight_order_batch",
    "submit_order_batch",
    "sync_order_batch",
]
