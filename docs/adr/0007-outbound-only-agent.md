# ADR 0007: Outbound-only agent with compiled-in allowlisted actions

- Status: Accepted
- Date: 2026-09-27
- Related: PRD v2 §11; TRD v2 §12, §13, §43

## Context

TRD §12 lists agent endpoints such as `GET /agent/health` and
`POST /operations/restart-elasticsearch`, which read as a management API listening on every
VM. TRD §13 and PRD §11 prefer outbound-only communication so that no management endpoint is
exposed on the VMs. The two are reconciled here.

## Decision

- **Nothing listens on the VMs** for management traffic. The endpoints in TRD §12 are
  implemented as platform endpoints the agent calls (`POST /api/v1/agent/register`,
  `POST /api/v1/agent/heartbeat`, `POST /api/v1/agent/commands/{id}/result`), and health,
  metrics and node metadata travel inside the agent's report.
- **Two reporting channels:**
  1. GCE guest attributes, always on. The agent and the startup script write reports to the VM's
     guest attributes; the monitoring worker reads them through the Compute API with the
     organization's read identity. This works with no network path to the platform.
  2. HTTPS heartbeats to the platform's agent endpoint, when one is published and reachable
     through Cloud NAT. Heartbeat responses carry pending approved actions.
- **Authentication:** registration uses a Google-signed VM identity token (audience = the
  platform) that must match a known node of a known cluster (project, zone, instance). The agent
  then receives a per-node token; only its hash is stored. Short-lived tokens with automatic
  renewal replace the long-lived per-node token in P7.
- **Allowlisted actions only.** The allowlist is compiled into the agent and repeated in the
  control plane; arguments are decoded strictly and validated; nothing reaches a shell; commands
  run with fixed argument lists. For the MVP the allowlist is `restart_engine` only (reserved for
  the Operator's "approved Elasticsearch operations"; no workflow uses it yet). `drain_nodes` and
  `undrain_nodes` were removed with scale-down and come back with it.
- No arbitrary command execution, file transfer or remote shell, now or later.

## Consequences

- The agent needs no inbound firewall rule.
- Without a published agent endpoint the platform still sees health (guest attributes), but
  cannot deliver actions; workflows that need actions must say so before they start.
- Publishing the agent endpoint on the internet requires Cloud Armor rate limiting and
  authentication at the edge; that exposure decision is part of P7.
