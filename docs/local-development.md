# Local development

Docker Compose is a local development environment only. The platform's target deployment is
GKE (Helm chart and platform infrastructure, phase P3 of the
[implementation plan](implementation-plan.md)).

## Everything in Docker (recommended)

```bash
docker compose up --build -d     # postgres, redis, backend (API), cluster-manager, terraform-runner, monitoring-worker, frontend
docker compose logs -f backend cluster-manager terraform-runner monitoring-worker
docker compose down              # keep data;  docker compose down -v  to reset
```

| Service | URL |
|---|---|
| Console | http://localhost:3000 |
| API + OpenAPI docs | http://localhost:8000/api/docs |
| API metrics | http://localhost:8000/metrics |
| Worker metrics | `docker compose exec cluster-manager python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:9101').read().decode())"` |

Configuration comes from environment variables. Copy [`.env.example`](../.env.example) to
`.env` to override the defaults (ports, `MOCK_SPEED`, demo password, `ELASTICSEARCH_VERSION`).

The compose defaults are **for local use only**: a development `SECRET_KEY` and seeded demo
users (Owner, Admin, Operator, Viewer in Acme; an Owner in Globex). `MOCK_MODE=false` is refused
until phase P4, and `ENVIRONMENT=production` refuses the development secret.

Upgrading an existing local database: the API applies migrations on start. Revision `0002`
converts v1 data (states, roles, audit events) and removes stored service-account keys; the
demo user `developer@acme.example` becomes `operator@acme.example`. A cloud account that used a
key shows `DISCONNECTED`; press **Validate** to reconnect it in mock mode.

## Running components natively

Requirements: Python 3.12+ with [uv](https://docs.astral.sh/uv/), Node.js 20.9+, and a local
PostgreSQL and Redis (`docker compose up -d postgres redis` works if you publish their ports).

```bash
# API
cd backend
uv sync
export DATABASE_URL=postgresql+psycopg://byoc:byoc-local-dev@localhost:5432/byoc
export REDIS_URL=redis://localhost:6379/0 SEED_DEMO_DATA=true DEMO_PASSWORD=demo-password LOG_FORMAT=console
uv run alembic upgrade head
uv run uvicorn app.main:create_app --factory --reload --port 8000

# Worker (second terminal, same environment)
uv run python -m app.workers.main all      # or one role: cluster-manager | terraform-runner | monitoring-worker

# Console (third terminal)
cd frontend
npm install
BACKEND_URL=http://localhost:8000 npm run dev      # http://localhost:3000
```

The console calls `/api/...` on its own origin; Next.js proxies it to `BACKEND_URL`.

## Tests

| Suite | Command | Notes |
|---|---|---|
| Backend (unit, API, provider, integration, migrations) | `cd backend && uv run pytest` | SQLite, in-memory queue, mock mode at instant speed |
| Backend on PostgreSQL | `TEST_DATABASE_URL=postgresql+psycopg://... uv run pytest` | Same suite, real database |
| Lint | `uv run ruff check . && uv run ruff format --check .` | |
| Go agent | `make test-agent` | Runs `go vet` and `go test -race` in the golang image |
| Terraform | `make test-terraform` | `terraform validate` + `terraform test` with mocked providers (offline) |
| Frontend | `make test-frontend` (or `cd frontend && npm run typecheck && npm run build`) | Runs in a Node container |
| Acceptance | `make smoke` | PRD acceptance criteria and v2 rules against the running stack |

Backend test layout:

- `tests/unit`: domain rules (RBAC matrix, lifecycle and operation transitions, lifecycle ownership, desired state, node health), security primitives, the real-mode gate.
- `tests/providers`: the version catalog rules, Elasticsearch semantics (scale-up only, health model), GCP error mapping, the plan guard, Terraform rendering, a fake `tofu` binary for the full plan/apply/destroy flow, the mock data plane.
- `tests/api`: authentication, RBAC (Operator), tenant isolation, keyless cloud accounts, validation, idempotency, delete confirmation and pre-emption, the agent protocol.
- `tests/integration`: API → database → worker → provider lifecycles, failure detection, retries, cancellation, the reaper, Redis plumbing, migrations including the v1 → v2 data migration.

## Database migrations

```bash
cd backend
uv run alembic revision --autogenerate -m "describe the change"   # against PostgreSQL
uv run alembic upgrade head
uv run alembic check          # fails if models and migrations differ
```

The API container runs `alembic upgrade head` on start, under a PostgreSQL advisory lock
so that several replicas never migrate concurrently.

## Useful knobs

| Variable | Default | Effect |
|---|---|---|
| `MOCK_MODE` | `true` | Simulate GCP, Terraform and VMs (the only accepted value until P4) |
| `ELASTICSEARCH_VERSION` | catalog default | Exact default version for new clusters |
| `MOCK_SPEED` | `1.0` | Scales simulated durations; `0.3` makes demos faster |
| `MONITOR_INTERVAL_SECONDS` | `15` | Health monitor cadence |
| `AGENT_STALE_SECONDS` | `90` | Report age after which a node's agent counts as unavailable |
| `WORKER_CONCURRENCY` | `4` | Operations run in parallel per worker |
| `LOG_FORMAT` | `json` | `console` for human-readable logs |
