# API

Base path `/api/v1` (routes in `backend/app/api/v1/`). The interactive OpenAPI reference is
served at `/api/docs` (`/api/v1/openapi.json`). Every request and response is JSON.

## Authentication

```bash
curl -s -c cookies -H 'content-type: application/json' \
  -d '{"email":"owner@acme.example","password":"demo-password"}' \
  http://localhost:8000/api/v1/auth/login
```

The login response contains an `access_token` and also sets an httpOnly, `SameSite=Strict`
session cookie (used by the console). API clients can send `Authorization: Bearer <token>`
instead. Sessions last 12 hours. Membership and role are re-checked on every request, and the
organization always comes from the session, never from a request body.

| Endpoint | Purpose |
|---|---|
| `POST /auth/login` | Email + password (optionally `organization_id` of one of your memberships) |
| `POST /auth/signup` | Create a user and a new organization (you become Owner) |
| `POST /auth/logout` | Clear the session cookie |
| `GET /auth/me` | User, current organization, role, `permissions`, other memberships |
| `POST /auth/switch-organization` | Re-issue the session for another organization you belong to |

## Endpoints

Permissions are listed in [ADR 0009](adr/0009-roles-and-permissions.md); clients should enable
actions from `permissions` in `GET /auth/me`. Resources of other organizations answer **404**.

| Method and path | Permission | Notes |
|---|---|---|
| `GET /meta` | public | Version, mock mode, demo accounts (mock mode only) |
| `GET /organizations/current`, `.../members` | `member:read` | |
| `POST /organizations/current/members` | `member:manage` | `{email, name?, role, password?}`; roles `OWNER`, `ADMIN`, `OPERATOR`, `VIEWER`; only Owners grant Owner |
| `PATCH /organizations/current/members/{user_id}` | `member:manage` | `{role}`; the last Owner cannot be demoted |
| `DELETE /organizations/current/members/{user_id}` | `member:manage` | |
| `GET /cloud-providers` | `cloud_account:read` | Clouds offered (GCP and AWS in mock mode; GCP only once real mode is enabled) |
| `GET /cloud-accounts/onboarding?provider=gcp\|aws` | `cloud_account:read` | What the customer grants: principal, role, permissions; for AWS the organization's external ID and trust policy |
| `POST /cloud-accounts` | `cloud_account:manage` | GCP `{name, provider: "gcp", project_id, region, service_account_email}`; AWS `{name, provider: "aws", project_id: <12-digit account ID>, region, role_arn}`; `validate_now`. Keys are rejected. |
| `GET /cloud-accounts`, `GET /cloud-accounts/{id}` | `cloud_account:read` | Status `PENDING`, `VALIDATING`, `CONNECTED`, `FAILED` or `DISCONNECTED` |
| `POST /cloud-accounts/{id}/validate` | `cloud_account:manage` | Returns `VALIDATING` at once; the terraform-runner runs the checks and stores the result (poll the account) |
| `DELETE /cloud-accounts/{id}` | `cloud_account:manage` | 409 while clusters or registered networks use it |
| `GET /environments`, `GET /environments/{id}` | `environment:read` | Name, type `TEST` or `PRODUCTION`, network and cluster counts, providers in use |
| `POST /environments` | `environment:manage` | `{name, type, description?}` |
| `DELETE /environments/{id}` | `environment:manage` | 409 `ENVIRONMENT_NOT_EMPTY` while it has networks or clusters that are not deleted |
| `GET /environments/{id}/networks` | `network:read` | `?provider=gcp\|aws` |
| `POST /environments/{id}/networks` | `network:manage` | Register an existing network: `{name, cloud_account_id, region, vpc, subnets}` (GCP: network name and one subnet name; AWS: VPC ID and 1-6 subnet IDs, one per zone). Looks it up at once; status `AVAILABLE` or `FAILED` with the checks |
| `POST /networks/lookup` | `network:manage` | Same body without `name`: fetch the details without registering (the API waits up to `LOOKUP_TIMEOUT_SECONDS` for the monitoring-worker) |
| `GET /networks/{id}` | `network:read` | Status, zones, `details` (resource paths, ranges, free addresses, checks, warnings) |
| `POST /networks/{id}/validate` | `network:manage` | Look it up again; a network that fails after passing becomes `UNAVAILABLE` |
| `DELETE /networks/{id}` | `network:manage` | 409 `NETWORK_IN_USE` while clusters that are not deleted use it. The VPC is never touched |
| `GET /cloud-accounts/{id}/catalog` | `cloud_account:read` | Regions/zones and storage types |
| `GET /cloud-accounts/{id}/machine-types?zone=` | `cloud_account:read` | Supported machine types in a zone |
| `GET /engines` | authenticated | Engine catalog: exact versions with status and license-review status, limits, metrics, node groups of the dedicated layout, changeable settings |
| `POST /clusters` | `cluster:create` | **202** `{cluster_id, operation_id, lifecycle}` |
| `GET /clusters` | `cluster:read` | `?include_deleted=true&environment_id=`; each cluster has `lifecycle`, `health`, `environment`, `network`, `zones` |
| `GET /clusters/{id}` | `cluster:read` | Desired/actual state, nodes, health details |
| `POST /clusters/{id}/scale` | `cluster:scale` | `{node_count, group?}` larger than now → **202**; `group` (`data` or `coordinating`) for the dedicated layout |
| `GET /clusters/{id}/config` | `cluster:read` | Settings catalog, `desired` and `applied` settings, `pending` keys, `custom` elasticsearch.yml settings, `config_file` (the rendered elasticsearch.yml per node group and the platform-owned keys) |
| `PUT /clusters/{id}/config` | `cluster:configure` (Owner, Admin) | `{settings: {key: value \| null}}` → **202**, an `UPDATE_CONFIG` operation; see [Configuration](#configuration) |
| `DELETE /clusters/{id}?confirm=<name>` | `cluster:delete` | **202**; see [Deleting a cluster](#deleting-a-cluster) |
| `POST /clusters/{id}/health-check` | `cluster:health_check` | **202**, a `HEALTH_CHECK` operation |
| `GET /clusters/{id}/health` | `cluster:read` | Lifecycle, health, infrastructure and engine components, reasons, warnings, per-node health |
| `GET /clusters/{id}/metrics?minutes=60` | `cluster:read` | Current summary, per-node, history |
| `GET /clusters/{id}/nodes` | `cluster:read` | `lifecycle`, `health`, `instance_status`, `agent_status` |
| `GET /clusters/{id}/events`, `GET /events` | `cluster:read` | Failure detection and lifecycle events |
| `GET /operations` | `operation:read` | `?cluster_id=&status=&type=&limit=&offset=` |
| `GET /operations/{id}` | `operation:read` | Steps, log, error, params, result, `cancellable`, `retry_of` |
| `POST /operations/{id}/cancel` | `operation:manage` + the type's permission | Pending: immediate; running: at the next safe point; deletes: 409 |
| `POST /operations/{id}/retry` | `operation:manage` + the type's permission | Failed or cancelled operations; returns the new operation |
| `GET /audit-logs` | `audit:read` | `?action=&status=&user=&resource_id=&limit=&offset=` |
| `POST /agent/register`, `/agent/heartbeat`, `/agent/commands/{id}/result` | node agents | VM identity / agent token, not user sessions |
| `POST /mock/clusters/{id}/faults` | `cluster:operate` | Mock mode only |
| `POST /mock/clusters/{id}/nodes/{node}/identity-token` | `cluster:operate` | Mock mode only |

## Creating a cluster

```http
POST /api/v1/clusters
Idempotency-Key: 5f0e6f7c-...

{
  "name": "production-search",
  "engine": "elasticsearch",
  "version": "9.5.4",
  "environment_id": "…",
  "network_id": "…",
  "zone": "asia-south1-a",
  "machine_type": "e2-standard-8",
  "node_count": 3,
  "storage_gb": 500,
  "storage_type": "pd-balanced",
  "high_availability": true
}
```

```json
{"cluster_id": "…", "operation_id": "…", "lifecycle": "CREATING"}
```

- `version` must be an exact version from the catalog (`GET /engines`); omit it for the
  catalog default. `latest`, `9.x` and `9.5` are rejected.
- Placement comes from the registered network ([ADR 0013](adr/0013-environments-and-registered-networks.md)):
  it must belong to the environment and be `AVAILABLE`, and its cloud account must be `CONNECTED`.
  The cloud account and region are the network's (`cloud_account_id` and `region` are optional
  and must match if sent).
- `zone` is optional: the network's first zone by default. With `high_availability`, nodes are
  spread over three zones starting with `zone`; on AWS the network needs subnets in three
  availability zones (`422`, field `high_availability`).
- The subnets must have a free address per node (`422`, field `node_count`).
- Poll `GET /operations/{operation_id}` for progress.

### Dedicated master, data and coordinating nodes

[ADR 0016](adr/0016-ha-topology-dedicated-roles.md). Send `layout: "dedicated"` and
`node_groups` instead of `machine_type`, `node_count` and `storage_gb`:

```json
{
  "name": "production-search",
  "environment_id": "…",
  "network_id": "…",
  "storage_type": "pd-balanced",
  "high_availability": true,
  "layout": "dedicated",
  "node_groups": {
    "master": {"count": 3, "machine_type": "e2-standard-4", "storage_gb": 20},
    "data": {"count": 3, "machine_type": "e2-standard-8", "storage_gb": 500},
    "coordinating": {"count": 2, "machine_type": "e2-standard-4", "storage_gb": 20}
  },
  "config": {"search.max_buckets": 20000}
}
```

- Exactly 3 masters, one per zone. With `high_availability`: at least 2 data and 2
  coordinating nodes, three zones. Errors name the field, e.g. `node_groups.master.count`.
- All groups use the same CPU architecture.
- Nodes are named `master-N`, `data-N` and `coord-N`. The cluster's `endpoint` is the
  internal load balancer (`https://<private-ip>:9200`) in front of the coordinating nodes.
- `config` (optional, both layouts) sets initial [settings](#configuration).

## Configuration

[ADR 0017](adr/0017-configuration-management.md), [ADR 0018](adr/0018-elasticsearch-yml-settings.md).
`GET /engines` and `GET /clusters/{id}/config` list the typed settings. Besides them, **any
elasticsearch.yml setting** can be sent, except the ones the platform owns (`xpack.security.*`,
`network.*`, `http.port`, `transport.*`, `discovery.*`, `cluster.name`, `node.name`, `node.roles`,
`node.attr.*`, `path.*`, `bootstrap.*`, zone awareness; the full list is `config_file.reserved_prefixes`).

```http
PUT /api/v1/clusters/{id}/config
Idempotency-Key: …

{"settings": {"cluster.routing.allocation.disk.watermark.low": "80%", "thread_pool.write.queue_size": 20000,
              "search.max_buckets": null}}
```

- `null` goes back to the default (removes the line). Typed values are validated and normalized
  (`"100 MB"` → `"100mb"`); disk watermarks must increase. Other elasticsearch.yml settings take
  single-line text, numbers, booleans or lists (written comma-separated), are written as quoted
  strings, and at most 64 of them. Rejected keys come back in `details.fields` (`422`).
- If Elasticsearch refuses a setting on restart (unknown setting, unparsable value), that node is
  put back on its previous elasticsearch.yml, the operation fails with `CONFIG_REJECTED` and
  Elasticsearch's reason, and no other node is changed. Correct or remove the setting and apply
  again.
- `dynamic` settings apply live (the elected master's agent runs `PUT _cluster/settings`).
  `static` ones need a rolling restart: one node at a time, data nodes first, then coordinating,
  then masters with the elected master last, waiting for the cluster to be healthy after each.
- The cluster moves to `UPDATING` and keeps serving. One change at a time (`409`); nothing to
  change returns `422 NO_CHANGE`. A failed change returns the cluster to `ACTIVE`; retry resumes
  the rolling restart where it stopped.
- Audit events `CLUSTER_CONFIG_UPDATE_STARTED` and `CLUSTER_CONFIG_UPDATED`.

## Scaling

`POST /clusters/{id}/scale` with `{"node_count": 5}` adds nodes to an `ACTIVE` cluster. Fewer
nodes returns `422 SCALE_DOWN_NOT_SUPPORTED`; the same number returns `422 NO_CHANGE`. The
cluster's cloud account must be `CONNECTED` and its network `AVAILABLE` (`409
NETWORK_NOT_AVAILABLE`); the operation re-checks the network in the cloud first and fails with
`NETWORK_CHANGED` if a subnet disappeared or its range changed. If scaling fails, the cluster stays `ACTIVE` with
desired state ahead of actual state; retry the operation.

## Deleting a cluster

```http
DELETE /api/v1/clusters/{id}?confirm=production-search
```

- Without `confirm`, or with a different name: `422 CONFIRMATION_REQUIRED`.
- The cluster moves to `DELETING`. Its queued operations are cancelled at once and running
  ones stop at their next safe point; the delete starts when they have stopped.
- Repeating the request returns the delete already in progress (`202`, same `operation_id`).
- A confirmed delete cannot be cancelled (`409 DELETE_NOT_CANCELLABLE`); a failed one can be
  retried.

## Errors

Every error uses one envelope and never includes a stack trace:

```json
{
  "error": {
    "code": "SCALE_DOWN_NOT_SUPPORTED",
    "message": "Scaling down (3 to 2 nodes) is not supported.",
    "reason": "Removing nodes needs shard relocation that the platform does not perform yet.",
    "suggested_action": "Choose more than 3 nodes.",
    "details": {"fields": {"node_count": "Must be more than 3."}},
    "request_id": "6f1d0c3b2a9e4f10"
  }
}
```

| HTTP | Typical codes |
|---|---|
| 401 | `AUTHENTICATION_FAILED` |
| 403 | `PERMISSION_DENIED` (`details.required_permission`) |
| 404 | `NOT_FOUND` |
| 409 | `CONFLICT` (operation in progress, duplicate name), `IDEMPOTENCY_KEY_REUSED`, `DELETE_NOT_CANCELLABLE`, `CLOUD_ACCOUNT_NOT_CONNECTED` |
| 422 | `VALIDATION_FAILED`, `CONFIRMATION_REQUIRED`, `SCALE_DOWN_NOT_SUPPORTED`, `NO_CHANGE`, with `details.fields` |
| 429 | `RATE_LIMITED` (login) |

Failed operations carry the same structure in `error` (for example `GCP_PERMISSION_DENIED`,
`GCP_QUOTA_EXCEEDED`, `GCP_API_DISABLED`, `BOOTSTRAP_FAILED`, `HEALTH_CHECK_TIMEOUT`,
`UNSAFE_PLAN`). Send the `X-Request-ID` response header (or `request_id`) with support
requests; it is in every log line of that request.

## Idempotency

`POST /clusters` and `POST /clusters/{id}/scale` accept `Idempotency-Key`. Repeating the same
request with the same key returns the original result without doing the work twice. Reusing a
key for a different request returns `409 IDEMPOTENCY_KEY_REUSED`. Deletes are idempotent
without a key.

## Audit events

| Event | When |
|---|---|
| `CLUSTER_CREATE_STARTED`, `CLUSTER_SCALE_STARTED`, `CLUSTER_DELETE_STARTED`, `CLUSTER_HEALTH_CHECK_REQUESTED` | A user's request was accepted |
| `CLUSTER_CREATED`, `CLUSTER_SCALE_COMPLETED`, `CLUSTER_DELETE_COMPLETED`, `CLUSTER_HEALTH_CHECK_COMPLETED` | The operation completed |
| `OPERATION_FAILED` (status `FAILURE`), `OPERATION_CANCELLED` | The operation failed, or was cancelled (by a user or by a delete) |
| `OPERATION_CANCEL_REQUESTED`, `OPERATION_RETRIED` | User actions on operations |
| `CLOUD_ACCOUNT_CREATED`, `CLOUD_ACCOUNT_VALIDATED` (`SUCCESS` or `FAILURE`), `CLOUD_ACCOUNT_DELETED` | Cloud accounts |
| `MEMBER_ADDED`, `MEMBER_ROLE_CHANGED`, `MEMBER_REMOVED` | Membership |
| `LOGIN_SUCCEEDED`, `LOGIN_FAILED`, `USER_SIGNED_UP`, `ORGANIZATION_SWITCHED` | Sessions |
| `FAULT_INJECTED` | Mock mode |

```json
{
  "user": "operator@acme.example",
  "organization": "Acme Corp",
  "action": "CLUSTER_SCALE_STARTED",
  "resource": "production-search",
  "resource_id": "…",
  "timestamp": "2026-09-27T12:00:00Z",
  "status": "SUCCESS",
  "operation_id": "…",
  "details": {"operation_type": "SCALE_CLUSTER", "params": {"from": 3, "to": 5}}
}
```

Audit details never contain secrets.
