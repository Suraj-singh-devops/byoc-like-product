# ADR 0001: Evolve the v1 prototype instead of rewriting it

- Status: Accepted
- Date: 2026-09-27

## Context

A working prototype exists, built from PRD v1: FastAPI control plane, Next.js console, Go agent,
Terraform modules and a mock mode, with about 190 backend tests, Go and Terraform tests and an
end-to-end acceptance test. PRD v2 and TRD v2 keep the product and the data plane but change the
control-plane deployment (GKE), the identity model (Workload Identity, no keys), the component
split, the vocabulary and several rules (scale-up only, exact versions, delete confirmation).

## Decision

Evolve the existing code base. Each phase of the [implementation plan](../implementation-plan.md)
leaves the application runnable with every test suite green, and mock mode stays the default for
development and CI.

- Where v1 already meets v2 (operations, idempotency, tenancy, plan guard, agent protocol, mock
  data plane), keep it.
- Where v1 conflicts with v2 (key upload, scale-down, version aliases, Developer role, state
  names), refactor or remove it, with an Alembic data migration for stored values.
- The TRD's suggested repository layout is adopted where it clarifies ownership (`app/api/v1`,
  `app/domain/states.py`, `deploy/helm`, a separate firewall module). Pure renames that add no
  behaviour (for example splitting `cluster_service.py` into `scaling_service.py`) are not done.

## Consequences

- Breaking API changes are made once, in P1, and the frontend and backend ship together.
- The pre-v2 tree is kept as `.snapshots/v1-prototype-before-p1-2026-09-27.tar.gz` because the
  directory has no version control yet. Initialising git and committing each phase is
  recommended.
- Engine-neutral names from v1 are kept where the TRD schema is Elasticsearch-specific (for
  example `engine_version` rather than `elasticsearch_version`), to honour TRD §43 ("do not
  tightly couple core services to Elasticsearch").
