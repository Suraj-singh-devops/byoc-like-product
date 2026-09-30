# Technical Requirements Document (TRD)
## Database Infrastructure Platform — GKE Control Plane / GCP Data Plane

**Status:** Prototype / MVP  
**Primary Cloud:** GCP  
**Control Plane:** GKE  
**Data Plane:** Customer GCP project/VPC  
**Initial Database:** Elasticsearch

---

# 1. Technical Architecture

The system is divided into two major planes.

## Control Plane

Runs in the platform-owned GCP project.

```text
Internet
   |
HTTPS Load Balancer / Gateway
   |
GKE
   |
   +-- Frontend
   +-- API
   +-- Cluster Manager
   +-- Provisioning Worker
   +-- Monitoring Worker
   +-- Terraform Runner
   |
   +-- PostgreSQL
   +-- Redis
```

## Data Plane

Runs in the customer's GCP project.

```text
Customer GCP Project
|
+-- VPC
|   |
|   +-- subnet
|   |
|   +-- firewall
|
+-- Elasticsearch VM(s)
|   |
|   +-- Persistent Disk
|   +-- Elasticsearch
|   +-- Management Agent
|
+-- Service Account
```

The control plane communicates with the customer environment through authenticated GCP APIs and private network paths where applicable.

---

# 2. Technology Stack

## Backend

- Python 3.12+
- FastAPI
- Pydantic v2
- SQLAlchemy 2.x
- Alembic

## Database

- PostgreSQL

## Queue/Cache

- Redis

## Frontend

- Next.js
- React
- TypeScript

## Infrastructure

- Terraform or OpenTofu
- GCP
- GKE
- Helm

## Node Agent

- Go

## Local Development

Docker Compose may be provided for local development, but it is NOT the primary deployment architecture.

---

# 3. Repository Structure

```text
database-platform/
|
+-- backend/
|   +-- app/
|       +-- api/
|       |   +-- v1/
|       |       +-- auth.py
|       |       +-- cloud_accounts.py
|       |       +-- clusters.py
|       |       +-- operations.py
|       |       +-- audit_logs.py
|       |
|       +-- domain/
|       |   +-- cluster.py
|       |   +-- operation.py
|       |   +-- providers.py
|       |   +-- states.py
|       |
|       +-- application/
|       |   +-- cluster_service.py
|       |   +-- provisioning_service.py
|       |   +-- scaling_service.py
|       |
|       +-- infrastructure/
|       |   +-- database/
|       |   +-- auth/
|       |   +-- queue/
|       |   +-- logging/
|       |
|       +-- providers/
|       |   +-- cloud/
|       |       +-- base.py
|       |       +-- gcp/
|       |           +-- provider.py
|       |           +-- compute.py
|       |           +-- storage.py
|       |           +-- networking.py
|       |           +-- iam.py
|       |
|       +-- providers/
|           +-- database/
|               +-- base.py
|               +-- elasticsearch/
|                   +-- provider.py
|                   +-- installer.py
|                   +-- health.py
|                   +-- metrics.py
|
+-- frontend/
|
+-- agent/
|   +-- elasticsearch-agent/
|
+-- infrastructure/
|   +-- terraform/
|       +-- gcp/
|           +-- modules/
|           |   +-- network/
|           |   +-- firewall/
|           |   +-- iam/
|           |   +-- storage/
|           |   +-- compute/
|           |   +-- elasticsearch/
|           |
|           +-- environments/
|               +-- prototype/
|
+-- deploy/
|   +-- helm/
|       +-- database-platform/
|
+-- docs/
+-- tests/
```

---

# 4. GKE Architecture

Namespace:

```text
database-platform
```

Deployments:

```text
frontend
api
cluster-manager
provisioning-worker
monitoring-worker
terraform-runner
```

Supporting services:

```text
postgresql
redis
```

Each platform workload should have a dedicated Kubernetes ServiceAccount where practical.

Example:

```text
ksa-platform-api
ksa-cluster-manager
ksa-provisioning-worker
ksa-monitoring-worker
ksa-terraform-runner
```

---

# 5. Workload Identity

Do not mount GCP service-account JSON keys into pods.

Use GKE Workload Identity Federation.

Conceptually:

```text
Kubernetes ServiceAccount
          |
          v
GCP IAM principal
          |
          v
GCP APIs
```

Example:

```text
ksa-provisioning-worker
          |
          v
platform-provisioning-gsa
          |
          +-- Compute permissions
          +-- Disk permissions
          +-- Network permissions
          +-- IAM permissions required for provisioning
```

Permissions must be minimized.

Do not grant project Owner unless absolutely unavoidable during a temporary prototype, and even then replace it with specific permissions before production.

---

# 6. Customer GCP Project Connection

A customer cloud account is represented as:

```text
CloudAccount
```

Minimum fields:

```text
id
organization_id
provider
project_id
region
status
created_at
updated_at
```

Connection states:

```text
PENDING
VALIDATING
CONNECTED
FAILED
DISCONNECTED
```

Validation must verify:

- project exists
- required APIs enabled
- required permissions available
- network prerequisites
- required service accounts/resources can be created

---

# 7. Provider Abstraction

## CloudProvider

```python
class CloudProvider(ABC):

    async def validate_connection(...):
        pass

    async def create_network(...):
        pass

    async def create_compute_instance(...):
        pass

    async def create_disk(...):
        pass

    async def delete_compute_instance(...):
        pass

    async def delete_disk(...):
        pass

    async def get_instance_status(...):
        pass
```

GCP implementation:

```text
GCPProvider
```

Future:

```text
AWSProvider
AzureProvider
```

---

# 8. DatabaseProvider

```python
class DatabaseProvider(ABC):

    async def install(...):
        pass

    async def configure(...):
        pass

    async def health(...):
        pass

    async def metrics(...):
        pass

    async def scale(...):
        pass

    async def remove(...):
        pass
```

Elasticsearch implementation:

```text
ElasticsearchProvider
```

Future:

```text
RedisProvider
MySQLProvider
PostgreSQLProvider
MongoDBProvider
OpenSearchProvider
```

---

# 9. Elasticsearch Installation

Do not use the Elasticsearch Docker quickstart as the production installation mechanism.

Use the official supported Linux installation/package mechanism for the selected Elasticsearch distribution/version.

The version must be explicitly configured.

Example:

```text
ELASTICSEARCH_VERSION=approved-version
```

Do not use:

```text
latest
```

The implementation must maintain a version catalog.

Example:

```yaml
elasticsearch:
  supported_versions:
    - version: "X.Y.Z"
      status: supported
      distribution: "approved-distribution"
```

Before implementation/commercial release, document:

- exact version
- exact distribution
- license
- package source
- checksum/signature verification approach
- trademark considerations

---

# 10. Data Plane Network

Prototype architecture:

```text
Customer VPC
|
+-- GKE/platform connectivity where applicable
|
+-- Elasticsearch subnet
    |
    +-- ES node 1
    +-- ES node 2
    +-- ES node 3
```

Elasticsearch should use private IPs by default.

Never expose:

```text
0.0.0.0/0 -> TCP 9200
```

Firewall rules must be explicit.

Potential ports:

```text
9200 - Elasticsearch HTTP
9300 - Elasticsearch transport
```

Exact ports and security configuration must be validated against the selected Elasticsearch version.

---

# 11. Elasticsearch Node

Each node consists of:

```text
Compute Engine VM
|
+-- OS
+-- Persistent Disk
+-- Elasticsearch
+-- Elasticsearch Agent
```

Storage must be persistent and separated from the VM lifecycle where possible.

---

# 12. Management Agent

The Go agent is installed on every Elasticsearch node.

Responsibilities:

- heartbeat
- CPU metrics
- memory metrics
- disk metrics
- Elasticsearch health
- Elasticsearch version
- node metadata
- approved operational actions

Example API:

```text
POST /agent/heartbeat
GET  /agent/health
GET  /agent/metrics
GET  /agent/node
```

The agent must NOT support arbitrary commands.

Bad:

```text
POST /execute
{
  "command": "rm -rf ..."
}
```

Good:

```text
POST /operations/restart-elasticsearch
POST /operations/join-cluster
```

Each operation must be explicitly authorized.

---

# 13. Agent Communication

Preferred model:

```text
Agent
  |
  | outbound TLS
  v
Platform
```

This avoids exposing a management endpoint on every Elasticsearch VM.

Authentication should use short-lived credentials or an equivalent secure identity mechanism.

Never hard-code agent credentials.

---

# 14. Database Schema

## users

```text
id
email
password_hash / auth_provider_id
created_at
updated_at
```

## organizations

```text
id
name
created_at
updated_at
```

## organization_members

```text
id
organization_id
user_id
role
created_at
```

## cloud_accounts

```text
id
organization_id
provider
project_id
status
metadata
created_at
updated_at
```

## clusters

```text
id
organization_id
cloud_account_id
name
engine
version
region
zone
desired_state
actual_state
machine_type
node_count
storage_gb
storage_type
created_at
updated_at
```

## cluster_nodes

```text
id
cluster_id
instance_id
private_ip
zone
status
agent_status
elasticsearch_version
created_at
updated_at
```

## operations

```text
id
organization_id
cluster_id
type
status
idempotency_key
started_at
completed_at
error
metadata
```

## audit_logs

```text
id
organization_id
user_id
action
resource_type
resource_id
metadata
created_at
```

---

# 15. Desired vs Actual State

The platform must maintain:

```text
desired_state
actual_state
```

Example:

```text
Desired:
nodes = 5

Actual:
nodes = 3
```

This means an operation is in progress or failed.

The platform must reconcile these states safely.

---

# 16. Operation State Machine

```text
PENDING
   |
   v
VALIDATING
   |
   v
PROVISIONING
   |
   v
BOOTSTRAPPING
   |
   v
CONFIGURING
   |
   v
HEALTH_CHECK
   |
   +------> FAILED
   |
   v
COMPLETED
```

API requests must not block while these operations execute.

---

# 17. Idempotency

Cluster creation must support:

```text
Idempotency-Key
```

Retries must not create duplicate:

- VMs
- disks
- networks
- firewall rules
- Elasticsearch clusters

Terraform state and database state must be consistent.

---

# 18. Terraform Architecture

Terraform should manage infrastructure declaratively.

Example:

```text
terraform/
|
+-- modules/
|   +-- network
|   +-- firewall
|   +-- iam
|   +-- storage
|   +-- compute
|   +-- elasticsearch
|
+-- environments/
    +-- prototype
```

Execution flow:

```text
API
 |
 v
Operation
 |
 v
Queue
 |
 v
Provisioning Worker
 |
 v
Terraform Runner
 |
 v
Customer GCP Project
```

The API must not synchronously run Terraform.

---

# 19. Terraform Runner Security

The Terraform runner is a high-privilege component.

Requirements:

- dedicated Kubernetes ServiceAccount
- dedicated GCP identity
- least privilege
- no user-provided arbitrary Terraform
- approved module sources only
- validated variables
- controlled state storage
- operation timeout
- cancellation handling
- concurrency locking

Terraform plans should be validated before apply.

---

# 20. Concurrency Control

Only one conflicting operation should execute against a cluster at a time.

Example:

```text
Scale cluster
     |
     +-- another Scale request -> rejected/queued
```

Do not allow:

```text
Scale
+
Delete
+
Upgrade
```

to run simultaneously.

---

# 21. API

## Authentication

```text
POST /api/v1/auth/login
GET  /api/v1/auth/me
```

## Cloud

```text
POST   /api/v1/cloud-accounts
GET    /api/v1/cloud-accounts
GET    /api/v1/cloud-accounts/{id}
POST   /api/v1/cloud-accounts/{id}/validate
DELETE /api/v1/cloud-accounts/{id}
```

## Clusters

```text
POST   /api/v1/clusters
GET    /api/v1/clusters
GET    /api/v1/clusters/{id}
DELETE /api/v1/clusters/{id}

POST   /api/v1/clusters/{id}/scale
GET    /api/v1/clusters/{id}/health
GET    /api/v1/clusters/{id}/metrics
GET    /api/v1/clusters/{id}/nodes
```

## Operations

```text
GET /api/v1/operations
GET /api/v1/operations/{id}
```

## Audit

```text
GET /api/v1/audit-logs
```

---

# 22. Create Cluster Request

Example:

```json
{
  "name": "search-prod",
  "engine": "elasticsearch",
  "version": "approved-version",
  "cloud_account_id": "cloud-account-id",
  "region": "asia-south1",
  "zone": "asia-south1-a",
  "machine_type": "n2-standard-8",
  "node_count": 3,
  "storage_gb": 500,
  "storage_type": "pd-balanced",
  "high_availability": false
}
```

The actual supported versions and machine/storage combinations must be validated.

---

# 23. Scaling Workflow

Example:

```text
3 nodes
  |
  v
Desired = 5
  |
  v
Create operation
  |
  v
Terraform
  |
  v
Create 2 VMs
  |
  v
Create/attach disks
  |
  v
Install Elasticsearch
  |
  v
Configure cluster membership
  |
  v
Health check
  |
  v
Actual = 5
```

---

# 24. Monitoring

Collect:

### VM

- CPU
- memory
- disk
- disk IOPS
- disk latency
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
- version

The first prototype can use agent-reported metrics. GCP Cloud Monitoring integration can be added as the platform matures.

---

# 25. Health Model

Cluster health:

```text
UNKNOWN
HEALTHY
DEGRADED
UNHEALTHY
```

Node health:

```text
UNKNOWN
HEALTHY
UNHEALTHY
```

The platform must distinguish:

```text
Infrastructure healthy
```

from:

```text
Elasticsearch healthy
```

A running VM does not necessarily mean Elasticsearch is healthy.

---

# 26. Security Model

## Control Plane

- HTTPS
- authentication
- RBAC
- tenant isolation
- audit logs
- secure secrets
- network policies where appropriate

## GCP

- Workload Identity
- least privilege
- no static service-account keys
- separate identities per workload
- resource-level IAM where possible

## Data Plane

- private IP
- restricted firewall
- no public Elasticsearch by default
- TLS
- authentication
- authorization

## Secrets

Use a secret-management system.

Do not store:

- passwords
- tokens
- service-account keys
- Elasticsearch credentials

in Git or container images.

---

# 27. Multi-Tenancy

Every resource must be associated with an organization.

Example:

```text
request
  |
  v
authenticated user
  |
  v
organization
  |
  v
cloud account
  |
  v
cluster
```

Never trust an organization ID supplied by the client without authorization checks.

All queries must enforce organization scope.

---

# 28. GKE Network Architecture

Recommended prototype:

```text
GCP Platform Project
|
+-- VPC
    |
    +-- GKE subnet
    |
    +-- Private Google Access
```

Customer environment:

```text
Customer GCP Project
|
+-- Customer VPC
    |
    +-- Elasticsearch subnet
```

If the platform needs direct private connectivity to customer Elasticsearch, establish an explicit networking model such as VPC peering, Private Service Connect, VPN, or another supported private connectivity mechanism. Do not assume that placing resources in similarly named VPCs creates connectivity.

For the first prototype, GCP API-driven provisioning can be tested independently of direct private data-plane connectivity.

---

# 29. PostgreSQL Deployment

For the earliest prototype:

```text
PostgreSQL
```

can run inside GKE for simplicity.

For production:

```text
GKE
 |
 +-- API
 +-- Workers
 |
 v
Cloud SQL for PostgreSQL
```

is preferred unless there is a specific reason to self-host the control-plane database.

Redis can initially run inside GKE and later move to a managed service if required.

---

# 30. Helm Deployment

Create a Helm chart:

```text
deploy/helm/database-platform
```

Values should include:

```yaml
image:
  repository: ...
  tag: ...

api:
  replicas: 2

workers:
  replicas: 2

terraformRunner:
  replicas: 1

redis:
  enabled: true

postgresql:
  enabled: true

serviceAccount:
  create: true

workloadIdentity:
  gcpServiceAccount: ...
```

Do not hard-code environment-specific project IDs or credentials.

---

# 31. Configuration

Environment variables/configuration:

```text
ENVIRONMENT
DATABASE_URL
REDIS_URL
AUTH_SECRET
GCP_PROJECT_ID
GCP_REGION
TERRAFORM_STATE_BUCKET
ELASTICSEARCH_VERSION
LOG_LEVEL
MOCK_MODE
```

Secrets must come from Kubernetes Secret/Secret Manager integration, not Git.

---

# 32. Mock Mode

Support:

```text
MOCK_MODE=true
```

Mock:

- GCP APIs
- Terraform
- VM creation
- disk creation
- Elasticsearch installation
- health checks
- scaling
- deletion

Mock mode allows backend/frontend development without creating real infrastructure.

It is a testing/development mode, not the primary architecture.

---

# 33. Testing

## Unit

- provider logic
- state transitions
- validation
- RBAC
- organization isolation
- idempotency
- operation locking

## Integration

- PostgreSQL
- Redis
- Terraform
- GCP provider mocks
- Elasticsearch provider mocks

## E2E

Minimum E2E:

```text
Login
 ↓
Connect GCP
 ↓
Create cluster
 ↓
Provision
 ↓
Health check
 ↓
Scale
 ↓
Health check
 ↓
Delete
```

---

# 34. Failure Handling

The system must handle:

### Terraform failure

```text
PROVISIONING
     ↓
FAILED
```

Record:

- error
- operation
- Terraform output
- resource state

Never expose secrets in logs.

### VM creation failure

Retry only safe/idempotent operations.

### Agent unavailable

Cluster can become:

```text
DEGRADED
```

Do not immediately assume the VM is dead.

### Elasticsearch unhealthy

Differentiate:

```text
VM healthy
Elasticsearch unhealthy
```

---

# 35. Audit Logging

Audit events:

```text
CLOUD_ACCOUNT_CREATED
CLOUD_ACCOUNT_VALIDATED
CLUSTER_CREATED
CLUSTER_SCALE_STARTED
CLUSTER_SCALE_COMPLETED
CLUSTER_DELETE_STARTED
CLUSTER_DELETE_COMPLETED
OPERATION_FAILED
```

Record:

- user
- organization
- resource
- action
- timestamp
- relevant metadata

Never record secrets.

---

# 36. Observability

Platform logs must be structured JSON.

Every request/operation should have:

```text
request_id
operation_id
organization_id
cluster_id
```

Metrics:

```text
api_request_count
api_request_latency
operation_count
operation_duration
operation_failure_count
terraform_apply_duration
cluster_health
agent_heartbeat_status
```

---

# 37. Backup and Upgrade Interfaces

MVP does not need full implementation, but the architecture should leave room for:

```python
class DatabaseProvider:
    async def backup(...)
    async def restore(...)
    async def upgrade(...)
```

Do not tightly couple future operations to Elasticsearch-specific code in the core application.

---

# 38. Version Management

Maintain an explicit version catalog.

Example:

```yaml
elasticsearch:
  versions:
    X.Y.Z:
      status: supported
      distribution: ...
```

Do not automatically deploy the newest version.

Version changes must be deliberate and tested.

---

# 39. Deployment Environments

## Prototype

```text
GCP
 |
 GKE
 |
 database-platform
```

## Future

```text
Development
Staging
Production
```

Each environment should have:

- separate GCP project or appropriately isolated resources
- separate Terraform state
- separate secrets
- separate databases
- separate GKE namespaces/clusters as appropriate

---

# 40. CI/CD

Pipeline:

```text
Git Push
   |
   v
Lint
   |
   v
Unit Tests
   |
   v
Build Images
   |
   v
Security Scan
   |
   v
Build Helm Package
   |
   v
Deploy
```

Terraform should also run:

```text
terraform fmt
terraform validate
terraform plan
```

before apply.

---

# 41. Security Scanning

Use:

- container image scanning
- dependency scanning
- Terraform scanning
- secret scanning
- Kubernetes manifest scanning

Do not allow secrets into Git history.

---

# 42. Implementation Order for Claude/AI Coding Agent

Implement incrementally.

## Step 1

Create repository structure.

Verify:

```text
backend starts
frontend starts
tests run
```

## Step 2

Create FastAPI skeleton.

## Step 3

Create PostgreSQL models and Alembic migrations.

## Step 4

Implement authentication and RBAC.

## Step 5

Implement cloud-provider interfaces.

## Step 6

Implement mock GCP provider.

## Step 7

Implement mock Elasticsearch provider.

## Step 8

Implement cluster lifecycle.

## Step 9

Implement operation state machine and queue.

## Step 10

Implement real GCP provider.

## Step 11

Implement Terraform modules.

## Step 12

Create GKE Helm deployment.

## Step 13

Configure Workload Identity.

## Step 14

Implement real Elasticsearch provisioning.

## Step 15

Implement Go agent.

## Step 16

Implement health/metrics.

## Step 17

Implement scaling.

## Step 18

Implement deletion.

## Step 19

Implement frontend.

## Step 20

Implement audit logs.

## Step 21

Run complete E2E test against GCP.

After every step:

1. Run tests.
2. Fix failures.
3. Verify architecture.
4. Update documentation.
5. Keep the application runnable.

---

# 43. Critical Technical Constraints

The implementation MUST NOT:

- use service-account JSON keys inside pods
- expose Elasticsearch publicly by default
- use `latest` Elasticsearch version
- execute arbitrary shell commands through the agent
- block HTTP requests while Terraform runs
- run conflicting cluster operations concurrently
- create duplicate resources on retries
- put secrets in source code
- grant unrestricted IAM permissions unnecessarily
- tightly couple core services to GCP
- tightly couple core services to Elasticsearch
- make Kubernetes the Elasticsearch data plane
- assume VPC connectivity without explicitly configuring it

---

# 44. Prototype Definition of Done

The prototype is complete when this works:

```text
User
 |
 v
Web UI
 |
 v
GKE API
 |
 v
Operation
 |
 v
Worker
 |
 v
Terraform
 |
 v
Customer GCP Project
 |
 +-- VPC
 +-- Subnet
 +-- Firewall
 +-- VM
 +-- Persistent Disk
 |
 v
Elasticsearch
 |
 v
Agent
 |
 v
Health
 |
 v
Platform UI
```

Then:

```text
Scale
 |
 v
Terraform
 |
 v
New VM
 |
 v
Elasticsearch node joins
 |
 v
Health
```

Finally:

```text
Delete
 |
 v
Terraform
 |
 v
Resources removed
 |
 v
Audit completed
```

That end-to-end lifecycle is the primary technical goal of the MVP.
