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
    """The ms-markets execution tables this capability reads and writes at runtime.

    They belong to the ms-markets provider (``msm_migrations:migration``), like ``AccountTable``;
    this project never migrates them, it attaches them to its runtime.
    """
    from msm.data_nodes.execution.storage import OrdersStorage, TradesStorage
    from msm.models import OrderManagerTable

    return [OrderManagerTable, OrdersStorage, TradesStorage]


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
