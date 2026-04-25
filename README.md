# Temporal Order Processing System

[![Tests](https://img.shields.io/github/actions/workflow/status/gsaini/temporal-order-system/tests.yml?branch=main&style=for-the-badge&logo=githubactions&logoColor=white&label=tests)](https://github.com/gsaini/temporal-order-system/actions/workflows/tests.yml)
[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/downloads/)
[![Temporal](https://img.shields.io/badge/Temporal-1.7.1-7C4DFF?style=for-the-badge&logo=temporal&logoColor=white)](https://github.com/temporalio/sdk-python)
[![Helm](https://img.shields.io/badge/Helm-3.x-0F1689?style=for-the-badge&logo=helm&logoColor=white)](https://helm.sh/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=for-the-badge&logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![Kubernetes](https://img.shields.io/badge/Kubernetes-Ready-326CE5?style=for-the-badge&logo=kubernetes&logoColor=white)](https://kubernetes.io/)
[![Last commit](https://img.shields.io/github/last-commit/gsaini/temporal-order-system?style=for-the-badge&logo=github&logoColor=white)](https://github.com/gsaini/temporal-order-system/commits/main)

A learning project that combines **Temporal** (workflow orchestration) and **Helm** (Kubernetes deployment).

---

## Understanding Temporal

### What problem does Temporal solve?

Imagine writing code for a process that involves multiple steps across unreliable systems — validate inventory, charge the customer, ship the package, notify the user. In plain code you have to handle:

- **Crashes mid-way** — what if the process dies after charging but before shipping?
- **Transient failures** — network hiccups, gateway timeouts, rate limits
- **Long-running waits** — pausing hours/days for human input or external events
- **Retry logic** — exponential backoff, max attempts, non-retryable errors
- **State tracking** — where are we in the pipeline? what has been done?
- **Visibility** — what is my system doing right now?

Temporal turns all of this into a solved problem. You write your business logic as a normal-looking async function, and Temporal guarantees that it runs to completion — even across crashes, restarts, and multi-day pauses — with full history, retries, and observability for free.

### Core concepts

#### 1. Workflow

A workflow is **durable, deterministic code** that orchestrates a business process. In this project, [`OrderWorkflow`](workflow/order_workflow.py) orchestrates the entire order pipeline.

Key properties:

- **Durable** — Temporal records every step to a database. If the worker dies, the workflow resumes exactly where it left off on another worker.
- **Deterministic** — workflow code must produce the same output given the same event history. You can't use `random`, `time.time()`, `datetime.now()`, or `requests.get()` directly inside a workflow. Instead, use workflow-safe APIs: `workflow.now()`, `workflow.random()`, and call activities for any I/O.
- **Long-running** — workflows can pause for seconds or years without consuming worker resources.

```python
@workflow.defn
class OrderWorkflow:
    @workflow.run
    async def run(self, order: Order) -> OrderStatus:
        await workflow.execute_activity(validate_inventory, order, ...)
        await workflow.execute_activity(process_payment, order, ...)
        # ...
```

#### 2. Activity

An activity is where **side effects** happen — database writes, API calls, sending email, charging payments. Activities are **regular functions** (not deterministic-constrained), and Temporal handles their retries, timeouts, and result persistence.

```python
@activity.defn
async def process_payment(order: Order) -> str:
    # Real API call here — Temporal will retry this on failure.
    return "PAYMENT_OK"
```

The workflow calls activities via `workflow.execute_activity(...)`. The result is persisted, so on replay the workflow doesn't re-run the activity — it just reads the stored result.

#### 3. Worker

A worker is a **process that hosts workflow and activity code**. It connects to the Temporal server, polls a **task queue** for work, and executes it. See [`worker/main.py`](worker/main.py).

You can run multiple workers for scale or redundancy. Temporal distributes tasks automatically.

```python
worker = Worker(
    client,
    task_queue="order-processing",
    workflows=[OrderWorkflow],
    activities=[validate_inventory, process_payment, ...],
)
```

#### 4. Task queue

A **named channel** where workers pick up tasks. Workflows and activities are dispatched to workers via task queues. This project uses `order-processing`.

You can route different workflows/activities to different queues — e.g., a high-priority queue with dedicated workers, or a GPU queue for ML activities.

#### 5. Temporal Server

The backend that stores workflow state, dispatches tasks, and provides the API. It runs independently of your workers. In this project it's a Docker container ([docker-compose.yaml](docker-compose.yaml)) backed by PostgreSQL.

For production, you can self-host on Kubernetes or use **Temporal Cloud** (managed service).

---

### Temporal features used in this project

#### Retry policies

Each activity can have a custom retry policy. In [`order_workflow.py`](workflow/order_workflow.py), payment has a generous retry policy because the gateway is flaky:

```python
PAYMENT_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),   # wait 1s before first retry
    backoff_coefficient=2.0,                 # then 2s, 4s, 8s, 16s...
    maximum_interval=timedelta(seconds=30),  # cap at 30s
    maximum_attempts=5,                      # give up after 5 tries
)
```

Temporal handles all the retry math. You don't write loops.

#### Non-retryable errors

Not every error should be retried. Out-of-stock is permanent — no amount of retrying will fix it:

```python
raise activity.ApplicationError(
    f"Item '{order.item}' is out of stock",
    type="OUT_OF_STOCK",
    non_retryable=True,
)
```

See [`activities/order_activities.py`](activities/order_activities.py).

#### Signals — external input to a running workflow

A signal is a **message sent into a workflow from outside**. The workflow pauses until it arrives:

```python
@workflow.signal
async def confirm_delivery(self) -> None:
    self._delivery_confirmed = True

# Later in run():
await workflow.wait_condition(lambda: self._delivery_confirmed, timeout=timedelta(days=7))
```

From the CLI:

```bash
python -m starter.main signal --order-id ORD-001
```

Signals are **buffered** — if sent before the workflow is ready to receive, they wait. They're also **durable** — a signal arriving while the worker is down is not lost.

#### Queries — read state without side effects

A query lets you **read a workflow's current state** without affecting it:

```python
@workflow.query
def get_status(self) -> OrderStatus:
    return self._status
```

Queries are synchronous, return immediately, and don't appear in the workflow history. Great for dashboards, APIs, or `python -m starter.main query`.

#### Saga / compensation pattern

If a later step fails after earlier steps succeeded, the workflow **compensates** in reverse — refunds payment, restores inventory. Implemented in [`OrderWorkflow._compensate`](workflow/order_workflow.py):

```python
try:
    await execute_activity(validate_inventory, ...)
    completed_steps.append("INVENTORY")
    await execute_activity(process_payment, ...)
    completed_steps.append("PAYMENT")
    await execute_activity(ship_order, ...)  # fails here!
except ActivityError:
    # Reverse: refund payment, then restore inventory
    await self._compensate(order, completed_steps)
```

This gives you **transactional semantics across services** without a distributed transaction coordinator.

#### Timeouts

Three distinct timeouts you can set on activities:

- **`start_to_close_timeout`** — max time for a single activity attempt (after it starts on a worker)
- **`schedule_to_close_timeout`** — total time from scheduling until final result (including retries)
- **`heartbeat_timeout`** — for long-running activities, fail if no heartbeat within this window

Workflows also have timeouts — e.g., the 7-day delivery signal deadline:

```python
delivered = await workflow.wait_condition(
    lambda: self._delivery_confirmed,
    timeout=timedelta(days=7),
)
```

#### Workflow sandbox

Workflow code runs in a restricted sandbox to enforce determinism. Non-deterministic imports (like anything that does I/O) must go through `workflow.unsafe.imports_passed_through()`:

```python
with workflow.unsafe.imports_passed_through():
    from activities.order_activities import validate_inventory, ...
```

---

### How it all fits together

```
┌─────────┐         ┌──────────────────┐         ┌──────────┐
│ Starter │─ start ─►│  Temporal Server  │◄─ poll ─│  Worker  │
│  CLI    │         │  (PostgreSQL)     │         │          │
│         │◄─ result─│  - event history  │─dispatch►│  - runs  │
│         │         │  - task queues    │         │   workflow│
│         │─signal ─►│  - timers         │         │  - runs  │
│         │         │                   │         │   activities│
│         │─ query ─►│                   │◄─ result─│          │
└─────────┘         └──────────────────┘         └──────────┘
```

1. **Starter** sends "start workflow" RPC to the server
2. **Server** persists the start event and schedules a workflow task
3. **Worker** polls the task queue, picks up the workflow task
4. Worker runs `OrderWorkflow.run` until it hits `await execute_activity(...)`
5. Worker reports "I need this activity run" back to the server
6. Server schedules an activity task; worker (maybe a different one) picks it up
7. Activity runs, returns result; server persists the result
8. Worker resumes workflow with the activity result, continues to next `await`
9. Each step is an event in the persistent **event history**

**Crashes don't matter.** If the worker dies mid-workflow, another worker picks up from the last persisted event. The workflow code "replays" from the beginning, reading prior results from the event history, until it catches up to where the crash happened.

---

### Debugging & observability

- **Web UI** at <http://localhost:8233> shows every workflow's event history, including activity executions, retries, signals, and failures
- **Workflow IDs** are user-chosen (this project uses `order-{order_id}`), so you can find a specific workflow easily
- **Temporal CLI** (`tctl` / `temporal`) lets you script workflow starts, signals, queries, and terminations
- **Logging** — use `workflow.logger` inside workflows and `activity.logger` inside activities; they correlate logs to the workflow/activity context

---

## Architecture

```
                         ┌──────────────────────────┐
                         │       Starter CLI         │
                         │    (starter/main.py)      │
                         │  start / query / signal   │
                         └────────────┬─────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Docker Compose / Kubernetes                   │
│                                                                 │
│  ┌──────────────┐    ┌──────────────────┐    ┌──────────────┐  │
│  │  PostgreSQL   │◄───│  Temporal Server  │───►│  Temporal UI  │ │
│  │    :5432      │    │     :7233 gRPC    │    │    :8233      │ │
│  └──────────────┘    └────────┬─────────┘    └──────────────┘  │
│                               │                                 │
│                     dispatches tasks                             │
│                               │                                 │
│                               ▼                                 │
│             ┌─────────────────────────────────┐                 │
│             │     Worker  (worker/main.py)     │                │
│             │                                  │                │
│             │  ┌────────────────────────────┐  │                │
│             │  │      OrderWorkflow         │  │                │
│             │  │                            │  │                │
│             │  │  1. Validate Inventory     │  │                │
│             │  │           │                │  │                │
│             │  │           ▼                │  │                │
│             │  │  2. Process Payment        │  │                │
│             │  │     (retry: 5 attempts)    │  │                │
│             │  │           │                │  │                │
│             │  │           ▼                │  │                │
│             │  │  3. Ship Order             │  │                │
│             │  │           │                │  │                │
│             │  │           ▼                │  │                │
│             │  │  4. Notify: Shipped        │  │                │
│             │  │           │                │  │                │
│             │  │           ▼                │  │                │
│             │  │  5. Wait for Signal ◄──────┼──┼── confirm_delivery
│             │  │           │                │  │                │
│             │  │           ▼                │  │                │
│             │  │  6. Notify: Delivered      │  │                │
│             │  │           │                │  │                │
│             │  │           ▼                │  │                │
│             │  │  7. Completed              │  │                │
│             │  └────────────────────────────┘  │                │
│             └─────────────────────────────────┘                 │
└─────────────────────────────────────────────────────────────────┘
```

## Project Structure

```
temporal-order-system/
├── model/                 # Order & OrderStatus dataclasses
├── activities/            # Activity implementations (inventory, payment, shipping, notifications)
├── workflow/              # OrderWorkflow definition
├── worker/                # Worker entrypoint — connects to Temporal and polls for tasks
├── starter/               # CLI to start workflows, query status, and signal delivery
├── helm/order-worker/     # Helm chart for K8s deployment
├── Dockerfile
├── docker-compose.yaml
└── requirements.txt
```

## Prerequisites

- Python 3.12+
- Docker & Docker Compose
- (Phase 2) kubectl, Helm 3, and a local K8s cluster (kind / minikube)

---

## Phase 1: Run Locally

### 1. Start Temporal Server

```bash
docker-compose up -d
```

This starts:

- **Temporal Server** on `localhost:7233`
- **Temporal Web UI** at <http://localhost:8233>

### 2. Install Python dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Run the Worker

```bash
python -m worker.main
```

You should see: `Worker started — listening on task queue 'order-processing'`

### 4. Interact with the workflow (in a second terminal)

```bash
# Activate venv in this terminal too
source .venv/bin/activate

# Start a new order
python -m starter.main start --order-id ORD-001 --item "Mechanical Keyboard" --amount 149.99

# Query order status (run this while the workflow is in progress)
python -m starter.main query --order-id ORD-001

# Confirm delivery (the workflow is paused waiting for this signal)
python -m starter.main signal --order-id ORD-001
```

### 5. Observe in the Web UI

Open <http://localhost:8233> and click on the workflow. You can see:

- Each activity execution and its duration
- Retry attempts on the payment activity
- The signal event when you confirm delivery
- The full event history

---

## Phase 2: Deploy to Kubernetes with Helm

### 1. Create a local K8s cluster

```bash
# Using kind
kind create cluster --name temporal-learn

# Or minikube
minikube start
```

### 2. Deploy Temporal Server (official Helm chart)

```bash
helm repo add temporal https://temporal.io/helm-charts
helm install temporal-server temporal/temporal \
  --set server.replicaCount=1 \
  --set cassandra.config.cluster_size=1 \
  --timeout 15m
```

### 3. Build and load the worker image

```bash
docker build -t order-worker:latest .

# For kind:
kind load docker-image order-worker:latest --name temporal-learn

# For minikube:
minikube image load order-worker:latest
```

### 4. Deploy the worker

```bash
# Dev (defaults)
helm install order-worker ./helm/order-worker

# Prod (3 replicas, higher resource limits)
helm install order-worker ./helm/order-worker -f ./helm/order-worker/values-prod.yaml
```

### 5. Useful Helm commands

```bash
helm status order-worker           # Check deployment status
helm upgrade order-worker ./helm/order-worker  # Deploy new code
helm rollback order-worker 1       # Rollback to revision 1
helm template ./helm/order-worker  # Preview rendered manifests (dry run)
helm uninstall order-worker        # Tear down
```

---

## Key Concepts Covered

| Concept | Where to look |
|---|---|
| **Temporal Workflows** | `workflow/order_workflow.py` — the full pipeline |
| **Temporal Activities** | `activities/order_activities.py` — each step |
| **Retry Policies** | `workflow/order_workflow.py` — `PAYMENT_RETRY` |
| **Signals** | Delivery confirmation pauses the workflow |
| **Queries** | Check order status mid-workflow |
| **Helm Templates** | `helm/order-worker/templates/` — Go templating |
| **Helm Values** | `helm/order-worker/values.yaml` vs `values-prod.yaml` |
| **ConfigMaps** | Temporal connection config injected via env vars |
| **Health Probes** | Liveness/readiness in `deployment.yaml` |

---

## What to experiment with

1. **Watch retries** — Payment has a 40% failure rate. Start an order and watch the Web UI show retry attempts with exponential backoff.
2. **Try signals** — Start an order, wait for it to reach `AWAITING_DELIVERY`, then send the signal. The workflow resumes.
3. **Kill the worker** — Mid-workflow, stop the worker process. Restart it. The workflow picks up exactly where it left off.
4. **Scale with Helm** — Change `replicaCount` in values and run `helm upgrade`. Watch pods scale.
5. **Override values** — Try `helm install ... --set temporal.taskQueue=my-queue` to see how Helm parameterization works.
