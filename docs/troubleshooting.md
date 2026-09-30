# Troubleshooting

Every API error and failed operation carries a `code`, a `reason` and a `suggested_action`,
and every log line carries `request_id` / `operation_id`. Start there:

```bash
docker compose logs backend worker | grep <request_id or operation_id>
```

## Local stack

| Symptom | Fix |
|---|---|
| `docker compose up` fails with a port in use | Set `UI_PORT` / `API_PORT` in `.env` |
| Console shows "Could not reach the control plane" | `docker compose ps`; the backend must be healthy. The frontend proxies `/api` to `http://backend:8000` (baked in at build time) |
| Backend exits with "MOCK_MODE=false is disabled" | Real GCP mode is off until per-organization identities exist (phase P4); use `MOCK_MODE=true` |
| Backend exits with "SECRET_KEY must be a random value" | `ENVIRONMENT=production` needs a `SECRET_KEY` of at least 32 random characters |
| Backend exits with a version catalog error | `ELASTICSEARCH_VERSION` (or a mounted catalog) names a version that is not a `supported` catalog entry |
| Demo accounts missing | Seeding runs only on an empty database with `SEED_DEMO_DATA=true` and an 8+ character `DEMO_PASSWORD`; `docker compose down -v` resets |
| Operations stay PENDING | The worker is not running: `docker compose logs worker`. The reaper re-enqueues lost messages within about 2 minutes |
| "Too many login attempts" | 10 attempts per 5 minutes per email and per IP; wait, or restart Redis in development |

## Cloud accounts

| Error code | Meaning and fix |
|---|---|
| `GCP_AUTHENTICATION_FAILED` | The platform lacks `roles/iam.serviceAccountTokenCreator` on the service account, or the service account was deleted |
| `KEY_AUTH_REMOVED` | The account used an uploaded key before keys were removed; grant impersonation on the same service account and validate again |
| `CLOUD_ACCOUNT_NOT_CONNECTED` | Scaling or retrying needs a `CONNECTED` account; validate it again (deleting a cluster does not need one) |
| `GCP_PROJECT_NOT_FOUND` / project check failed | Wrong project ID, or the service account has no role in the project |
| `GCP_API_DISABLED` | Run the `gcloud services enable ...` command from the suggested action |
| `GCP_PERMISSION_DENIED` | The listed permissions are missing; apply the `control-plane-access` module or grant the suggested role |

## Provisioning

| Error code | Meaning and fix |
|---|---|
| `GCP_QUOTA_EXCEEDED` | Request a quota increase (IAM & Admin → Quotas), or use fewer/smaller nodes |
| `GCP_CAPACITY_UNAVAILABLE` | The zone is out of capacity for the machine type; retry later or choose another zone/type |
| `GCP_RESOURCE_CONFLICT` | A resource with the generated name already exists outside this cluster |
| `UNSAFE_PLAN` | Terraform would destroy or replace a data node, usually because of manual changes in the project. Nothing was changed; inspect the workspace plan and remove the drift |
| `BOOTSTRAP_FAILED` | The startup script failed on a VM. Read the serial console output or `/var/log/byoc-bootstrap.log` (IAP SSH can be enabled for break-glass access) |
| `BOOTSTRAP_TIMEOUT` | VMs never reported progress: check Cloud NAT egress (package downloads) and that `enable-guest-attributes` is set |
| `HEALTH_CHECK_TIMEOUT` | Elasticsearch did not form a green cluster; the reasons list the failing nodes. Check `journalctl -u elasticsearch` on the VM |
| `OPERATION_ABANDONED` | A worker died three times during the operation; check the worker logs and retry |
| `SCALE_DOWN_NOT_SUPPORTED` | Only scale-up exists in the MVP; choose a larger node count |
| `CONFIRMATION_REQUIRED` | Deletes need `?confirm=<cluster name>` |
| `DELETE_NOT_CANCELLABLE` | A confirmed delete cannot be cancelled; if it fails, retry it |

Failed operations can be retried from the operation or cluster page. Workflows are
idempotent: Terraform converges on what already exists. A failed scale-up leaves the cluster
ACTIVE on its existing nodes; retrying finishes it (rolling back would be a scale-down).

## Health

| Reason shown | Meaning |
|---|---|
| Agent unavailable: no report for Ns | No report through heartbeat or guest attributes for 90 s while the VM runs (node UNKNOWN, agent STALE); check `systemctl status byoc-agent` |
| VM unavailable: instance status is TERMINATED | The VM stopped (preemption, maintenance, manual stop) |
| VM missing | The instance was deleted outside the platform |
| Node missing: 2/3 nodes have joined the cluster | A node is up but not part of the Elasticsearch cluster |
| Cluster status YELLOW / RED | Replica (DEGRADED) / primary (UNHEALTHY) shards unassigned |
| Elasticsearch cluster has not formed | No elected master (lost quorum) |

Automatic replacement of failed nodes is planned for a later phase; today, fix or restart
the VM and the monitor records the recovery.
