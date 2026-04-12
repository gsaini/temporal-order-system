"""Temporal Worker — connects to the Temporal server and polls for tasks.

Run with:
    python -m worker.main

The worker registers the OrderWorkflow and all activities, then listens on
the "order-processing" task queue.
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
)
from workflow.order_workflow import OrderWorkflow

TEMPORAL_ADDRESS = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
TASK_QUEUE = os.getenv("TASK_QUEUE", "order-processing")


async def main() -> None:
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
        ],
    )

    print(f"Worker started — listening on task queue '{TASK_QUEUE}'")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
