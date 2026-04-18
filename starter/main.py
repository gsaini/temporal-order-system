"""CLI to interact with order workflows — start, query status, or signal delivery.

Each subcommand constructs a :class:`temporalio.client.Client`, looks up or
starts a workflow identified by ``order-{order_id}``, and performs a single
client-side action.

Environment variables:
    TEMPORAL_ADDRESS: Host:port of the Temporal frontend gRPC endpoint
        (default ``localhost:7233``).
    TASK_QUEUE: Task queue that the worker is polling (default
        ``order-processing``). Must match the worker configuration.

Usage:
    # Start a new order workflow
    python -m starter.main start --order-id ORD-001 --item "Mechanical Keyboard" --amount 149.99

    # Query current order status
    python -m starter.main query --order-id ORD-001

    # Signal delivery confirmation
    python -m starter.main signal --order-id ORD-001
"""

import argparse
import asyncio
import os

from temporalio.client import Client

from model.order import Order
from workflow.order_workflow import OrderWorkflow

TEMPORAL_ADDRESS = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
TASK_QUEUE = os.getenv("TASK_QUEUE", "order-processing")


async def start_order(client: Client, args: argparse.Namespace) -> None:
    """Dispatch a new :class:`OrderWorkflow` execution.

    Builds an :class:`Order` from ``args`` and starts the workflow
    asynchronously — the call returns as soon as Temporal has persisted the
    start request, not when the workflow finishes. The workflow ID follows
    the pattern ``order-{order_id}`` so later ``query`` and ``signal``
    commands can address the same run.

    Args:
        client: Connected Temporal client.
        args: Parsed CLI namespace. Must include ``order_id``, ``item``,
            ``amount``, and ``customer_id``.
    """
    order = Order(
        order_id=args.order_id,
        item=args.item,
        amount=args.amount,
        customer_id=args.customer_id,
    )

    handle = await client.start_workflow(
        OrderWorkflow.run,
        order,
        id=f"order-{order.order_id}",
        task_queue=TASK_QUEUE,
    )

    print(f"Started workflow for order {order.order_id}")
    print(f"  Workflow ID : {handle.id}")
    print(f"  Run ID      : {handle.result_run_id}")
    print()
    print("Next steps:")
    print(f"  Query status : python -m starter.main query --order-id {order.order_id}")
    print(f"  Confirm delivery: python -m starter.main signal --order-id {order.order_id}")


async def query_status(client: Client, args: argparse.Namespace) -> None:
    """Fetch and print the current status of an in-flight or finished order.

    Invokes the workflow's ``get_status`` query handler, which is a read-only
    operation and may be called at any point in the workflow's lifecycle.

    Args:
        client: Connected Temporal client.
        args: Parsed CLI namespace. Must include ``order_id``.
    """
    handle = client.get_workflow_handle(f"order-{args.order_id}")
    status = await handle.query(OrderWorkflow.get_status)

    print(f"Order {status.order_id}")
    print(f"  Step      : {status.step}")
    print(f"  Completed : {status.completed}")
    if status.error:
        print(f"  Error     : {status.error}")


async def signal_delivery(client: Client, args: argparse.Namespace) -> None:
    """Send the ``confirm_delivery`` signal to a running order workflow.

    The signal unblocks the workflow's ``wait_condition`` on
    ``_delivery_confirmed``. If the workflow has not yet reached that step,
    Temporal buffers the signal until it does.

    Args:
        client: Connected Temporal client.
        args: Parsed CLI namespace. Must include ``order_id``.
    """
    handle = client.get_workflow_handle(f"order-{args.order_id}")
    await handle.signal(OrderWorkflow.confirm_delivery)
    print(f"Delivery confirmation signal sent for order {args.order_id}")


async def main() -> None:
    """Parse CLI arguments, connect to Temporal, and dispatch to a subcommand.

    Three subcommands are supported:

        * ``start``  — launch a new workflow for the given order.
        * ``query``  — read back the current status of an order workflow.
        * ``signal`` — confirm delivery on a running order workflow.
    """
    parser = argparse.ArgumentParser(description="Order workflow CLI")
    subparsers = parser.add_subparsers(dest="action", required=True)

    start_p = subparsers.add_parser("start", help="Start a new order workflow")
    start_p.add_argument("--order-id", required=True, help="Unique order ID")
    start_p.add_argument("--item", required=True, help="Item name")
    start_p.add_argument("--amount", type=float, required=True, help="Order amount")
    start_p.add_argument("--customer-id", default="CUST-001", help="Customer ID")

    query_p = subparsers.add_parser("query", help="Query order status")
    query_p.add_argument("--order-id", required=True, help="Order ID to query")

    signal_p = subparsers.add_parser("signal", help="Confirm delivery")
    signal_p.add_argument("--order-id", required=True, help="Order ID to signal")

    args = parser.parse_args()

    client = await Client.connect(TEMPORAL_ADDRESS)

    actions = {
        "start": start_order,
        "query": query_status,
        "signal": signal_delivery,
    }
    await actions[args.action](client, args)


if __name__ == "__main__":
    asyncio.run(main())
