# Architecture decision records

Decisions locked before the v2 implementation (2026-09-27), plus 0013 and 0014 added in P1b.
Each record states the context, the decision, what it rules out and its consequences. Change a decision by adding a new record that
supersedes the old one; do not rewrite accepted records.

| # | Decision | Status |
|---|---|---|
| [0001](0001-evolve-the-v1-prototype.md) | Evolve the v1 prototype instead of rewriting it | Accepted |
| [0002](0002-elasticsearch-distribution-and-licensing.md) | Elasticsearch distribution, exact version pinning and licensing review | Accepted (licensing: review pending) |
| [0003](0003-customer-access-per-organization-identities.md) | Customer access through per-organization identities; consent proven by the customer's own IAM grant | Accepted |
| [0004](0004-privilege-separated-components.md) | Four privilege-separated backend components, one identity each | Accepted |
| [0005](0005-terraform-only-writer-state-and-secrets.md) | Terraform is the only infrastructure writer; state and secret handling | Accepted |
| [0006](0006-dedicated-vpc-per-cluster.md) | Dedicated VPC per cluster; no platform-to-data-plane network path | Superseded by 0013 (the no-connectivity rule still applies) |
| [0007](0007-outbound-only-agent.md) | Outbound-only agent with compiled-in allowlisted actions | Accepted |
| [0008](0008-separate-lifecycle-and-health.md) | Lifecycle and health are stored separately | Accepted |
| [0009](0009-roles-and-permissions.md) | Roles and permissions (Owner, Admin, Operator, Viewer) | Accepted |
| [0010](0010-operation-semantics.md) | Operation semantics: concurrency, idempotency, delete pre-emption, scale-up only | Accepted |
| [0011](0011-elasticsearch-topology-and-ha.md) | Elasticsearch topology and high availability in the MVP | Accepted |
| [0012](0012-platform-data-stores.md) | Cloud SQL for PostgreSQL; Redis inside GKE | Accepted |
| [0013](0013-environments-and-registered-networks.md) | Environments and registered networks (existing VPC and subnet) | Accepted (P1b) |
| [0014](0014-aws-support.md) | AWS support, mock first, with keyless per-organization access | Accepted (P1b mock; real AWS in the AWS track) |
| [0015](0015-development-local-credentials.md) | Development-only real GCP with the developer's own credentials, allowlisted sandbox projects | Accepted (temporary, until P4) |
| [0016](0016-ha-topology-dedicated-roles.md) | HA topology with dedicated master, data and coordinating nodes; internal load balancer | Accepted (supersedes part of 0011) |
| [0017](0017-configuration-management.md) | Allowlisted configuration changes: dynamic live, static by rolling restart | Accepted (static allowlist superseded by 0018) |
| [0018](0018-elasticsearch-yml-settings.md) | Any elasticsearch.yml setting except platform-owned ones; rollback of a node that does not start | Accepted |

Related documents: [migration plan](../migration-plan.md), [implementation plan](../implementation-plan.md).
