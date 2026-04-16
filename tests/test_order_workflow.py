"""Tests for the OrderWorkflow using Temporal's test environment.

Uses mock activities (no random failures) for deterministic testing.
The time-skipping test server fast-forwards timers automatically.
"""

import pytest
from temporalio import activity
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from model.order import Order
from workflow.order_workflow import OrderWorkflow

TASK_QUEUE = "test-order-processing"
TEST_ORDER = Order(order_id="TEST-001", item="Keyboard", amount=99.99)


# -- Mock activities (deterministic, no random failures) ----------------------

@activity.defn(name="validate_inventory")
async def mock_validate_inventory(order: Order) -> str:
    return "INVENTORY_OK"


@activity.defn(name="process_payment")
async def mock_process_payment(order: Order) -> str:
    return "PAYMENT_OK"


@activity.defn(name="ship_order")
async def mock_ship_order(order: Order) -> str:
    return "SHIPPED"


@activity.defn(name="send_notification")
async def mock_send_notification(order: Order, message: str) -> str:
    return "NOTIFIED"


@activity.defn(name="refund_payment")
async def mock_refund_payment(order: Order) -> str:
    return "REFUNDED"


@activity.defn(name="restore_inventory")
async def mock_restore_inventory(order: Order) -> str:
    return "INVENTORY_RESTORED"


# -- Mock activities that fail ------------------------------------------------

@activity.defn(name="process_payment")
async def mock_payment_always_fails(order: Order) -> str:
    raise RuntimeError("Payment gateway unavailable")


@activity.defn(name="ship_order")
async def mock_shipping_always_fails(order: Order) -> str:
    raise RuntimeError("Shipping provider down")


# -- Helpers ------------------------------------------------------------------

PASSING_ACTIVITIES = [
    mock_validate_inventory,
    mock_process_payment,
    mock_ship_order,
    mock_send_notification,
    mock_refund_payment,
    mock_restore_inventory,
]


async def run_with_activities(env: WorkflowEnvironment, activities, fn):
    """Start a worker with the given activities, run fn, then shut down."""
    async with Worker(
        env.client,
        task_queue=TASK_QUEUE,
        workflows=[OrderWorkflow],
        activities=activities,
    ):
        await fn(env.client)


# -- Tests --------------------------------------------------------------------

@pytest.mark.asyncio
async def test_happy_path():
    """Full order flow: start → signal delivery → completed."""
    async with await WorkflowEnvironment.start_time_skipping() as env:

        async def execute(client: Client):
            handle = await client.start_workflow(
                OrderWorkflow.run,
                TEST_ORDER,
                id="test-happy-path",
                task_queue=TASK_QUEUE,
            )

            # Send signal immediately — Temporal buffers it until the
            # workflow reaches the wait_condition.
            await handle.signal(OrderWorkflow.confirm_delivery)

            result = await handle.result()

            assert result.step == "COMPLETED"
            assert result.completed is True
            assert result.error == ""

        await run_with_activities(env, PASSING_ACTIVITIES, execute)


@pytest.mark.asyncio
async def test_delivery_timeout():
    """No delivery signal within 7 days → timeout with notification."""
    async with await WorkflowEnvironment.start_time_skipping() as env:

        async def execute(client: Client):
            handle = await client.start_workflow(
                OrderWorkflow.run,
                TEST_ORDER,
                id="test-delivery-timeout",
                task_queue=TASK_QUEUE,
            )

            # Don't send the signal — time-skipping fast-forwards 7 days.
            result = await handle.result()

            assert result.step == "DELIVERY_TIMEOUT"
            assert result.completed is False
            assert "timed out" in result.error

        await run_with_activities(env, PASSING_ACTIVITIES, execute)


@pytest.mark.asyncio
async def test_saga_payment_failure():
    """Payment fails after all retries → inventory is restored (saga)."""
    async with await WorkflowEnvironment.start_time_skipping() as env:

        # Swap in a payment activity that always fails
        activities = [
            mock_validate_inventory,
            mock_payment_always_fails,  # always fails
            mock_ship_order,
            mock_send_notification,
            mock_refund_payment,
            mock_restore_inventory,
        ]

        async def execute(client: Client):
            handle = await client.start_workflow(
                OrderWorkflow.run,
                TEST_ORDER,
                id="test-saga-payment",
                task_queue=TASK_QUEUE,
            )

            result = await handle.result()

            assert result.step == "FAILED"
            assert result.completed is False
            assert result.error != ""

        await run_with_activities(env, activities, execute)


@pytest.mark.asyncio
async def test_saga_shipping_failure():
    """Shipping fails → payment refunded AND inventory restored (saga)."""
    async with await WorkflowEnvironment.start_time_skipping() as env:

        # Swap in a shipping activity that always fails
        activities = [
            mock_validate_inventory,
            mock_process_payment,
            mock_shipping_always_fails,  # always fails
            mock_send_notification,
            mock_refund_payment,
            mock_restore_inventory,
        ]

        async def execute(client: Client):
            handle = await client.start_workflow(
                OrderWorkflow.run,
                TEST_ORDER,
                id="test-saga-shipping",
                task_queue=TASK_QUEUE,
            )

            result = await handle.result()

            assert result.step == "FAILED"
            assert result.completed is False
            assert result.error != ""

        await run_with_activities(env, activities, execute)
