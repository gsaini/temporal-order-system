"""Temporal activities that implement the side-effecting steps of the order
pipeline.

Activities are the place where it is safe to do non-deterministic work such as
network I/O, database access, or random number generation. Temporal records
each activity's inputs and results in the workflow history so that failures
can be retried and workflow replays remain deterministic.

The module exposes six activities:

    Forward path:
        * ``validate_inventory`` — reserve stock for the order
        * ``process_payment``   — charge the customer (retry-friendly)
        * ``ship_order``        — hand the order to the shipping provider
        * ``send_notification`` — notify the customer of status changes

    Saga compensation (invoked by the workflow on failure):
        * ``refund_payment``    — reverse a successful charge
        * ``restore_inventory`` — release reserved stock

Random failures are injected in ``validate_inventory`` and ``process_payment``
purely for learning purposes, so the Temporal Web UI demonstrates retries and
non-retryable errors in action.
"""

import asyncio
import random

from temporalio import activity

from model.order import Order


@activity.defn
async def validate_inventory(order: Order) -> str:
    """Check whether the requested item is in stock.

    Simulates an inventory lookup with a 10% chance of the item being out of
    stock. An out-of-stock result is raised as a non-retryable
    :class:`ApplicationError` because no number of retries will make the item
    appear.

    Args:
        order: The order whose item availability is being verified.

    Returns:
        The literal string ``"INVENTORY_OK"`` when stock is available.

    Raises:
        activity.ApplicationError: With ``type="OUT_OF_STOCK"`` and
            ``non_retryable=True`` when the item is unavailable.
    """
    activity.logger.info(f"Validating inventory for order {order.order_id}, item={order.item}")
    await asyncio.sleep(0.5)

    if random.random() < 0.1:
        raise activity.ApplicationError(
            f"Item '{order.item}' is out of stock",
            type="OUT_OF_STOCK",
            non_retryable=True,
        )

    activity.logger.info(f"Inventory validated for order {order.order_id}")
    return "INVENTORY_OK"


@activity.defn
async def process_payment(order: Order) -> str:
    """Charge the customer for the order.

    Demonstrates Temporal's retry mechanism: a 40% transient failure rate is
    injected to emulate flaky payment gateways. Because the raised exception
    is a plain :class:`RuntimeError` (not flagged non-retryable), the workflow
    will retry according to its configured retry policy.

    Args:
        order: The order being charged. ``order.amount`` is the amount to
            charge.

    Returns:
        The literal string ``"PAYMENT_OK"`` when the charge is accepted.

    Raises:
        RuntimeError: Simulates a transient gateway failure; Temporal will
            retry this activity up to the workflow's ``PAYMENT_RETRY`` policy.
    """
    activity.logger.info(f"Processing payment for order {order.order_id}, amount=${order.amount}")
    await asyncio.sleep(1.0)

    if random.random() < 0.4:
        raise RuntimeError(
            f"Payment gateway timeout for order {order.order_id} "
            "(transient error — Temporal will retry)"
        )

    activity.logger.info(f"Payment processed for order {order.order_id}")
    return "PAYMENT_OK"


@activity.defn
async def ship_order(order: Order) -> str:
    """Hand the order to the shipping provider.

    Args:
        order: The order to ship.

    Returns:
        The literal string ``"SHIPPED"`` once the shipping request is accepted.
    """
    activity.logger.info(f"Shipping order {order.order_id}")
    await asyncio.sleep(0.8)

    activity.logger.info(f"Order {order.order_id} shipped")
    return "SHIPPED"


@activity.defn
async def refund_payment(order: Order) -> str:
    """Reverse a previously successful payment.

    Compensation activity invoked by the workflow's saga handler when a step
    after ``process_payment`` (such as shipping) fails. It assumes the payment
    was already captured for ``order``.

    Args:
        order: The order whose payment should be refunded.

    Returns:
        The literal string ``"REFUNDED"`` once the refund is accepted.
    """
    activity.logger.info(f"Refunding payment for order {order.order_id}, amount=${order.amount}")
    await asyncio.sleep(0.8)

    activity.logger.info(f"Payment refunded for order {order.order_id}")
    return "REFUNDED"


@activity.defn
async def restore_inventory(order: Order) -> str:
    """Return the reserved item to available inventory.

    Compensation activity invoked by the workflow's saga handler when a step
    after ``validate_inventory`` fails. It assumes the item was previously
    reserved for ``order``.

    Args:
        order: The order whose inventory reservation should be released.

    Returns:
        The literal string ``"INVENTORY_RESTORED"`` once the release is
        accepted.
    """
    activity.logger.info(f"Restoring inventory for order {order.order_id}, item={order.item}")
    await asyncio.sleep(0.3)

    activity.logger.info(f"Inventory restored for order {order.order_id}")
    return "INVENTORY_RESTORED"


@activity.defn
async def send_notification(order: Order, message: str) -> str:
    """Deliver a status notification to the customer.

    Stubs out an email/SMS send; no real network call is made.

    Args:
        order: The order the notification relates to. Used for logging and
            addressing the customer.
        message: The human-readable notification body.

    Returns:
        The literal string ``"NOTIFIED"`` once the notification has been
        dispatched.
    """
    activity.logger.info(f"Sending notification for order {order.order_id}: {message}")
    await asyncio.sleep(0.3)

    activity.logger.info(f"Notification sent for order {order.order_id}")
    return "NOTIFIED"
