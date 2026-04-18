"""Domain models for the order processing pipeline.

These dataclasses are serialized by Temporal and passed between the workflow
and its activities. Keep them JSON-serializable and backward-compatible with
running workflow histories.
"""

from dataclasses import dataclass


@dataclass
class Order:
    """Represents an e-commerce order flowing through the pipeline.

    Attributes:
        order_id: Unique identifier for the order. Also used to derive the
            Temporal workflow ID (``order-{order_id}``).
        item: Human-readable name of the item being purchased.
        amount: Total charge amount in the account's currency.
        customer_id: Identifier of the purchasing customer. Defaults to
            ``CUST-DEFAULT`` when not supplied by the caller.
    """

    order_id: str
    item: str
    amount: float
    customer_id: str = "CUST-DEFAULT"


@dataclass
class OrderStatus:
    """Tracks the current state of an order workflow.

    Returned by the workflow's ``get_status`` query and as the final result
    of the workflow run.

    Attributes:
        order_id: The order this status refers to.
        step: Name of the current (or final) pipeline step — e.g.
            ``VALIDATING_INVENTORY``, ``PROCESSING_PAYMENT``, ``COMPLETED``,
            ``FAILED``, ``DELIVERY_TIMEOUT``.
        completed: ``True`` once the workflow has successfully reached the
            end of the pipeline; ``False`` while in progress or on failure.
        error: Human-readable error message when the workflow failed or
            timed out; empty string on the happy path.
    """

    order_id: str
    step: str
    completed: bool = False
    error: str = ""
