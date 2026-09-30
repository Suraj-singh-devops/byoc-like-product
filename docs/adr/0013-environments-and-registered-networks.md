# ADR 0013: Environments and registered networks

- Status: Accepted. Supersedes [ADR 0006](0006-dedicated-vpc-per-cluster.md) for new clusters.
  Implemented in P1b (mock mode).
- Date: 2026-09-27
- Related: [ADR 0003](0003-customer-access-per-organization-identities.md) (customer access),
  [ADR 0011](0011-elasticsearch-topology-and-ha.md) (HA), [ADR 0014](0014-aws-support.md) (AWS)

## Context

Customers run several environments (for example test and production), sometimes on different
clouds, and want their database clusters inside networks they already operate. The platform must
therefore place a cluster in an existing VPC and subnet chosen by the customer instead of
creating a VPC per cluster (ADR 0006).

The requested flow is: choose or create an environment, choose the cloud provider, then choose a
network, a platform record that points at an existing VPC and subnet. The platform fetches the
network's details from the cloud; it does not create the network.

## Decision

### Resource model

```text
Organization
 ├─ Cloud accounts     GCP project + service account to impersonate, or AWS account + role (ADR 0014)
 └─ Environments       name, type TEST | PRODUCTION
      └─ Networks      cloud account + region + existing VPC + subnet(s)   (platform records)
           └─ Clusters placed in the network's subnets
```

- **Environment**: an organization-scoped group with a unique name and a type, `TEST` or
  `PRODUCTION`. An environment is not tied to one cloud: the provider is chosen per network, so a
  production environment can hold GCP and AWS networks. A customer who wants one provider per
  environment registers networks of one provider only. In P1b the type is a label that sets
  console defaults (production suggests high availability); it enforces nothing yet.
- **Network**: a platform record that points at an existing VPC and subnet(s) in one of the
  organization's cloud accounts. The provider comes from the cloud account. The customer enters
  identifiers only:
  - GCP: region, VPC network name and one subnet name. A GCP subnet is regional, so one subnet
    serves every zone of the region. The network must be in the cloud account's project; Shared
    VPC host projects are not supported yet.
  - AWS: region, VPC ID and one to six subnet IDs of that VPC, at most one per availability zone.
    AWS subnets are zonal, so the subnets decide where nodes can run.
- A network is immutable. Using a different VPC or subnet means registering another network.
- The platform never creates, changes or deletes the customer's VPCs, subnets, routes, routers
  or NAT.

### Fetching network details

Everything about the network except the identifiers comes from a read-only lookup in the
customer's cloud, never from the client:

| Check | GCP | AWS | On failure |
|---|---|---|---|
| VPC exists | `networks.get` | `DescribeVpcs` | failed |
| Subnets exist, belong to the VPC and the region | `subnetworks.get` | `DescribeSubnets` | failed |
| Subnet usable for VMs | purpose `PRIVATE` (not proxy-only or Private Service Connect) | n/a | failed |
| Address ranges and free addresses | primary range | CIDR, available IP count | recorded |
| Zones nodes can use | every zone of the region | each subnet's availability zone | recorded |
| Outbound access for package downloads | a Cloud NAT that covers the subnet | a `0.0.0.0/0` route to a NAT gateway | warning (a proxy or mirror may exist) |
| Access to cloud APIs without public IPs | Private Google Access on the subnet | n/a (NAT or VPC endpoints) | warning |
| Public addresses | n/a | subnet assigns public IPv4 on launch | warning (the platform never assigns one) |

A network is `PENDING`, then `VALIDATING`, then `AVAILABLE` when no check failed, or `FAILED`.
A network that was available and fails a later check becomes `UNAVAILABLE`. Warnings are shown
but do not block.

The lookup is read-only. Until P2 it runs in the API process in mock mode only; from P2 it runs in
the monitoring worker under the organization's read identity (the GCP monitor service account,
the AWS read role), never under the write identity. The customer's read-only role therefore
gains `compute.networks.get`, `compute.subnetworks.get` and `compute.routers.list` on GCP, and
`ec2:DescribeVpcs`, `ec2:DescribeSubnets`, `ec2:DescribeRouteTables` and
`ec2:DescribeNatGateways` on AWS.

### Clusters in a network

- Creating a cluster needs an environment and an `AVAILABLE` network in that environment whose
  cloud account is `CONNECTED`. The cloud account, region and provider come from the network.
- Zones:
  - Without HA, every node runs in one zone: the chosen zone, or the network's first zone.
  - With HA, nodes are spread over three distinct zones (the chosen zone first). ADR 0011 defines
    HA as surviving the loss of one zone, which three master-eligible nodes in only two zones do
    not. On AWS the network therefore needs subnets in at least three availability zones.
  - Each node runs in its zone's subnet (on GCP, the one regional subnet).
- The zones and the network (VPC, subnets, ranges) are copied into the cluster's desired state
  at creation, so scaling adds nodes to the same zones and subnets, and a cluster never depends on
  later reads of the network record.
- The subnets must have at least as many free addresses as the cluster has nodes.
- Scaling needs the network to be `AVAILABLE`; deletion never does. Create and scale re-check the
  network in the cloud before planning and fail with `NETWORK_CHANGED` if a subnet disappeared or
  its range changed.
- A network cannot be removed while a cluster that is not deleted uses it. An environment cannot be
  removed while it has networks or clusters that are not deleted. A cloud account cannot be
  removed while networks use it.

### What Terraform creates in an existing network

- No VPC, subnet, router or NAT (GCP module input `network.create = false`).
- Firewall rules (GCP) or a security group (AWS) that apply only to the cluster's VMs, through a
  per-cluster network tag or security group. They allow 9200 and 9300 from the network's subnet
  ranges and nothing else inbound. `0.0.0.0/0` is never allowed.
- VMs without public IPs in the network's subnets, and their data disks.
- The VPC and subnet are passed to Terraform as resource paths, and the subnet range comes from the
  platform's validated lookup, not from a Terraform data source. Deleting a cluster therefore never
  depends on reading the customer's network.
- The GCP provisioner role no longer needs to create or delete networks, subnets, routers or NAT.
  Those permissions are removed from the required set; `compute.networks.updatePolicy` (firewall
  rules on the VPC) and `compute.subnetworks.use` (attaching VMs) remain.

### Clusters created before environments

Clusters that existed before P1b are moved into an environment named `default` (type
`PRODUCTION`, the cautious assumption) by the migration. They have no network record and keep
their dedicated VPC from ADR 0006 through scaling and deletion. New clusters cannot use a
dedicated VPC.

## Alternatives considered

- **One cloud provider per environment**: simpler lists, but a production environment that spans
  GCP and AWS would have to be split into two. Rejected; the console still asks for the provider
  before the network.
- **Keep creating a dedicated VPC per cluster (ADR 0006)**: rejected by the product requirement;
  customers place databases in their own networks.
- **Listing the VPCs and subnets of a cloud account for the customer to pick**: better usability,
  but more read permissions and more surface. Deferred; P1b looks up the identifiers the customer
  enters.
- **Terraform data sources for the existing network**: they catch drift at plan time but make
  deletion fail when the network cannot be read. Replaced by the platform's own lookup before
  create and scale.

## Consequences

- Customers must provide a VPC and subnet with outbound access to the Elastic package repository
  (Cloud NAT, a NAT gateway or a proxy) and access to the cloud's secret and storage APIs (Private
  Google Access on GCP; NAT or VPC endpoints on AWS).
- The platform adds firewall rules to the customer's VPC. They are scoped to the cluster's VMs, but
  hierarchical firewall policies or network ACLs that block 9200 and 9300 inside the subnet
  must allow them.
- Address planning and overlapping ranges are the customer's own, since the network is theirs.
- Clusters in the same subnet can reach each other's ports 9200 and 9300 at the network level;
  TLS and authentication still protect every cluster.
- Follow-ups:
  - client CIDRs per network, so applications in other subnets can reach port 9200 (private ranges only);
  - Shared VPC on GCP;
  - VPC and subnet discovery;
  - environment policies (for example HA required in production) and per-environment roles.
