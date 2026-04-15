import asyncio
import random

from temporalio import activity

from model.order import Order


@activity.defn
async def validate_inventory(order: Order) -> str:
    """Check whether the requested item is in stock.

    A straightforward activity — it either succeeds or fails.
    """
    activity.logger.info(f"Validating inventory for order {order.order_id}, item={order.item}")
    await asyncio.sleep(0.5)  # simulate DB/API call

    # For learning: 10% chance the item is out of stock
    if random.random() < 0.1:
        raise activity.ApplicationError(
            f"Item '{order.item}' is out of stock",
            type="OUT_OF_STOCK",
            non_retryable=True,  # no point retrying — the item is genuinely unavailable
        )

    activity.logger.info(f"Inventory validated for order {order.order_id}")
    return "INVENTORY_OK"


@activity.defn
async def process_payment(order: Order) -> str:
    """Charge the customer.

    This activity demonstrates Temporal's retry mechanism — payments can fail
    transiently (network issues, gateway timeouts), and Temporal will
    automatically retry based on the retry policy defined in the workflow.

    The failure rate is intentionally high (40%) so you can watch retries
    happen in the Temporal Web UI.
    """
    activity.logger.info(f"Processing payment for order {order.order_id}, amount=${order.amount}")
    await asyncio.sleep(1.0)  # simulate payment gateway call

    # 40% transient failure rate — watch Temporal retry with backoff!
    if random.random() < 0.4:
        raise RuntimeError(
            f"Payment gateway timeout for order {order.order_id} "
            "(transient error — Temporal will retry)"
        )

    activity.logger.info(f"Payment processed for order {order.order_id}")
    return "PAYMENT_OK"


@activity.defn
async def ship_order(order: Order) -> str:
    """Initiate shipping for the order."""
    activity.logger.info(f"Shipping order {order.order_id}")
    await asyncio.sleep(0.8)  # simulate shipping API call

    activity.logger.info(f"Order {order.order_id} shipped")
    return "SHIPPED"


# -- Compensation activities (saga pattern) ------------------------------------

@activity.defn
async def refund_payment(order: Order) -> str:
    """Reverse a previously processed payment.

    Called as compensation when a later step (e.g. shipping) fails after
    payment has already succeeded.
    """
    activity.logger.info(f"Refunding payment for order {order.order_id}, amount=${order.amount}")
    await asyncio.sleep(0.8)  # simulate refund API call

    activity.logger.info(f"Payment refunded for order {order.order_id}")
    return "REFUNDED"


@activity.defn
async def restore_inventory(order: Order) -> str:
    """Put the reserved item back into available inventory.

    Called as compensation when inventory was reserved but a later step failed.
    """
    activity.logger.info(f"Restoring inventory for order {order.order_id}, item={order.item}")
    await asyncio.sleep(0.3)  # simulate inventory update

    activity.logger.info(f"Inventory restored for order {order.order_id}")
    return "INVENTORY_RESTORED"


@activity.defn
async def send_notification(order: Order, message: str) -> str:
    """Send an order status notification to the customer (email/SMS stub)."""
    activity.logger.info(f"Sending notification for order {order.order_id}: {message}")
    await asyncio.sleep(0.3)  # simulate email/SMS send

    activity.logger.info(f"Notification sent for order {order.order_id}")
    return "NOTIFIED"
