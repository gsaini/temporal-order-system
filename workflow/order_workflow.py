from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

# Activities must be imported through the sandbox-safe mechanism
with workflow.unsafe.imports_passed_through():
    from activities.order_activities import (
        validate_inventory,
        process_payment,
        ship_order,
        send_notification,
        refund_payment,
        restore_inventory,
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

# Delivery signal timeout — workflow won't wait forever.
DELIVERY_TIMEOUT = timedelta(days=7)


@workflow.defn
class OrderWorkflow:
    """End-to-end order processing pipeline with saga compensation.

    Flow:
      1. Validate inventory
      2. Process payment (with retries)
      3. Ship order
      4. Send "shipped" notification
      5. Wait for delivery confirmation signal (7-day timeout)
      6. Send "delivered" notification
      7. Complete

    Saga pattern:
      If a step fails, previously completed steps are compensated in reverse:
        - shipping failed  → refund payment → restore inventory
        - payment failed   → restore inventory

    Concepts covered:
      - Activities with different retry policies
      - Signals  (delivery confirmation)
      - Queries  (order status check)
      - Saga    (compensation on failure)
      - Timeouts (delivery signal deadline)
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

    # -- Compensation logic ---------------------------------------------------
    async def _compensate(self, order: Order, completed_steps: list[str]) -> None:
        """Run compensations in reverse order for all completed steps."""
        self._status.step = "COMPENSATING"
        for step in reversed(completed_steps):
            if step == "PAYMENT":
                await workflow.execute_activity(
                    refund_payment,
                    order,
                    start_to_close_timeout=DEFAULT_TIMEOUT,
                )
            elif step == "INVENTORY":
                await workflow.execute_activity(
                    restore_inventory,
                    order,
                    start_to_close_timeout=DEFAULT_TIMEOUT,
                )

    # -- Main workflow --------------------------------------------------------
    @workflow.run
    async def run(self, order: Order) -> OrderStatus:
        self._status = OrderStatus(order_id=order.order_id, step="STARTED")
        completed_steps: list[str] = []

        try:
            # Step 1 — Validate inventory
            self._status.step = "VALIDATING_INVENTORY"
            await workflow.execute_activity(
                validate_inventory,
                order,
                start_to_close_timeout=DEFAULT_TIMEOUT,
            )
            completed_steps.append("INVENTORY")

            # Step 2 — Process payment (flaky gateway → custom retry policy)
            self._status.step = "PROCESSING_PAYMENT"
            await workflow.execute_activity(
                process_payment,
                order,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=PAYMENT_RETRY,
            )
            completed_steps.append("PAYMENT")

            # Step 3 — Ship order
            self._status.step = "SHIPPING"
            await workflow.execute_activity(
                ship_order,
                order,
                start_to_close_timeout=DEFAULT_TIMEOUT,
            )
            completed_steps.append("SHIPPING")

        except ActivityError as err:
            # A step failed after exhausting retries — compensate and fail.
            workflow.logger.error(f"Order {order.order_id} failed at {self._status.step}: {err}")
            await self._compensate(order, completed_steps)

            self._status.step = "FAILED"
            self._status.error = str(err.cause) if err.cause else str(err)
            return self._status

        # Step 4 — Notify customer: "Your order has been shipped"
        self._status.step = "NOTIFYING_SHIPPED"
        await workflow.execute_activity(
            send_notification,
            args=[order, f"Order {order.order_id} has been shipped!"],
            start_to_close_timeout=DEFAULT_TIMEOUT,
        )

        # Step 5 — Wait for delivery confirmation signal (with timeout)
        #   The workflow pauses (without consuming resources) until either
        #   the signal arrives or the timeout expires.
        self._status.step = "AWAITING_DELIVERY"
        delivered = await workflow.wait_condition(
            lambda: self._delivery_confirmed,
            timeout=DELIVERY_TIMEOUT,
        )

        if not delivered:
            # Timeout — no delivery confirmation received within the deadline.
            self._status.step = "DELIVERY_TIMEOUT"
            await workflow.execute_activity(
                send_notification,
                args=[order, f"Order {order.order_id}: delivery not confirmed after 7 days. Please contact support."],
                start_to_close_timeout=DEFAULT_TIMEOUT,
            )
            self._status.error = "Delivery confirmation timed out"
            return self._status

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
