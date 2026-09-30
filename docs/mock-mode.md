# Mock mode

`MOCK_MODE=true` (the default, and currently the only accepted value) replaces the GCP cloud
provider with a simulator, so the whole product can be developed and demonstrated without a
cloud account and without any chance of creating real resources. Real GCP mode returns in
phase P4 with per-organization identities ([ADR 0003](adr/0003-customer-access-per-organization-identities.md)).

## What is simulated, and what is real

| Real (same code as production) | Simulated |
|---|---|
| API, auth, RBAC, audit, database, Redis queue, worker, reaper | Google APIs (impersonation and permission checks, regions, machine types) |
| Workflows, step timeline, plan guard, retries, cancellation, delete pre-emption | Terraform plan/apply/destroy (resource-by-resource progress with delays) |
| Health evaluation, failure detection, metrics aggregation | VMs booting, the bootstrap script, Elasticsearch and the agents |
| Terraform root-module generation (written to the workspace for inspection) | |

The simulated data plane lives in its own table (`mock_instances`), separate from the
control plane's view of the nodes. Injected failures change the "cloud", and the
control plane has to **detect** them through its normal monitoring, as it would in
production.

Timing at `MOCK_SPEED=1.0`: create ≈ 30 s, scale-up ≈ 20 s, delete ≈ 8 s. `MOCK_SPEED=0.3`
makes demos snappier; tests use `0`. Simulated nodes run exactly the catalog version (9.5.4).

## Failure injection

On a running cluster, **Simulate failure** (or `POST /api/v1/mock/clusters/{id}/faults`):

| Fault | What the monitor detects |
|---|---|
| `vm_down` | Instance TERMINATED → node UNHEALTHY "VM unavailable", Elasticsearch reports a missing node and yellow status → cluster DEGRADED; two of three → UNHEALTHY (no quorum). Lifecycle stays ACTIVE. |
| `agent_down` | No new reports → after 90 s the node is UNKNOWN (agent STALE, VM still RUNNING) → cluster DEGRADED |
| `es_down` | Agent reports Elasticsearch not responding → node UNHEALTHY with the VM healthy → cluster DEGRADED |
| `disk_pressure` | Data disk at 93% → node UNHEALTHY "Disk usage critical" |
| `heap_pressure` | JVM heap at 94% → node UNHEALTHY |
| `cpu_spike` | CPU at 97% → a warning; health stays HEALTHY |
| `clear` | The VM restarts, rejoins, and events record the recovery |

`bootstrap_fail` is used by the tests to make provisioning fail with a realistic bootstrap
error, then succeed on retry.

## Deterministic failure triggers

- **elasticsearch.yml setting refused at startup:** a setting whose name starts with `unknown.`
  (e.g. `unknown.setting: 1` on the Configuration page) makes the simulated node refuse to start,
  like a real node with an unknown setting. The first node is rolled back and the change fails with
  `CONFIG_REJECTED` ([ADR 0018](adr/0018-elasticsearch-yml-settings.md)).


Mock mode simulates two clouds, GCP and AWS ([ADR 0014](adr/0014-aws-support.md)), on the same
simulated VMs.

| GCP project ID contains | Validation result |
|---|---|
| `denied` | Missing permissions (`compute.instances.create`, ...) with the suggested role |
| `disabled` | Compute Engine API disabled, with the `gcloud services enable` command |
| `notfound` | Project not found or not accessible |

| AWS role ARN contains | Validation result |
|---|---|
| `untrusted` | The role cannot be assumed (trust policy or external ID) |
| `denied` | The role lacks `ec2:RunInstances`, `ec2:CreateVolume`, `iam:PassRole` |

Network lookups ([ADR 0013](adr/0013-environments-and-registered-networks.md)) are synthesized
deterministically from the identifiers, so any well-formed name or ID "exists":

| GCP network or subnet name contains | Lookup result |
|---|---|
| `notfound` | Network or subnet not found (network `FAILED`) |
| `othervpc` (subnet) | Subnet belongs to another VPC |
| `proxy` (subnet) | Proxy-only subnet, VMs cannot use it |
| `nonat` | No Cloud NAT (warning) |
| `nopga` (subnet) | Private Google Access off (warning) |
| `small` (subnet) | A /28 with 12 usable addresses |

| AWS VPC or subnet ID contains | Lookup result |
|---|---|
| `dead` | Not found |
| `0bad` (subnet) | Subnet in another VPC |
| `beef` (subnet) | No route to a NAT gateway (warning) |
| `cafe` (subnet) | Assigns public IPs on launch (warning) |

A simulated AWS subnet's availability zone follows the last hex digit of its ID: in a region with
three zones, `0` → a, `1` → b, `2` → c, `3` → a again. For example
`subnet-0a1b2c3d4e5f60000`, `...60001`, `...60002` cover three zones of `ap-south-1`.

## Inspecting the Terraform that would run

Every mock operation still renders the real workspace (generated `main.tf.json` plus the
modules) under `WORKSPACES_DIR/<cluster-id>`:

```bash
docker compose exec worker sh -c 'ls /var/lib/byoc/workspaces/*/ && cat /var/lib/byoc/workspaces/*/main.tf.json'
```

## Running the real Go agent against a mock-mode control plane

The mock provider can mint VM identity tokens, so the real agent can register, heartbeat
and run approved actions locally, for example against an Elasticsearch container on the
compose network. This flow was verified against Elasticsearch 9.5.4:

```bash
API=http://localhost:8000/api/v1
curl -s -c cookies -H 'content-type: application/json' \
  -d '{"email":"owner@acme.example","password":"demo-password"}' $API/auth/login >/dev/null
CLUSTER=$(curl -s -b cookies $API/clusters | jq -r '.[] | select(.name=="logs-dev") | .id')

# Silence the simulated agent of node-1 so the real one is the only reporter.
curl -s -b cookies -H 'content-type: application/json' -d '{"node_name":"node-1","fault":"agent_down"}' \
  $API/mock/clusters/$CLUSTER/faults
curl -s -b cookies -X POST $API/mock/clusters/$CLUSTER/nodes/node-1/identity-token | jq -r .identity_token > identity

docker run -d --name es --network byoc_default -e discovery.type=single-node -e ELASTIC_PASSWORD=changeme-local \
  -e node.name=node-1 -e ES_JAVA_OPTS="-Xms512m -Xmx512m" docker.elastic.co/elasticsearch/elasticsearch:9.5.4
printf changeme-local > es-password

docker build -t byoc-agent:local agent/elasticsearch-agent
docker run --rm --network byoc_default -v "$PWD":/run/byoc:ro \
  -e BYOC_AGENT_NODE_NAME=node-1 -e BYOC_AGENT_CLUSTER_ID=$CLUSTER \
  -e BYOC_AGENT_CONTROL_PLANE_URL=http://backend:8000 -e BYOC_AGENT_ALLOW_INSECURE_CONTROL_PLANE=true \
  -e BYOC_AGENT_IDENTITY_SOURCE=static -e BYOC_AGENT_IDENTITY_TOKEN_FILE=/run/byoc/identity \
  -e BYOC_AGENT_ELASTICSEARCH_URL=https://es:9200 -e BYOC_AGENT_ELASTICSEARCH_INSECURE_SKIP_VERIFY=true \
  -e BYOC_AGENT_ELASTICSEARCH_PASSWORD_FILE=/run/byoc/es-password -e BYOC_AGENT_STATE_DIR=/tmp/agent \
  -e BYOC_AGENT_DATA_PATH=/ byoc-agent:local -config "" -once
```

The node then shows `report_source = heartbeat` with the real Elasticsearch version, heap
and shard counts. Clear the fault afterwards (`"fault":"clear"`) and remove the container.
