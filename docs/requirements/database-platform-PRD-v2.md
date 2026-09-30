# Product Requirements Document (PRD)
## Database Infrastructure Platform — GKE Control Plane / Customer-Owned Data Plane

**Status:** Prototype / MVP  
**Initial Cloud:** Google Cloud Platform (GCP)  
**Initial Database:** Elasticsearch  
**Primary Deployment:** GKE  
**Future Clouds:** AWS, Azure  
**Future Databases:** Redis, MySQL, PostgreSQL, MongoDB, OpenSearch

---

## 1. Product Summary

The Database Infrastructure Platform provides a managed-database experience while keeping the actual database infrastructure inside the customer's GCP environment.

The platform control plane runs on GKE. It manages customer-owned infrastructure through GCP APIs and Terraform, while Elasticsearch runs on customer-owned Compute Engine VMs and Persistent Disks.

### Core proposition

> Managed database operations without requiring the customer to move their database infrastructure or data into a third-party managed service.

The platform should make database provisioning and operations feel simple while abstracting infrastructure complexity.

---

## 2. Problem

Teams that run Elasticsearch themselves must handle:

- VM provisioning
- Persistent storage
- networking
- firewall rules
- IAM
- Elasticsearch installation
- TLS/security
- cluster formation
- health monitoring
- scaling
- upgrades
- node failures
- backups and recovery
- operational troubleshooting

Cloud-native managed services solve much of this but can introduce:

- less infrastructure control
- data residency concerns
- additional service/vendor dependency
- opaque or difficult-to-compare costs
- migration friction

This product aims to provide the operational experience of a managed service while keeping the data plane in the customer's cloud account.

---

## 3. Product Architecture

### Control Plane

Runs in the platform-owned GCP project on GKE.

Components:

- Frontend
- API
- Cluster Manager
- Provisioning Worker
- Operation Worker
- Monitoring Worker
- Terraform Runner
- PostgreSQL
- Redis
- Authentication/RBAC
- Audit logging

### Data Plane

Runs in the customer's GCP project/VPC.

Initial resources:

- VPC/subnet
- firewall rules
- service accounts
- Compute Engine VMs
- Persistent Disks
- Elasticsearch
- platform management agent

### High-level flow

```text
User
  |
  v
Platform GKE
  |
  +-- API
  +-- Cluster Manager
  +-- Workers
  +-- Terraform Runner
  |
  | Workload Identity / IAM
  v
Customer GCP Project
  |
  +-- VPC
  +-- Elasticsearch VMs
  +-- Persistent Disks
  +-- Elasticsearch Agent
```

---

## 4. Target Users

### Primary

- Small and medium engineering teams
- DevOps/platform teams
- Companies with GCP infrastructure
- Teams running self-managed Elasticsearch
- Teams that want infrastructure ownership but reduced database operations work

### Secondary

- Companies migrating from VM-based Elasticsearch
- Teams with security/data-residency requirements
- Platform teams wanting standardized database provisioning

---

## 5. Product Principles

1. Customer owns the data plane.
2. Platform owns the management experience.
3. No public Elasticsearch endpoints by default.
4. Infrastructure is declarative and idempotent.
5. Long-running operations are asynchronous.
6. Security is enabled by default.
7. Platform components use least-privilege IAM.
8. Kubernetes workloads use Workload Identity rather than service-account keys.
9. Database operations are controlled through explicit APIs/agent operations.
10. Cloud and database providers must be abstracted behind interfaces.

---

# 6. MVP Scope

## 6.1 Cloud

Only GCP.

The platform control plane runs on a GKE cluster.

## 6.2 Database

Only Elasticsearch.

The implementation must support an explicitly approved Elasticsearch version. Do not blindly use `latest`.

The exact Elasticsearch distribution and applicable license must be recorded before implementation and before any public/commercial release.

## 6.3 Infrastructure

Initial data-plane infrastructure:

- VPC
- subnet
- firewall
- service account
- Compute Engine VM
- Persistent Disk
- Elasticsearch
- management agent

## 6.4 Platform

MVP must provide:

- authentication
- organization
- basic RBAC
- GCP project connection
- GCP access validation
- Elasticsearch cluster creation
- asynchronous provisioning
- cluster status
- node status
- health status
- basic metrics
- manual scaling
- cluster deletion
- operation history
- audit logs
- mock provider mode for development/testing

---

# 7. Explicitly Out of Scope for MVP

- AWS
- Azure
- Redis
- MySQL
- PostgreSQL
- MongoDB
- OpenSearch
- automatic scaling
- multi-region deployment
- full disaster recovery
- enterprise SSO
- advanced billing
- AI database operations
- arbitrary shell execution
- Kubernetes-based Elasticsearch data plane
- advanced backup lifecycle
- full production-grade HA automation
- advanced upgrade orchestration

---

# 8. User Journey

## Connect Cloud

1. User logs in.
2. Creates/selects an organization.
3. Adds a GCP project.
4. Platform validates:
   - project access
   - required APIs
   - required IAM permissions
   - network prerequisites
5. Cloud account becomes `CONNECTED`.

## Create Elasticsearch Cluster

User provides:

- cluster name
- Elasticsearch version
- GCP project
- region
- zone
- machine type
- node count
- disk size
- disk type
- HA option

Platform:

1. Creates an operation.
2. Validates request.
3. Generates desired state.
4. Runs Terraform.
5. Creates infrastructure.
6. Bootstraps Elasticsearch.
7. Installs/configures the management agent.
8. Performs health checks.
9. Marks cluster `HEALTHY`.

The API must return immediately after creating the asynchronous operation.

---

# 9. Functional Requirements

## FR-1 Authentication

Users can:

- sign up/login
- retrieve current user
- logout

## FR-2 Organization

Users belong to organizations.

Organizations isolate:

- clusters
- cloud accounts
- operations
- audit logs

## FR-3 RBAC

MVP roles:

- Owner
- Admin
- Operator
- Viewer

## FR-4 GCP Cloud Account

Users can:

- add GCP project
- validate project
- view connection status
- remove project

## FR-5 Cluster Creation

The platform must support:

```text
POST /api/v1/clusters
```

The request must include:

- name
- engine
- version
- cloud_account_id
- region
- zone
- machine_type
- node_count
- storage_gb
- storage_type
- high_availability

Support an idempotency key.

## FR-6 Cluster Lifecycle

Supported states:

```text
CREATING
HEALTHY
DEGRADED
SCALING
UPGRADING
FAILED
DELETING
DELETED
```

## FR-7 Operations

Every long-running action must create an operation.

Example:

```text
PENDING
  ↓
VALIDATING
  ↓
PROVISIONING
  ↓
BOOTSTRAPPING
  ↓
CONFIGURING
  ↓
HEALTH_CHECK
  ↓
COMPLETED
```

Failure:

```text
FAILED
```

## FR-8 Scaling

MVP supports manual scaling.

Example:

```text
3 nodes → 5 nodes
```

The platform must:

1. Update desired state.
2. Create required VMs/disks.
3. Bootstrap nodes.
4. Join nodes to the Elasticsearch cluster.
5. Validate health.
6. Update actual state.

## FR-9 Delete

Delete requires explicit confirmation.

Deletion must:

- stop platform operations
- remove Elasticsearch resources
- remove infrastructure created by the platform
- update desired/actual state
- record an audit event

---

# 10. Monitoring

MVP metrics:

### Infrastructure

- CPU
- memory
- disk
- disk utilization
- network
- load

### Elasticsearch

- cluster health
- node health
- JVM heap
- JVM pressure
- indexing rate
- search rate
- shard count
- disk usage
- Elasticsearch version

---

# 11. Management Agent

Each Elasticsearch node runs a small management agent.

The agent provides:

- heartbeat
- CPU/memory/disk metrics
- Elasticsearch health
- node information
- Elasticsearch version
- approved operational actions

The agent must NOT expose arbitrary shell execution.

Communication should be outbound and authenticated.

---

# 12. Security Requirements

## GCP

Use:

- Workload Identity Federation for GKE
- least-privilege IAM
- separate Kubernetes ServiceAccounts
- no service-account JSON keys in pods

Suggested platform identities:

```text
platform-api
cluster-manager
provisioning-worker
terraform-runner
monitoring-worker
```

Permissions should be scoped to the required resources/actions.

## Network

Default:

- private IP
- no public Elasticsearch endpoint
- restricted firewall
- controlled management traffic

## Elasticsearch

Enable:

- authentication
- authorization
- TLS
- secure transport
- secure HTTP
- credential rotation design

Never store credentials in logs.

---

# 13. UI Requirements

## Dashboard

Show:

- organizations
- cloud accounts
- clusters
- health
- operations

## Create Cluster

Form fields:

- cluster name
- Elasticsearch version
- GCP project
- region
- zone
- machine type
- node count
- disk size/type
- HA

## Cluster Details

Show:

- cluster status
- health
- nodes
- resources
- version
- metrics
- recent operations
- audit history

Actions:

- scale
- delete

---

# 14. GKE Deployment Requirements

The platform itself is deployed to GKE.

Minimum components:

```text
database-platform namespace

frontend
api
cluster-manager
provisioning-worker
monitoring-worker
terraform-runner
postgresql
redis
```

For production, PostgreSQL should preferably be an external managed PostgreSQL service rather than a database inside the same GKE cluster.

Docker Compose remains optional for local developer convenience only.

---

# 15. Success Criteria

Prototype is successful when:

1. Platform runs on GKE.
2. User can authenticate.
3. User can connect a GCP project.
4. Platform validates IAM/API requirements.
5. User can create an Elasticsearch cluster.
6. Terraform provisions real GCP infrastructure.
7. Elasticsearch is installed/configured.
8. Agent reports health.
9. Platform shows cluster/node health.
10. User can scale the cluster.
11. User can delete the cluster.
12. All long-running operations are tracked.
13. Audit events are recorded.
14. No public Elasticsearch endpoint is required.
15. Retrying an operation does not create duplicate infrastructure.

---

# 16. Roadmap

## Phase 1 — GCP + Elasticsearch Prototype

- GKE control plane
- Workload Identity
- Terraform
- Elasticsearch VM provisioning
- agent
- monitoring
- create/scale/delete

## Phase 2 — Production Hardening

- HA
- automatic recovery
- backup
- restore
- rolling upgrades
- alerting
- stronger IAM isolation
- operational safety controls

## Phase 3 — Redis

## Phase 4 — MySQL

## Phase 5 — PostgreSQL

## Phase 6 — AWS

## Phase 7 — AI Operations

---

# 17. Business Hypothesis

The product should not be positioned as a generic clone of existing managed database providers.

The hypothesis is:

> Teams want managed database operations while retaining ownership, network placement, cloud billing, and infrastructure control.

The first validation should compare:

- operational effort
- infrastructure cost
- security/control
- migration effort
- support burden

against self-managed deployments and existing managed services.

Customer interviews should validate willingness to pay before major commercial investment.

---

# 18. Product Risks

### Competition

Existing managed database platforms already provide managed operations and some support for customer-owned cloud environments.

Therefore the product needs a clear wedge.

### Security

The platform can potentially control customer cloud infrastructure.

This makes IAM isolation, auditability, tenant isolation, and operation authorization critical.

### Terraform safety

Incorrect Terraform changes can destroy customer resources.

All destructive operations need safeguards.

### Elasticsearch licensing

The exact Elasticsearch distribution/version/license must be reviewed before commercialization or redistribution.

### Multi-tenancy

A compromised control-plane component must not provide uncontrolled access to all customer environments.

---

# 19. MVP Non-Goals

Do not add complexity before the end-to-end lifecycle works.

Avoid:

- multiple clouds
- multiple databases
- AI agents
- advanced billing
- multi-region
- Kubernetes Elasticsearch clusters
- complex service mesh
- arbitrary remote execution

The first goal is:

> GKE platform → customer GCP project → Elasticsearch cluster → monitor → scale → delete.
