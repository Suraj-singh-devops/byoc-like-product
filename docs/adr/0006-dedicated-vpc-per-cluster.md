# ADR 0006: Dedicated VPC per cluster; no platform-to-data-plane network path

- Status: Superseded by [ADR 0013](0013-environments-and-registered-networks.md) (2026-09-27).
  New clusters run in a network the customer registers (an existing VPC and subnet). Clusters
  created before P1b keep their dedicated VPC. The "no platform-to-data-plane connectivity"
  section below still applies.
- Date: 2026-09-27
- Related: PRD v2 §12 (network); TRD v2 §10, §28, §43

## Context

Elasticsearch must use private IPs, must never be reachable from `0.0.0.0/0` on 9200, and the
TRD forbids assuming network connectivity that was not explicitly configured. Supporting
arbitrary existing customer VPCs means handling shared VPCs, overlapping ranges, existing
firewall policies and organization policies, which is a large surface for a first MVP.

## Decision

### Network per cluster

```text
Customer project
  └─ cluster VPC  (<prefix>-vpc, custom mode)
       └─ cluster subnet (<prefix>-subnet, 10.10.0.0/24 by default, Private Google Access on)
            └─ Elasticsearch nodes (no external IP)
       ├─ Cloud Router + Cloud NAT (egress only, for package downloads)
       └─ firewall: 9200 and 9300 from the cluster subnet only; nothing else inbound
```

- Every cluster gets its own VPC and subnet, created and destroyed with the cluster.
- No public IPs on nodes; the compute module refuses them and a Terraform test asserts it.
- Client access from the customer's applications is the customer's network decision (for
  example peering the cluster VPC with an application VPC and adding allowed client ranges). The
  MVP opens nothing beyond the cluster subnet by default.

### Designed for existing VPCs later

- The `network` module already accepts `create_network = false` with an existing network and
  subnet; compute and storage take only a subnet self-link.
- Firewall rules move into their own `firewall` module (TRD §3) that targets any network by tag,
  so an existing-VPC mode can reuse it unchanged.
- The subnet range becomes a request field before existing-VPC or peering support, so that
  clusters peered with the same application network do not overlap.

### No platform-to-data-plane connectivity

- The platform manages infrastructure only through Google APIs and Terraform, and reads node
  state through the Compute API (instance status, guest attributes). It never opens a
  connection to Elasticsearch or to the VMs.
- Agents talk outbound only: guest attributes always; HTTPS heartbeats to the platform through
  Cloud NAT when an agent endpoint is published ([ADR 0007](0007-outbound-only-agent.md)).
- No VPC peering, Private Service Connect or VPN between the platform and customers unless a
  concrete requirement appears; if it does, it gets its own record.

## Consequences

- One VPC per cluster uses the customer's VPC quota (default 15 per project).
- Package downloads need Cloud NAT (or, later, a package mirror reachable through Private Google
  Access).
- A customer who wants applications to reach Elasticsearch must connect networks themselves in
  the MVP; this is documented in the onboarding guide.
