# Temporal Order Processing System

A learning project that combines **Temporal** (workflow orchestration) and **Helm** (Kubernetes deployment).

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
