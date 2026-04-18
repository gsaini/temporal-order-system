"""Workflow definition for the order processing pipeline.

The :class:`OrderWorkflow` orchestrates inventory validation, payment,
shipping, and delivery confirmation for a single order. It demonstrates the
core Temporal concepts: activities, per-activity retry policies, signals,
queries, timeouts, and the saga compensation pattern.

Workflow code must be deterministic on replay, so activity implementations
are imported through :func:`workflow.unsafe.imports_passed_through` to bypass
the Temporal sandbox.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

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


DEFAULT_TIMEOUT = timedelta(seconds=10)
"""Start-to-close timeout applied to most activities."""

PAYMENT_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
)
"""Retry policy for the payment activity.

The payment gateway is intentionally flaky (40% transient failure rate) so
the workflow uses an exponential-backoff policy with five attempts before
giving up and triggering compensation.
"""

DELIVERY_TIMEOUT = timedelta(days=7)
"""Maximum time the workflow will wait for a delivery confirmation signal."""


@workflow.defn
class OrderWorkflow:
    """End-to-end order processing pipeline with saga compensation.

    Flow:
        1. Validate inventory.
        2. Process payment (with the ``PAYMENT_RETRY`` policy).
        3. Ship order.
        4. Send a "shipped" notification.
        5. Wait up to ``DELIVERY_TIMEOUT`` for a delivery confirmation signal.
        6. Send a "delivered" notification.
        7. Complete.

    Saga pattern:
        When a forward step fails, previously completed steps are compensated
        in reverse order:

            * Shipping failed   → refund payment → restore inventory
            * Payment failed    → restore inventory
            * Inventory failed  → (no compensation needed)

    Concepts exercised:
        * Activities with per-call timeouts and retry policies
        * Signals (``confirm_delivery``) and queries (``get_status``)
        * Saga-style compensation on failure
        * Signal timeouts via :func:`workflow.wait_condition`
    """

    def __init__(self) -> None:
        """Initialize per-instance workflow state.

        Called once when the workflow starts. The ``order_id`` placeholder is
        overwritten by :meth:`run` as soon as the order argument is received.
        """
        self._status = OrderStatus(order_id="", step="INITIALIZED")
        self._delivery_confirmed = False

    @workflow.query
    def get_status(self) -> OrderStatus:
        """Return the current order status.

        Queries are read-only and may be invoked by clients at any time
        during workflow execution, including after it has completed.

        Returns:
            A snapshot of the workflow's current :class:`OrderStatus`.
        """
        return self._status

    @workflow.signal
    async def confirm_delivery(self) -> None:
        """Mark the order as delivered.

        Signals are fire-and-forget messages from external clients. The
        workflow's main loop observes the ``_delivery_confirmed`` flag via
        :func:`workflow.wait_condition` and proceeds once this signal is
        received.
        """
        self._delivery_confirmed = True

    async def _compensate(self, order: Order, completed_steps: list[str]) -> None:
        """Undo previously successful steps in reverse order.

        Implements the saga compensation path. For each step that was
        successfully completed before the failure, the corresponding
        compensating activity is invoked.

        Args:
            order: The order whose state should be rolled back.
            completed_steps: Ordered list of step identifiers that had
                completed successfully before the failure. Expected values:
                ``"INVENTORY"``, ``"PAYMENT"``, ``"SHIPPING"``. ``"SHIPPING"``
                has no compensating activity in this demo.
        """
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

    @workflow.run
    async def run(self, order: Order) -> OrderStatus:
        """Execute the full order pipeline.

        This is the workflow's entry point, invoked by the Temporal SDK when
        a client calls ``start_workflow``. It drives the pipeline forward,
        records progress in ``self._status`` so that the ``get_status`` query
        can observe it, and runs the compensation path when any forward
        activity ultimately fails.

        Args:
            order: The order to process.

        Returns:
            The terminal :class:`OrderStatus`. Possible terminal ``step``
            values are:

                * ``"COMPLETED"``        — happy path, ``completed=True``.
                * ``"FAILED"``           — a forward activity failed after
                  retries; compensations have been run and ``error`` is set.
                * ``"DELIVERY_TIMEOUT"`` — the delivery signal did not arrive
                  within :data:`DELIVERY_TIMEOUT`; ``error`` is set.
        """
        self._status = OrderStatus(order_id=order.order_id, step="STARTED")
        completed_steps: list[str] = []

        try:
            self._status.step = "VALIDATING_INVENTORY"
            await workflow.execute_activity(
                validate_inventory,
                order,
                start_to_close_timeout=DEFAULT_TIMEOUT,
            )
            completed_steps.append("INVENTORY")

            self._status.step = "PROCESSING_PAYMENT"
            await workflow.execute_activity(
                process_payment,
                order,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=PAYMENT_RETRY,
            )
            completed_steps.append("PAYMENT")

            self._status.step = "SHIPPING"
            await workflow.execute_activity(
                ship_order,
                order,
                start_to_close_timeout=DEFAULT_TIMEOUT,
            )
            completed_steps.append("SHIPPING")

        except ActivityError as err:
            workflow.logger.error(f"Order {order.order_id} failed at {self._status.step}: {err}")
            await self._compensate(order, completed_steps)

            self._status.step = "FAILED"
            self._status.error = str(err.cause) if err.cause else str(err)
            return self._status

        self._status.step = "NOTIFYING_SHIPPED"
        await workflow.execute_activity(
            send_notification,
            args=[order, f"Order {order.order_id} has been shipped!"],
            start_to_close_timeout=DEFAULT_TIMEOUT,
        )

        self._status.step = "AWAITING_DELIVERY"
        delivered = await workflow.wait_condition(
            lambda: self._delivery_confirmed,
            timeout=DELIVERY_TIMEOUT,
        )

        if not delivered:
            self._status.step = "DELIVERY_TIMEOUT"
            await workflow.execute_activity(
                send_notification,
                args=[order, f"Order {order.order_id}: delivery not confirmed after 7 days. Please contact support."],
                start_to_close_timeout=DEFAULT_TIMEOUT,
            )
            self._status.error = "Delivery confirmation timed out"
            return self._status

        self._status.step = "NOTIFYING_DELIVERED"
        await workflow.execute_activity(
            send_notification,
            args=[order, f"Order {order.order_id} has been delivered!"],
            start_to_close_timeout=DEFAULT_TIMEOUT,
        )

        self._status.step = "COMPLETED"
        self._status.completed = True
        return self._status
