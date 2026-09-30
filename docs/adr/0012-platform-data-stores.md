# ADR 0012: Cloud SQL for PostgreSQL; Redis inside GKE

- Status: Accepted (applies from P3)
- Date: 2026-09-27
- Related: PRD v2 §14; TRD v2 §29

## Decision

- **PostgreSQL: Cloud SQL for PostgreSQL**, private IP only, in the platform project, with
  automated backups, point-in-time recovery and IAM database authentication for the platform's
  Google identities (no database passwords in Kubernetes). Accessed through the Cloud SQL Auth
  Proxy sidecar or the language connector. PostgreSQL is the source of truth for desired state,
  actual state, operations and audit.
- **Redis: a single Redis instance inside GKE** (StatefulSet with a persistent volume, AOF on),
  used for the operation queue, leader lock and rate limits. Losing Redis loses no state: queue
  messages are re-created from PostgreSQL by the reaper, and locks and rate limits reset. A
  managed service (Memorystore) can replace it later without code changes.
- Docker Compose keeps local PostgreSQL and Redis containers for development.

## Consequences

- The Helm chart supports an external PostgreSQL (Cloud SQL) as the default and keeps an in-cluster
  PostgreSQL option only for throw-away environments.
- Schema migrations run as a Kubernetes Job before the Deployments roll out, serialized by the
  existing advisory lock.
