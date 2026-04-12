# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A learning project combining Temporal (workflow orchestration) with Helm (Kubernetes deployment). It implements an order processing pipeline: inventory validation -> payment -> shipping -> delivery confirmation via signal.

## Commands

### Local Development

```bash
# Start Temporal server (gRPC on :7233, Web UI on :8233)
docker-compose up -d

# Set up Python environment
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Run the worker (connects to Temporal, polls task queue)
python -m worker.main

# Start an order workflow
python -m starter.main start --order-id ORD-001 --item "Keyboard" --amount 149.99

# Query order status mid-workflow
python -m starter.main query --order-id ORD-001

# Signal delivery confirmation (unblocks the waiting workflow)
python -m starter.main signal --order-id ORD-001
```

### Helm / Kubernetes

```bash
docker build -t order-worker:latest .
helm install order-worker ./helm/order-worker                          # dev
helm install order-worker ./helm/order-worker -f ./helm/order-worker/values-prod.yaml  # prod
helm upgrade order-worker ./helm/order-worker
```

## Architecture

All Python modules are run as top-level packages (`python -m worker.main`, `python -m starter.main`).

- **model/order.py** — `Order` and `OrderStatus` dataclasses. These are serialized by Temporal and passed between workflow and activities.
- **activities/order_activities.py** — Four Temporal activities: `validate_inventory`, `process_payment`, `ship_order`, `send_notification`. Payment has an intentional 40% failure rate to demonstrate retries; inventory has 10% out-of-stock (non-retryable).
- **workflow/order_workflow.py** — `OrderWorkflow` orchestrates the pipeline. Defines a custom `PAYMENT_RETRY` policy. Uses `workflow.signal` for delivery confirmation and `workflow.query` for status checks. Activities are imported through `workflow.unsafe.imports_passed_through()` (Temporal sandbox requirement).
- **worker/main.py** — Connects to Temporal and registers the workflow + all activities on the `order-processing` task queue. Reads `TEMPORAL_ADDRESS` and `TASK_QUEUE` from env vars.
- **starter/main.py** — argparse CLI with three subcommands (`start`, `query`, `signal`). Workflow IDs follow the pattern `order-{order_id}`.

## Key Details

- Python 3.12+, dependencies are only `temporalio` and `pydantic`
- No test suite exists currently
- Temporal Web UI runs at http://localhost:8233 when using docker-compose
- Env vars: `TEMPORAL_ADDRESS` (default `localhost:7233`), `TASK_QUEUE` (default `order-processing`)
- Helm chart is at `helm/order-worker/` with separate `values-prod.yaml` for production overrides (3 replicas, higher resource limits)
