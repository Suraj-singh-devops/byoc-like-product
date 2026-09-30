# ADR 0009: Roles and permissions

- Status: Accepted (implemented in P1)
- Date: 2026-09-27
- Related: PRD v2 FR-3; TRD v2 §26, §27

## Context

PRD v2 names the roles Owner, Admin, Operator and Viewer without defining them. v1 had a
Developer role that could manage only the clusters it created.

## Decision

Permissions are granted per action; roles are fixed sets of permissions. Every role can read
everything inside its own organization, and nothing outside it (other organizations' resources
return 404).

| Action | Permission | Owner | Admin | Operator | Viewer |
|---|---|:-:|:-:|:-:|:-:|
| View clusters, nodes, health, metrics, events | `cluster:read` | ✓ | ✓ | ✓ | ✓ |
| View operations | `operation:read` | ✓ | ✓ | ✓ | ✓ |
| View cloud accounts, members, audit logs | `cloud_account:read`, `member:read`, `audit:read` | ✓ | ✓ | ✓ | ✓ |
| Create a cluster | `cluster:create` | ✓ | ✓ | ✓ | |
| Scale a cluster (up only in the MVP) | `cluster:scale` | ✓ | ✓ | ✓ | |
| Run an on-demand health check | `cluster:health_check` | ✓ | ✓ | ✓ | |
| Approved Elasticsearch actions (restart; when available), mock fault injection | `cluster:operate` | ✓ | ✓ | ✓ | |
| Cancel or retry an operation | `operation:manage` plus the permission of the operation's type | ✓ | ✓ | ✓ (not deletes) | |
| Delete a cluster (typed confirmation) | `cluster:delete` | ✓ | ✓ | | |
| Add, validate, remove cloud accounts | `cloud_account:manage` | ✓ | ✓ | | |
| Add members, change roles, remove members | `member:manage` | ✓ | ✓ (never Owners) | | |
| Grant, change or remove the Owner role | | ✓ | | | |

- Operators may manage any cluster in the organization, not only the ones they created.
- Operators cannot manage cloud accounts, members, IAM, billing or platform configuration. The
  last three do not exist in the MVP.
- An organization always keeps at least one Owner.
- Membership and role are read on every request, so removals and role changes apply
  immediately.

## Consequences

- The console enables actions from the permission list returned by `GET /api/v1/auth/me`
  instead of a per-cluster `can_manage` flag.
- Existing Developer memberships become Operator (Alembic revision 0002). Developers could delete
  their own clusters; Operators cannot delete any.
