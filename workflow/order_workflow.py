from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

# Activities must be imported through the sandbox-safe mechanism
with workflow.unsafe.imports_passed_through():
    from activities.order_activities import (
        validate_inventory,
        process_payment,
        ship_order,
        send_notification,
    )
    from model.order import Order, OrderStatus


# -- Shared activity config --------------------------------------------------
# Most activities get a simple timeout + default retry.
DEFAULT_TIMEOUT = timedelta(seconds=10)

# Payment gets a generous retry policy because the gateway is flaky.
PAYMENT_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
)


@workflow.defn
class OrderWorkflow:
    """End-to-end order processing pipeline.

    Flow:
      1. Validate inventory
      2. Process payment (with retries)
      3. Ship order
      4. Send "shipped" notification
      5. Wait for delivery confirmation signal
      6. Send "delivered" notification
      7. Complete

    Concepts covered:
      - Activities with different retry policies
      - Signals  (delivery confirmation)
      - Queries  (order status check)
    """

    def __init__(self) -> None:
        self._status = OrderStatus(order_id="", step="INITIALIZED")
        self._delivery_confirmed = False

    # -- Query handler --------------------------------------------------------
    @workflow.query
    def get_status(self) -> OrderStatus:
        """Query the current order status at any point during execution."""
        return self._status

    # -- Signal handler -------------------------------------------------------
    @workflow.signal
    async def confirm_delivery(self) -> None:
        """Signal that the package has been delivered."""
        self._delivery_confirmed = True

    # -- Main workflow --------------------------------------------------------
    @workflow.run
    async def run(self, order: Order) -> OrderStatus:
        self._status = OrderStatus(order_id=order.order_id, step="STARTED")

        # Step 1 — Validate inventory
        self._status.step = "VALIDATING_INVENTORY"
        await workflow.execute_activity(
            validate_inventory,
            order,
            start_to_close_timeout=DEFAULT_TIMEOUT,
        )

        # Step 2 — Process payment (flaky gateway → custom retry policy)
        self._status.step = "PROCESSING_PAYMENT"
        await workflow.execute_activity(
            process_payment,
            order,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=PAYMENT_RETRY,
        )

        # Step 3 — Ship order
        self._status.step = "SHIPPING"
        await workflow.execute_activity(
            ship_order,
            order,
            start_to_close_timeout=DEFAULT_TIMEOUT,
        )

        # Step 4 — Notify customer: "Your order has been shipped"
        self._status.step = "NOTIFYING_SHIPPED"
        await workflow.execute_activity(
            send_notification,
            args=[order, f"Order {order.order_id} has been shipped!"],
            start_to_close_timeout=DEFAULT_TIMEOUT,
        )

        # Step 5 — Wait for delivery confirmation signal
        #   This is the key Temporal concept here: the workflow *pauses*
        #   (without consuming resources) until an external signal arrives.
        self._status.step = "AWAITING_DELIVERY"
        await workflow.wait_condition(lambda: self._delivery_confirmed)

        # Step 6 — Notify customer: "Delivered!"
        self._status.step = "NOTIFYING_DELIVERED"
        await workflow.execute_activity(
            send_notification,
            args=[order, f"Order {order.order_id} has been delivered!"],
            start_to_close_timeout=DEFAULT_TIMEOUT,
        )

        # Done
        self._status.step = "COMPLETED"
        self._status.completed = True
        return self._status
