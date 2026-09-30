# Product Requirements Document (PRD)

# Database Infrastructure Platform

**Version:** 0.1
**Status:** Prototype / MVP
**Initial Cloud Provider:** Google Cloud Platform (GCP)
**Initial Database Engine:** elastic search
**Future Cloud Providers:** AWS, Azure
**Future Database Engines:** Redis, MySQL, PostgreSQL, MongoDB, Elasticsearch
**Primary Goal:** Provide a managed-database experience while running the actual database infrastructure inside the customer's own cloud account.

---

# 1. Executive Summary

We are building a cloud database infrastructure platform that allows engineering teams to provision and operate databases without manually managing the underlying infrastructure.

The platform will provide a user experience similar to managed database services such as Cloud SQL, Amazon RDS, or managed Redis services.

However, instead of hosting the database infrastructure ourselves, the platform will provision the required infrastructure directly inside the customer's cloud account.

For the initial prototype:

* Cloud provider: GCP
* Database: elastic search
* Compute: GCP Compute Engine
* Storage: GCP Persistent Disk
* Networking: GCP VPC
* Infrastructure provisioning: Terraform/OpenTofu
* Control plane: Our platform
* Data plane: Customer's GCP project

The customer will pay GCP directly for infrastructure costs and separately pay for our platform subscription.

The platform will handle the operational complexity:

* Provisioning
* Configuration
* Monitoring
* Scaling
* Backups
* Upgrades
* Health checks
* Failure detection
* Recovery
* Lifecycle management

The long-term vision is to become a **multi-cloud, multi-database infrastructure platform**.

---

# 2. Problem Statement

Managed database services provide significant operational convenience, but their cost can become high for startups, small companies, and organizations running large workloads.

The alternative is to run databases directly on virtual machines.

For example:

```text
GCP Managed Service
        │
        ▼
   Managed Database
        │
     Expensive
```

versus:

```text
GCP
 │
 ▼
Compute Engine VM
 │
 ▼
elastic search
```

Running the database directly on VMs can reduce infrastructure costs, but it creates significant operational responsibilities.

Engineering teams must manage:

* VM provisioning
* Operating system configuration
* Database installation
* Database configuration
* Storage
* Networking
* Security
* Monitoring
* Backups
* Disaster recovery
* Scaling
* Upgrades
* Patching
* High availability
* Failover
* Capacity planning
* Incident recovery

The product should bridge this gap.

### Product proposition

> **Managed database experience with customer-owned infrastructure.**

The customer gets a simple managed-service experience while retaining ownership and control of their cloud infrastructure and data.

---

# 3. Product Vision

The long-term vision is:

> Build a platform that allows engineering teams to provision production-grade databases in their own cloud accounts with the simplicity of a managed database service.

The user should not need to understand the underlying infrastructure to perform common database operations.

For example:

```text
Create Database
      ↓
Select Database
      ↓
Select Version
      ↓
Select Resources
      ↓
Select HA
      ↓
Create
```

The platform handles the rest.

---

# 4. Product Principles

The platform should follow these principles.

## 4.1 Customer owns the infrastructure

The actual database infrastructure should run inside the customer's cloud account.

```text
Our Platform
     │
     │ Control
     ▼
Customer Cloud
     │
     ├── VM
     ├── Storage
     ├── Network
     └── Database
```

We should not require customers to move their data into our infrastructure.

---

## 4.2 Managed experience

Customers should not need to manually:

* SSH into VMs
* Install databases
* Configure databases
* Resize disks
* Configure replication
* Perform routine upgrades
* Monitor database health

The platform should handle these operations.

---

## 4.3 Infrastructure abstraction

The user should interact with:

```text
Database
```

rather than:

```text
VM
Disk
Network
Firewall
Service Account
Operating System
Database Process
```

The infrastructure remains an implementation detail.

---

## 4.4 Cloud agnostic architecture

Although GCP is the first cloud provider, the core platform must not be tightly coupled to GCP.

Future:

```text
CloudProvider
│
├── GCPProvider
├── AWSProvider
└── AzureProvider
```

---

## 4.5 Database agnostic architecture

elastic search is the first database engine.

Future:

```text
DatabaseProvider
│
├── elastic searchProvider
├── RedisProvider
├── MySQLProvider
├── PostgreSQLProvider
├── MongoDBProvider
└── ElasticsearchProvider
```

---

# 5. Target Customers

## Primary customers

### Startups

Companies that:

* have limited DevOps resources
* want lower infrastructure costs
* need production databases
* don't want to build database automation themselves

### Mid-sized companies

Companies that:

* already run databases on VMs
* want centralized management
* want standardized database operations
* want better reliability

### Engineering organizations

Organizations that:

* operate multiple databases
* want self-service infrastructure
* need standardized provisioning
* want database operations through APIs

---

# 6. Initial Product Scope

The first prototype will focus on one database and one cloud.

## Cloud

Google Cloud Platform.

## Database

elastic search.

## Infrastructure

* Compute Engine
* Persistent Disk
* VPC
* Firewall Rules
* IAM
* Service Accounts

## Provisioning

Terraform/OpenTofu.

## Control Plane

FastAPI.

## Frontend

Next.js / React.

## Metadata Database

PostgreSQL.

## Background Jobs

Redis + worker architecture.

## VM Agent

Go.

---

# 7. MVP Features

The prototype should support:

### Authentication

* Login
* User
* Organization

### Cloud Account

* Add GCP project
* Configure GCP credentials/service account
* Validate access

### elastic search

* Create cluster
* Select elastic search version
* Select VM type
* Select disk size
* Select number of nodes
* Select GCP region/zone
* Enable/disable HA

### Operations

* Provision
* View status
* Scale
* Delete
* Health check

### Monitoring

Display:

* CPU
* Memory
* Disk
* Network
* elastic search cluster health
* Node count
* JVM heap
* Disk utilization

### Audit

Track:

* Who performed the operation
* What operation was performed
* When it happened
* Resource affected
* Operation result

---

# 8. Out of Scope for Initial Prototype

The following should NOT be implemented in Phase 1:

* AWS support
* Azure support
* Redis
* MySQL
* PostgreSQL
* MongoDB
* Elasticsearch
* Automatic scaling
* Full disaster recovery
* Multi-region deployment
* Advanced billing
* Enterprise SSO
* Complex workflow engine
* AI-based database operations
* Kubernetes-based control plane
* Advanced backup management

The architecture should support these capabilities later, but the prototype should remain focused.

---

# 9. High-Level Architecture

The platform will have two major components:

## Control Plane

Owned and operated by us.

## Data Plane

Runs inside the customer's cloud account.

Architecture:

```text
                         USER
                           │
                           ▼
                    ┌─────────────┐
                    │   Web UI    │
                    └──────┬──────┘
                           │
                           ▼
                    ┌─────────────┐
                    │ API Server  │
                    │  FastAPI    │
                    └──────┬──────┘
                           │
             ┌─────────────┴─────────────┐
             │                           │
             ▼                           ▼
      Control Plane                 PostgreSQL
             │
             ├── Cluster Manager
             ├── Provisioning Engine
             ├── Operation Manager
             ├── Monitoring Manager
             ├── Authentication
             ├── RBAC
             └── Audit Manager
                           │
                           ▼
                  Database Provider
                           │
                     elastic search
                           │
                           ▼
                    Cloud Provider
                           │
                          GCP
                           │
                           ▼
                 CUSTOMER GCP PROJECT
                           │
              ┌────────────┴────────────┐
              │                         │
          Compute Engine           Persistent Disk
              │                         │
              ▼                         │
          elastic search ◄──────────────────┘
```

---

# 10. Control Plane

The control plane is responsible for managing the entire lifecycle of databases.

Components:

```text
Control Plane
│
├── API
├── Authentication
├── Authorization
├── Organization Management
├── Cloud Account Manager
├── Cluster Manager
├── Provisioning Engine
├── Operation Manager
├── Monitoring Manager
├── Audit Manager
└── Database Providers
```

---

# 11. Data Plane

The data plane is deployed inside the customer's GCP project.

Example:

```text
Customer GCP Project
│
├── VPC
│
├── Subnet
│
├── Firewall Rules
│
├── Service Account
│
├── Compute Engine
│   ├── elastic search Node 1
│   ├── elastic search Node 2
│   └── elastic search Node 3
│
└── Persistent Disks
    ├── Disk 1
    ├── Disk 2
    └── Disk 3
```

The customer retains control of the underlying resources.

---

# 12. elastic search Architecture

The initial prototype should support a multi-node elastic search cluster.

Example:

```text
                  elastic search Cluster
                         │
          ┌──────────────┼──────────────┐
          │              │              │
          ▼              ▼              ▼
       Node-1          Node-2          Node-3
          │              │              │
          ▼              ▼              ▼
       Disk-1          Disk-2          Disk-3
```

The exact topology should be configurable.

For the prototype, the platform should support:

* Single-node development cluster
* Multi-node production-style cluster

---

# 13. GCP Architecture

The GCP provider must manage:

### Compute

Google Compute Engine.

### Storage

Persistent Disk.

### Networking

* VPC
* Subnet
* Firewall rules
* Private networking

### IAM

Service accounts with least-privilege permissions.

### Region/Zone

The user should be able to select the target region/zone.

Example:

```text
Cloud:
GCP

Project:
customer-prod

Region:
asia-south1

Zone:
asia-south1-a
```

---

# 14. Infrastructure as Code

Terraform/OpenTofu must be used for infrastructure provisioning.

Do not provision production infrastructure using ad-hoc shell scripts.

Example:

```text
Terraform
   │
   ├── VPC
   ├── Subnet
   ├── Firewall
   ├── Service Account
   ├── Compute Engine
   └── Persistent Disk
```

Terraform modules should be reusable.

Example:

```text
infrastructure/
└── terraform/
    └── gcp/
        ├── modules/
        │   ├── network/
        │   ├── compute/
        │   ├── storage/
        │   └── elastic search/
        │
        └── environments/
            └── prototype/
```

---

# 15. Desired State

The platform must use a desired-state model.

Example:

```yaml
cluster:
  name: production-search

engine:
  type: elastic search
  version: "2.x"

cloud:
  provider: gcp
  project: customer-prod
  region: asia-south1
  zone: asia-south1-a

compute:
  machineType: e2-standard-8

storage:
  sizeGB: 500
  type: pd-balanced

nodes:
  count: 3

highAvailability:
  enabled: true
```

The platform should maintain:

```text
Desired State
      vs
Actual State
```

Future versions can continuously reconcile differences.

---

# 16. Database Provider Abstraction

Create a generic interface:

```text
DatabaseProvider
```

Example capabilities:

```text
validate()
provision()
configure()
health()
metrics()
scale()
upgrade()
backup()
restore()
delete()
```

Initial implementation:

```text
elastic searchProvider
```

Future:

```text
RedisProvider
MySQLProvider
PostgreSQLProvider
MongoDBProvider
```

The core platform should not contain elastic search-specific business logic.

elastic search-specific functionality belongs inside the provider.

---

# 17. Cloud Provider Abstraction

Create:

```text
CloudProvider
```

with capabilities such as:

```text
validateCredentials()
createNetwork()
createCompute()
createStorage()
createIAM()
deleteResources()
getResourceStatus()
```

Initial implementation:

```text
GCPProvider
```

Future:

```text
AWSProvider
AzureProvider
```

---

# 18. Provisioning Workflow

When a user creates a cluster:

```text
User
 │
 ▼
Create Cluster API
 │
 ▼
Validate Request
 │
 ▼
Validate GCP Access
 │
 ▼
Create Cluster Record
 │
 ▼
Create Operation
 │
 ▼
Provisioning Worker
 │
 ▼
Generate Terraform Configuration
 │
 ▼
Terraform Plan
 │
 ▼
Terraform Apply
 │
 ▼
GCP Resources Created
 │
 ▼
VM Bootstrap
 │
 ▼
Install elastic search
 │
 ▼
Configure elastic search
 │
 ▼
Start elastic search
 │
 ▼
Health Check
 │
 ▼
Register Nodes
 │
 ▼
Cluster Healthy
 │
 ▼
Operation Completed
```

---

# 19. Asynchronous Operations

Long-running operations must not block HTTP requests.

Example:

```text
POST /api/v1/clusters
```

returns:

```json
{
  "cluster_id": "cluster-123",
  "operation_id": "operation-456",
  "status": "PROVISIONING"
}
```

The user can then query:

```text
GET /api/v1/operations/operation-456
```

Operation states:

```text
PENDING
VALIDATING
PROVISIONING
BOOTSTRAPPING
CONFIGURING
HEALTH_CHECK
COMPLETED
FAILED
CANCELLED
```

---

# 20. VM Agent

A lightweight Go agent should run on each database VM.

Responsibilities:

* Report VM health
* Report CPU
* Report memory
* Report disk
* Report elastic search health
* Report elastic search version
* Report node information
* Perform approved lifecycle operations

Architecture:

```text
             Control Plane
                   │
             Secure Channel
                   │
                   ▼
            ┌─────────────┐
            │ Go Agent    │
            └──────┬──────┘
                   │
        ┌──────────┼──────────┐
        ▼          ▼          ▼
       OS      elastic search    Disk
```

The agent must communicate outbound.

Do not expose an unrestricted administrative API.

Do not allow arbitrary shell execution through the agent.

---

# 21. Security

Security is a first-class product requirement.

## Network

Database instances should be private by default.

No public database endpoint should be created automatically.

## IAM

Use least privilege.

## Secrets

Never:

* hardcode passwords
* commit secrets
* print credentials in logs

## Encryption

Support:

* encrypted Persistent Disks
* TLS
* encrypted backups in future versions

## Authentication

The control plane should use secure authentication.

## Authorization

Implement organization-level RBAC.

Initial roles:

```text
Owner
Admin
Developer
Viewer
```

---

# 22. Audit Logging

Every significant action must be recorded.

Example:

```json
{
  "user": "user@example.com",
  "organization": "acme",
  "action": "SCALE_CLUSTER",
  "resource": "production-search",
  "resource_id": "cluster-123",
  "timestamp": "2026-09-25T12:00:00Z",
  "status": "SUCCESS"
}
```

Operations that should be audited:

* Create cluster
* Delete cluster
* Scale cluster
* Upgrade
* Backup
* Restore
* Cloud account changes
* Permission changes

---

# 23. Monitoring

The prototype should provide basic monitoring.

## Infrastructure metrics

* CPU
* Memory
* Disk utilization
* Disk I/O
* Network

## elastic search metrics

* Cluster health
* Node count
* JVM heap
* Heap utilization
* Disk utilization
* Search rate
* Indexing rate

The architecture should allow future integration with:

* Prometheus
* OpenTelemetry
* Google Cloud Monitoring

---

# 24. Cluster Health

The platform should expose a simple health state:

```text
HEALTHY
WARNING
CRITICAL
UNKNOWN
```

For example:

```text
Cluster: production-search

Status: HEALTHY

Nodes: 3/3
CPU: 42%
Memory: 61%
Disk: 48%
JVM Heap: 54%
```

---

# 25. Scaling

The prototype supports manual scaling.

Example:

```text
Current:
3 nodes

User:
Scale to 5

Platform:
3 → 5
```

Workflow:

```text
Validate
   ↓
Update desired state
   ↓
Provision additional nodes
   ↓
Bootstrap nodes
   ↓
Join elastic search cluster
   ↓
Validate cluster
   ↓
Update actual state
```

Automatic scaling is out of scope for the first prototype.

---

# 26. Failure Detection

The prototype should detect:

* VM unavailable
* elastic search unavailable
* Agent unavailable
* Disk usage critical
* Cluster unhealthy
* Node missing

Example:

```text
Node-2
   ↓
Health check failed
   ↓
Cluster status = WARNING
   ↓
Create operation/event
   ↓
Display in UI
```

Automatic replacement/recovery is a future capability.

---

# 27. Backup

The initial prototype should create the abstraction:

```text
BackupProvider
```

The first version does not need a complete enterprise backup system.

Future architecture:

```text
elastic search
    ↓
Snapshot
    ↓
GCS
    ↓
Retention Policy
```

Restore:

```text
GCS Snapshot
      ↓
New elastic search Cluster
      ↓
Restore Snapshot
      ↓
Validate
```

---

# 28. Upgrade Strategy

Future database upgrades should use controlled rolling upgrades.

Example:

```text
elastic search 2.x
      ↓
Backup
      ↓
Validate compatibility
      ↓
Upgrade Node 1
      ↓
Health check
      ↓
Upgrade Node 2
      ↓
Health check
      ↓
Upgrade Node 3
      ↓
Final validation
```

This is not required for the first prototype but the architecture should allow it.

---

# 29. REST API

## Authentication

```text
POST /api/v1/auth/login
```

## Cloud Accounts

```text
POST /api/v1/cloud-accounts
GET  /api/v1/cloud-accounts
GET  /api/v1/cloud-accounts/{id}
DELETE /api/v1/cloud-accounts/{id}
```

## Clusters

```text
POST /api/v1/clusters
GET /api/v1/clusters
GET /api/v1/clusters/{id}
DELETE /api/v1/clusters/{id}
```

## Cluster Operations

```text
POST /api/v1/clusters/{id}/scale
GET  /api/v1/clusters/{id}/health
GET  /api/v1/clusters/{id}/metrics
GET  /api/v1/clusters/{id}/nodes
```

## Operations

```text
GET /api/v1/operations
GET /api/v1/operations/{id}
```

## Audit Logs

```text
GET /api/v1/audit-logs
```

---

# 30. Create Cluster API

Example:

```http
POST /api/v1/clusters
```

Request:

```json
{
  "name": "production-search",
  "engine": "elastic search",
  "version": "2.x",
  "cloud_provider": "gcp",
  "cloud_account_id": "gcp-account-123",
  "project_id": "customer-prod",
  "region": "asia-south1",
  "zone": "asia-south1-a",
  "machine_type": "e2-standard-8",
  "node_count": 3,
  "storage_gb": 500,
  "storage_type": "pd-balanced",
  "high_availability": true
}
```

Response:

```json
{
  "cluster_id": "cluster-123",
  "operation_id": "operation-456",
  "status": "PROVISIONING"
}
```

---

# 31. Database Schema

PostgreSQL should contain at least:

```text
users
organizations
organization_members
cloud_accounts
clusters
cluster_nodes
operations
audit_logs
```

## Cluster

Fields:

```text
id
organization_id
name
engine
engine_version
cloud_provider
cloud_account_id
project_id
region
zone
machine_type
node_count
storage_gb
storage_type
high_availability
status
desired_state
actual_state
created_at
updated_at
```

## Cluster Node

```text
id
cluster_id
instance_id
hostname
private_ip
role
status
health
created_at
updated_at
```

## Operation

```text
id
cluster_id
operation_type
status
started_at
completed_at
error_message
metadata
created_at
```

---

# 32. Frontend

Use:

* Next.js
* React
* TypeScript

The UI should be clean and simple.

## Pages

```text
/login
/dashboard
/clusters
/clusters/:id
/clusters/create
/cloud-accounts
/operations
/audit-logs
```

---

# 33. Dashboard

Example:

```text
Database Platform

Clusters
3

Healthy
2

Warning
1

------------------------------------

production-search

elastic search 2.x
GCP / asia-south1

3 Nodes
500 GB

CPU       42%
Memory    61%
Disk      48%

Status: HEALTHY

[Open]
```

---

# 34. Create Cluster UI

```text
Create Database

Database Engine
[ elastic search ]

Version
[ 2.x ]

Cloud
[ GCP ]

GCP Project
[ customer-prod ]

Region
[ asia-south1 ]

Zone
[ asia-south1-a ]

Machine Type
[ e2-standard-8 ]

Nodes
[ 3 ]

Storage
[ 500 GB ]

Storage Type
[ pd-balanced ]

High Availability
[ ✓ ]

[ Create Cluster ]
```

---

# 35. Cluster Details

Display:

```text
production-search

Status: HEALTHY

Engine
elastic search 2.x

Cloud
GCP

Region
asia-south1

Nodes
3

Machine
e2-standard-8

Storage
500 GB

--------------------------------

Metrics

CPU              42%
Memory           61%
Disk             48%
JVM Heap         54%

--------------------------------

Nodes

Node-1     HEALTHY
Node-2     HEALTHY
Node-3     HEALTHY

--------------------------------

Operations

Created
Scaled
Health Check
```

---

# 36. Local Development

The entire control plane should run locally.

Use Docker Compose.

Required services:

```text
docker-compose.yml

├── backend
├── frontend
├── postgres
└── redis
```

The local system should support:

```text
MOCK_MODE=true
```

In mock mode:

* no real GCP infrastructure is created
* Terraform is not applied
* fake provisioning operations are simulated
* operation states change realistically
* the UI behaves like the real product

This allows developers to build and test without accidentally creating cloud resources.

---

# 37. Repository Structure

```text
database-platform/
│
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── domain/
│   │   ├── application/
│   │   ├── infrastructure/
│   │   ├── providers/
│   │   │   ├── cloud/
│   │   │   │   └── gcp/
│   │   │   └── database/
│   │   │       └── elastic search/
│   │   ├── models/
│   │   ├── repositories/
│   │   ├── workers/
│   │   └── config/
│   │
│   ├── tests/
│   ├── migrations/
│   └── Dockerfile
│
├── frontend/
│   ├── app/
│   ├── components/
│   ├── lib/
│   └── Dockerfile
│
├── agent/
│   └── elastic search-agent/
│       ├── cmd/
│       ├── internal/
│       └── Dockerfile
│
├── infrastructure/
│   └── terraform/
│       └── gcp/
│           ├── modules/
│           │   ├── network/
│           │   ├── compute/
│           │   ├── storage/
│           │   └── elastic search/
│           └── environments/
│               └── prototype/
│
├── docs/
│
├── tests/
│
├── docker-compose.yml
│
└── README.md
```

---

# 38. Technology Stack

## Backend

```text
Python
FastAPI
Pydantic
SQLAlchemy
Alembic
```

## Database

```text
PostgreSQL
```

## Queue

```text
Redis
```

## Worker

A background worker framework can be selected during implementation.

The implementation should keep long-running provisioning operations asynchronous.

## Frontend

```text
Next.js
React
TypeScript
```

## Infrastructure

```text
Terraform/OpenTofu
GCP
Compute Engine
Persistent Disk
VPC
IAM
```

## Agent

```text
Go
```

## Local Development

```text
Docker
Docker Compose
```

---

# 39. Authentication and RBAC

Initial roles:

### Owner

Full organization access.

### Admin

Manage clusters and cloud accounts.

### Developer

Create and manage permitted clusters.

### Viewer

Read-only access.

All API endpoints must enforce authorization.

---

# 40. Multi-Tenancy

The platform must be designed as multi-tenant from the beginning.

Every resource must belong to an organization.

Example:

```text
Organization
    │
    ├── Users
    ├── Cloud Accounts
    ├── Clusters
    ├── Operations
    └── Audit Logs
```

A user from Organization A must never be able to access Organization B's resources.

---

# 41. Idempotency

Infrastructure operations must be idempotent wherever possible.

For example, if provisioning is retried:

```text
Create Cluster
     ↓
Network already exists
     ↓
Don't create duplicate network
```

Terraform state and application-level operation tracking should prevent duplicate resources.

---

# 42. Error Handling

All operations must return meaningful errors.

Example:

```text
GCP authentication failed.

Reason:
Service account does not have compute.instances.create permission.

Suggested action:
Grant the required IAM role to the configured service account.
```

Do not expose raw internal stack traces to end users.

Detailed errors should be available in internal logs.

---

# 43. Observability

The platform itself must be observable.

Track:

* API latency
* API errors
* provisioning duration
* failed operations
* worker failures
* cloud API failures
* agent connectivity
* cluster health

Structured logging should be used.

Example:

```json
{
  "timestamp": "...",
  "level": "INFO",
  "service": "provisioner",
  "operation_id": "operation-123",
  "cluster_id": "cluster-123",
  "event": "terraform_apply_completed"
}
```

---

# 44. Testing Strategy

The project must include:

## Unit Tests

Test:

* domain logic
* validation
* provider interfaces
* state transitions

## API Tests

Test:

* authentication
* authorization
* cluster creation
* scaling
* deletion

## Provider Tests

Test:

* GCP provider
* elastic search provider

## Integration Tests

Test:

```text
API
 ↓
Database
 ↓
Worker
 ↓
Provider
```

## Mock Tests

Mock GCP and Terraform in local development.

---

# 45. Prototype Acceptance Criteria

The prototype is successful when a developer can:

1. Clone the repository.
2. Run `docker compose up`.
3. Open the web UI.
4. Log in.
5. Add a GCP project/account.
6. Validate GCP credentials.
7. Create an elastic search cluster.
8. Select GCP region.
9. Select VM type.
10. Select disk size.
11. Select node count.
12. Start provisioning.
13. See provisioning progress.
14. See cluster status.
15. See node information.
16. See basic metrics.
17. Scale the cluster.
18. See the scaling operation.
19. Delete the cluster.
20. View audit logs.

The prototype should be runnable in mock mode without any cloud account.

With valid GCP credentials, it should be possible to provision a real elastic search cluster.

---

# 46. Development Roadmap

## Phase 1 — Architecture

Deliver:

* Repository
* Architecture
* Interfaces
* Domain models
* API structure

---

## Phase 2 — Backend Foundation

Implement:

* FastAPI
* PostgreSQL
* SQLAlchemy
* Alembic
* Configuration
* Logging
* Error handling

---

## Phase 3 — Authentication

Implement:

* Users
* Organizations
* Authentication
* RBAC
* Organization isolation

---

## Phase 4 — Cluster Management

Implement:

* Cluster model
* Cluster APIs
* Desired state
* Operation model

---

## Phase 5 — GCP Provider

Implement:

```text
GCPProvider
```

Capabilities:

* authentication
* project validation
* region validation
* resource management

---

## Phase 6 — Terraform

Implement:

```text
GCP Terraform Modules
```

Provision:

* VPC
* Subnet
* Firewall
* Service Account
* Compute Engine
* Persistent Disk

---

## Phase 7 — elastic search Provider

Implement:

```text
elastic searchProvider
```

Capabilities:

* installation
* configuration
* health
* node discovery
* metrics

---

## Phase 8 — Provisioning Engine

Implement:

```text
Create Cluster
       ↓
Terraform
       ↓
Bootstrap
       ↓
elastic search
       ↓
Health Check
```

---

## Phase 9 — Go Agent

Implement:

* registration
* heartbeat
* health
* metrics
* approved operations

---

## Phase 10 — Frontend

Implement:

* Dashboard
* Create Cluster
* Cluster Details
* Operations
* Cloud Accounts
* Audit Logs

---

## Phase 11 — Scaling

Implement:

```text
3 nodes
   ↓
5 nodes
```

---

## Phase 12 — Testing

Complete:

* unit tests
* integration tests
* API tests
* provider tests
* mock mode

---

## Phase 13 — Documentation

Document:

* Architecture
* Setup
* GCP configuration
* IAM requirements
* Local development
* API
* Terraform
* Agent
* Troubleshooting

---

# 47. Phase 2 Product Roadmap

After the initial prototype is stable:

### elastic search

Add:

* HA
* Automated recovery
* Backup
* Restore
* Snapshot management
* Rolling upgrades
* Alerting
* Advanced monitoring
* Auto-scaling

---

# 48. Future Database Providers

After elastic search:

```text
Phase 2:
Redis

Phase 3:
MySQL

Phase 4:
PostgreSQL

Phase 5:
MongoDB
```

Each should implement the common:

```text
DatabaseProvider
```

interface.

---

# 49. Future Cloud Providers

After GCP:

```text
AWS
```

Then:

```text
Azure
```

Architecture:

```text
CloudProvider
│
├── GCPProvider
├── AWSProvider
└── AzureProvider
```

---

# 50. Future AI Capabilities

AI should be added only after the core platform is operational.

Potential capabilities:

## Troubleshooting

User:

```text
Why is my elastic search cluster unhealthy?
```

AI gathers:

* cluster health
* node health
* CPU
* memory
* disk
* JVM
* logs
* recent operations

Then provides an explanation.

---

## Natural Language Operations

User:

```text
Increase production-search from 3 to 5 nodes.
```

Flow:

```text
User
 ↓
AI
 ↓
Intent
 ↓
Policy Validation
 ↓
Approval
 ↓
Platform API
 ↓
Provisioning Engine
```

The AI must never directly execute arbitrary shell commands.

---

# 51. Future Product Experience

Eventually the customer should be able to say:

```text
I need an elastic search cluster for production.

3 nodes
32 GB memory
1 TB storage
HA enabled
Daily backups
14-day retention
asia-south1
```

The platform handles:

```text
Networking
IAM
VM
Storage
elastic search
Configuration
Monitoring
Backups
Health
Scaling
Upgrades
Recovery
```

The customer interacts with a **database**, not infrastructure.

---

# 52. Business Model

The customer pays two separate costs.

## Cloud Infrastructure

Paid directly by customer to GCP/AWS.

```text
Compute
Storage
Network
Snapshots
```

## Platform

Paid to our company.

Possible pricing models:

```text
Per database
Per cluster
Per node
Per infrastructure spend
Subscription tiers
```

Pricing should be finalized after prototype validation and infrastructure cost analysis.

---

# 53. Key Product Differentiation

The platform should not position itself simply as:

> "A cheaper managed database."

The broader proposition is:

> **A managed database experience running on infrastructure owned and controlled by the customer.**

Key characteristics:

```text
Customer Cloud
      +
Customer Data
      +
Managed Experience
      +
Automation
      +
Lower Operational Burden
```

---

# 54. Non-Functional Requirements

The platform should be designed for:

### Reliability

Operations should be retryable and idempotent.

### Security

Customer infrastructure and credentials must be protected.

### Scalability

The control plane should eventually support thousands of clusters.

### Extensibility

Adding a new database provider should not require rewriting the platform.

### Cloud portability

Adding AWS should not require rewriting the domain layer.

### Observability

Every operation should be traceable.

### Auditability

Every infrastructure-changing action should be recorded.

---

# 55. Important Engineering Constraints

1. Do not tightly couple business logic to GCP.
2. Do not tightly couple business logic to elastic search.
3. Do not use SSH as the primary management mechanism.
4. Do not allow arbitrary command execution.
5. Do not store secrets in source code.
6. Do not expose databases publicly by default.
7. Do not block HTTP requests during provisioning.
8. Do not build all database providers in the prototype.
9. Do not build all cloud providers in the prototype.
10. Do not over-engineer the first release.
11. Keep the prototype runnable locally.
12. Every long-running operation must have an operation ID and status.
13. Infrastructure must be provisioned through Terraform/OpenTofu.
14. The control plane must maintain desired state.
15. Customer data must remain in the customer's cloud environment.

---

# 56. Definition of Done — Prototype

The prototype is complete when:

```text
                    USER
                      │
                      ▼
                   WEB UI
                      │
                      ▼
                    API
                      │
                      ▼
               Control Plane
                      │
                      ▼
               GCP Provider
                      │
                      ▼
                  Terraform
                      │
                      ▼
             Customer GCP Project
                      │
             ┌────────┴────────┐
             │                 │
         Compute Engine    Persistent Disk
             │
             ▼
         Go Agent
             │
             ▼
         elastic search
             │
             ▼
       Cluster Healthy
```

And the user can successfully:

```text
Create
   ↓
Monitor
   ↓
Scale
   ↓
Delete
```

an elastic search cluster from the platform without manually SSHing into the VM.

---

# 57. Long-Term Vision

The final product should evolve into:

```text
                         DATABASE PLATFORM
                                │
              ┌─────────────────┴─────────────────┐
              │                                   │
        DATABASE ENGINES                     CLOUD PROVIDERS
              │                                   │
      ┌───────┼────────┐                  ┌───────┼───────┐
      │       │        │                  │       │       │
 elastic search Redis    MySQL               GCP     AWS    Azure
      │       │        │
 PostgreSQL MongoDB Elasticsearch
```

The customer sees:

```text
Create Database
      ↓
Choose Engine
      ↓
Choose Cloud
      ↓
Choose Resources
      ↓
Create
```

The platform handles the complexity underneath.

---

# 58. First Prototype Goal

**Do not try to build the entire vision now.**

The first technical milestone is deliberately simple:

> **Build a working platform that can provision an elastic search cluster on GCP Compute Engine through a web UI, monitor its health, scale it, and delete it, while keeping the architecture ready for Redis and additional cloud providers.**

Once that works reliably, the next major milestone is **Redis on the same platform architecture**, followed by AWS support.

This keeps the first version small enough to actually finish while preserving the architecture needed for the larger product.
