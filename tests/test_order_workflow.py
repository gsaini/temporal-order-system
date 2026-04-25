"""Tests for the :class:`OrderWorkflow` using Temporal's test environment.

These tests run the real workflow code against deterministic mock activities
so outcomes are reproducible. They use the time-skipping test server
(:meth:`WorkflowEnvironment.start_time_skipping`), which fast-forwards timers
(for example, the 7-day delivery timeout) to keep the suite fast.

Each activity has two variants:

    * Passing mocks — succeed immediately, no randomness.
    * Failing mocks — always raise, to exercise the saga compensation path.

Mocks are registered under the same activity names as the real ones so the
workflow's activity lookups resolve without modification.
"""

import pytest
from temporalio import activity
from temporalio.client import Client
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from model.order import Order
from workflow.order_workflow import OrderWorkflow

TASK_QUEUE = "test-order-processing"
TEST_ORDER = Order(order_id="TEST-001", item="Keyboard", amount=99.99)


@activity.defn(name="validate_inventory")
async def mock_validate_inventory(order: Order) -> str:
    """Passing mock for ``validate_inventory`` — always succeeds."""
    return "INVENTORY_OK"


@activity.defn(name="process_payment")
async def mock_process_payment(order: Order) -> str:
    """Passing mock for ``process_payment`` — always succeeds."""
    return "PAYMENT_OK"


@activity.defn(name="ship_order")
async def mock_ship_order(order: Order) -> str:
    """Passing mock for ``ship_order`` — always succeeds."""
    return "SHIPPED"


@activity.defn(name="send_notification")
async def mock_send_notification(order: Order, message: str) -> str:
    """Passing mock for ``send_notification`` — always succeeds."""
    return "NOTIFIED"


@activity.defn(name="refund_payment")
async def mock_refund_payment(order: Order) -> str:
    """Passing mock for ``refund_payment`` — always succeeds."""
    return "REFUNDED"


@activity.defn(name="restore_inventory")
async def mock_restore_inventory(order: Order) -> str:
    """Passing mock for ``restore_inventory`` — always succeeds."""
    return "INVENTORY_RESTORED"


@activity.defn(name="process_payment")
async def mock_payment_always_fails(order: Order) -> str:
    """Failing mock for ``process_payment``; used to exercise saga rollback.

    Raises a non-retryable error so the workflow short-circuits its
    ``PAYMENT_RETRY`` policy and reaches compensation immediately, keeping
    the test fast.
    """
    raise ApplicationError("Payment gateway unavailable", non_retryable=True)


@activity.defn(name="ship_order")
async def mock_shipping_always_fails(order: Order) -> str:
    """Failing mock for ``ship_order``; used to exercise saga rollback.

    The workflow does not configure a retry policy on ``ship_order``, so the
    default (unlimited retries) would loop forever on a transient error.
    Marking this non-retryable lets the workflow surface the failure to the
    saga handler on the first attempt.
    """
    raise ApplicationError("Shipping provider down", non_retryable=True)


PASSING_ACTIVITIES = [
    mock_validate_inventory,
    mock_process_payment,
    mock_ship_order,
    mock_send_notification,
    mock_refund_payment,
    mock_restore_inventory,
]


async def run_with_activities(env: WorkflowEnvironment, activities, fn):
    """Spin up a worker with the given activity set, run ``fn``, then tear down.

    Args:
        env: The active :class:`WorkflowEnvironment` providing the test
            Temporal server and client.
        activities: Activity callables (typically mocks) to register with the
            worker.
        fn: Async callable that performs the actual test interactions. It
            receives the environment's :class:`Client` as its sole argument
            and is awaited while the worker is running.
    """
    async with Worker(
        env.client,
        task_queue=TASK_QUEUE,
        workflows=[OrderWorkflow],
        activities=activities,
    ):
        await fn(env.client)


@pytest.mark.asyncio
async def test_happy_path():
    """Full order flow: start → signal delivery → completed.

    All activities succeed, the delivery signal is sent atomically with
    workflow start (via ``start_signal``) so that ``_delivery_confirmed`` is
    already True when the workflow reaches its ``wait_condition``. This
    avoids a race in :meth:`WorkflowEnvironment.start_time_skipping` where
    time-skipping would otherwise fire the 7-day delivery timer before a
    post-start signal is processed.
    """
    async with await WorkflowEnvironment.start_time_skipping() as env:

        async def execute(client: Client):
            handle = await client.start_workflow(
                OrderWorkflow.run,
                TEST_ORDER,
                id="test-happy-path",
                task_queue=TASK_QUEUE,
                start_signal="confirm_delivery",
            )

            result = await handle.result()

            assert result.step == "COMPLETED"
            assert result.completed is True
            assert result.error == ""

        await run_with_activities(env, PASSING_ACTIVITIES, execute)


@pytest.mark.asyncio
async def test_delivery_timeout():
    """No delivery signal within 7 days → timeout with notification.

    The test server time-skips past :data:`DELIVERY_TIMEOUT`, so the workflow
    should exit via the ``DELIVERY_TIMEOUT`` branch with ``completed=False``
    and an error message mentioning the timeout.
    """
    async with await WorkflowEnvironment.start_time_skipping() as env:

        async def execute(client: Client):
            handle = await client.start_workflow(
                OrderWorkflow.run,
                TEST_ORDER,
                id="test-delivery-timeout",
                task_queue=TASK_QUEUE,
            )

            result = await handle.result()

            assert result.step == "DELIVERY_TIMEOUT"
            assert result.completed is False
            assert "timed out" in result.error

        await run_with_activities(env, PASSING_ACTIVITIES, execute)


@pytest.mark.asyncio
async def test_saga_payment_failure():
    """Payment fails after all retries → inventory is restored (saga).

    Swaps in a payment mock that always fails. After the workflow exhausts
    its :data:`PAYMENT_RETRY` policy, it should compensate by restoring the
    reserved inventory and terminate in ``FAILED``.
    """
    async with await WorkflowEnvironment.start_time_skipping() as env:

        activities = [
            mock_validate_inventory,
            mock_payment_always_fails,
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
    """Shipping fails → payment refunded AND inventory restored (saga).

    Swaps in a shipping mock that always fails. Both prior steps
    (``PAYMENT`` and ``INVENTORY``) have completed, so the saga handler
    should invoke both compensation activities before the workflow
    terminates in ``FAILED``.
    """
    async with await WorkflowEnvironment.start_time_skipping() as env:

        activities = [
            mock_validate_inventory,
            mock_process_payment,
            mock_shipping_always_fails,
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
