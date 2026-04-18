"""Temporal Worker — connects to the Temporal server and polls for tasks.

Workers are the long-running processes that actually execute workflow and
activity code. This module registers :class:`OrderWorkflow` and every
activity it depends on, then listens on the ``order-processing`` task queue
until the process is terminated.

Environment variables:
    TEMPORAL_ADDRESS: Host:port of the Temporal frontend gRPC endpoint.
        Defaults to ``localhost:7233`` for local docker-compose runs.
    TASK_QUEUE: Name of the task queue to poll. Defaults to
        ``order-processing``. Must match the task queue used by the starter
        client when dispatching workflows.

Run with:
    python -m worker.main
"""

import asyncio
import os

from temporalio.client import Client
from temporalio.worker import Worker

from activities.order_activities import (
    validate_inventory,
    process_payment,
    ship_order,
    send_notification,
    refund_payment,
    restore_inventory,
)
from workflow.order_workflow import OrderWorkflow

TEMPORAL_ADDRESS = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
TASK_QUEUE = os.getenv("TASK_QUEUE", "order-processing")


async def main() -> None:
    """Connect to the Temporal server and run the worker forever.

    Blocks on :meth:`Worker.run` until the worker is shut down (for example,
    by a ``SIGINT``). Raises whatever the Temporal SDK raises if the server
    cannot be reached or the worker fails to start.
    """
    print(f"Connecting to Temporal at {TEMPORAL_ADDRESS} ...")
    client = await Client.connect(TEMPORAL_ADDRESS)

    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[OrderWorkflow],
        activities=[
            validate_inventory,
            process_payment,
            ship_order,
            send_notification,
            refund_payment,
            restore_inventory,
        ],
    )

    print(f"Worker started — listening on task queue '{TASK_QUEUE}'")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
