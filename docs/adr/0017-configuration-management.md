# ADR 0017: Configuration management from the platform

- Status: Accepted (2026-09-30)
- Related: [ADR 0005](0005-terraform-only-writer-state-and-secrets.md) (Terraform is the only
  writer), [ADR 0007](0007-outbound-only-agent.md) (outbound agent, allowlists),
  [ADR 0016](0016-ha-topology-dedicated-roles.md)

## Context

Users want to change Elasticsearch configuration from the platform. The platform never connects
to Elasticsearch (ADR 0006/0007), the agent runs only allowlisted actions, and some settings are
dynamic while others need a node restart.

## Decision

- **An allowlisted settings catalog**, owned by the engine provider: each setting has a type,
  a range or choices, a default and a scope. Anything else is rejected. Security-relevant
  settings (TLS, authentication, network binding, discovery, paths) are never offered.
  - **Dynamic** settings (for example disk watermarks, recovery throughput, shard limits, circuit
    breakers, destructive-action protection) apply live, with no restart.
  - **Static** settings (for example JVM heap percentage, thread-pool queues, HTTP body limit,
    indexing buffer) apply by a **rolling restart**, one node at a time.
- **Desired configuration is part of the cluster's desired state** and travels like everything
  else: Terraform writes it into VM metadata (`byoc-es-cluster-settings`, `byoc-es-node-settings`
  and a per-node `byoc-config-generation`). Terraform stays the only writer; no new channel to the
  VMs is opened.
- **The agent applies it** from the metadata server (works without any agent-to-platform
  connectivity):
  - dynamic: the agent on the elected master writes the managed keys with
    `PUT _cluster/settings` (persistent) and every agent reports the hash of the managed settings
    it reads back;
  - static: when its node's config generation changes, the agent runs the fixed
    `/opt/byoc/bin/apply-config` installed at boot, which re-renders `elasticsearch.yml` and JVM
    options from metadata and restarts the service, then reports the generation it applied.
- **An `UPDATE_CONFIG` operation** (lifecycle `UPDATING`) orchestrates it: validate → Terraform
  apply of the new metadata → wait until the dynamic settings are reported applied → for static
  changes, bump one node's generation at a time (data nodes, then coordinating, then masters, the
  elected master last), waiting after each node until it reports the new generation and the
  cluster is healthy again (green, or yellow for a single data node). It stops on the first node
  that does not come back, leaving the rest untouched; a retry resumes where it stopped.
- **Permission:** `cluster:configure`, Owner and Admin only. Everyone can read the configuration.
  Audit events `CLUSTER_CONFIG_UPDATE_STARTED` / `CLUSTER_CONFIG_UPDATED`.
- Config updates follow operation semantics (ADR 0010): one mutation at a time per cluster, and a
  delete pre-empts them.

## Consequences

- A static change on a large cluster takes one restart per node; the operation shows progress
  node by node.
- The agent gains a config-sync loop and a second fixed executable path; the allowlist of
  settings is compiled into both the platform and the agent.
