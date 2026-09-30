# ADR 0015: Development-only real GCP with the developer's own credentials

- Status: Accepted as a temporary development exception (2026-09-27). Removed or replaced when
  per-organization identities land in P4.
- Related: [ADR 0003](0003-customer-access-per-organization-identities.md) (which this bypasses
  for development), [ADR 0005](0005-terraform-only-writer-state-and-secrets.md)

## Context

The product owner wants to test the full flow (cloud account → environment → registered network
→ real 3-node cluster → delete) against a real sandbox project now, before P2–P5. The platform
identities of ADR 0003 do not exist yet, so real mode is refused.

## Decision

A development-only mode, `DEV_LOCAL_CREDENTIALS=true`:

- The platform (API calls and Terraform) uses the application default credentials of the
  machine it runs on, directly, with API quota billed to the target project. No service
  account is impersonated.
- Refused unless `ENVIRONMENT=development`, and unless `DEV_ALLOWED_PROJECTS` lists the projects
  it may reach. The allowlist is enforced when a cloud account is registered, before every API
  client is built and before every Terraform run, so no other project (for example a production
  project) can be touched even through an existing record.
- Opt-in through `docker-compose.real-gcp.yml`, which mounts the credential file read-only.
- The console hides the service-account field and says the developer's own credentials are used.

## Consequences

- Terraform runs with everything the developer can do in the sandbox project, not least
  privilege. Acceptable for a sandbox only.
- This mode must never be deployed; P3's Helm chart does not expose these settings.
- Everything else (Terraform modules, registered networks, bootstrap, monitoring) is the real
  path, so this run exercises P5–P6 behaviour early.
