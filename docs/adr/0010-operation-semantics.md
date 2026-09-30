# ADR 0010: Operation semantics — concurrency, idempotency, delete pre-emption, scale-up only

- Status: Accepted (implemented in P1)
- Date: 2026-09-27
- Related: PRD v2 FR-5, FR-7, FR-8, FR-9, success criterion 15; TRD v2 §16, §17, §19, §20, §23, §43

## Context

Long-running work must never block HTTP requests, conflicting operations must never run at the
same time, retries must never create duplicate resources, and deletion must stop other platform
operations and require explicit confirmation. PRD FR-9 ("deletion must stop platform
operations") and TRD §20 ("reject or queue conflicting operations") pull in different
directions for delete. Scale-down needs safe data movement that the MVP does not include.

## Decision

### Operations

- Every long-running action creates an operation row and a queue message in the same request,
  and the API returns `202 Accepted` with `{cluster_id, operation_id, lifecycle}`. Workers do the
  work.
- States: `PENDING → VALIDATING → PROVISIONING → BOOTSTRAPPING → CONFIGURING → HEALTH_CHECK →
  COMPLETED`, with `FAILED` and `CANCELLED` as the other terminal states. States only move
  forward; steps may skip states they do not need. A running operation whose worker died goes
  back to `PENDING` (lease expiry) and is retried from the start, at most three attempts.
- Every workflow step is idempotent: Terraform converges on the same resources, and waits
  re-read state. A retry is a new operation linked by `retry_of`.

### Concurrency

- At most one active mutating operation (create, scale, delete, upgrade, restore) per cluster,
  enforced by partial unique indexes in the database, not only by application checks. Health
  checks do not count.
- A conflicting request is **rejected** with `409` and the ID of the active operation. Requests
  are not queued, with one exception: delete.
- Mutation requests lock the cluster row (`SELECT … FOR UPDATE`) so that concurrent requests
  are serialized.

### Delete pre-emption

1. `DELETE /api/v1/clusters/{id}?confirm=<cluster name>`. A missing or wrong confirmation returns
   `422 CONFIRMATION_REQUIRED`. Only Owner and Admin may delete.
2. If a delete is already in progress, the same operation is returned (`202`): delete is
   idempotent.
3. Otherwise the cluster moves to `DELETING`; `PENDING` operations of the cluster are cancelled at
   once (`superseded by deletion`); running ones are asked to stop at their next safe point; the
   delete operation is created `PENDING`.
4. The delete starts only when no other mutation of the cluster is still running (checked in the
   claim, in the database). When the pre-empted operation stops, it wakes the delete.
5. A delete cannot be cancelled once accepted; a failed delete can be retried.

Safe points are step boundaries and waits. A Terraform `apply` in progress is never interrupted
by a cancellation; the operation stops after it returns, so state and infrastructure stay
consistent. (A Terraform timeout still interrupts it, after a graceful signal.)

### Scale-up only

- `POST /api/v1/clusters/{id}/scale` accepts only `node_count` greater than the current count
  (3 → 5, 5 → 7). Fewer nodes returns `422 SCALE_DOWN_NOT_SUPPORTED`; the same count returns
  `422 NO_CHANGE`. The cluster must be `ACTIVE`.
- The desired state is updated first (generation + 1), then the workflow plans and applies the
  new VMs and disks, bootstraps them, waits for them to join, checks health, and records the
  actual state (observed generation = generation).
- If scaling fails or is cancelled, the cluster returns to `ACTIVE` with desired state ahead of
  actual state and a status message. The operation can be retried; rolling back would be a
  scale-down, which is not available, so the other way out is to delete the cluster.
- The Terraform plan guard refuses any plan that destroys or replaces a VM or data disk.

### Idempotency and cancellation

- `Idempotency-Key` on create and scale, scoped to the organization, stored with a hash of the
  request. The same key and request return the original operation; the same key with a different
  request returns `409 IDEMPOTENCY_KEY_REUSED`.
- Cancelling a `PENDING` operation cancels it immediately; cancelling a running one takes effect
  at the next safe point. Cancel and retry need `operation:manage` plus the permission for the
  operation's type ([ADR 0009](0009-roles-and-permissions.md)).

## Consequences

- A cluster whose scale-up failed permanently (for example on quota) can only be retried or
  deleted until scale-down exists.
- The delete may wait up to the duration of one Terraform apply before it starts.
- The claim query checks for other running mutations, so a delete that was dequeued too early
  is simply claimed later when it is woken (or by the reaper as a fallback).
