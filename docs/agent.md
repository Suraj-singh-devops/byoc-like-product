# Node agent

[`agent/elasticsearch-agent`](../agent/elasticsearch-agent) is a small Go program (standard
library only) that runs as a systemd service on every database VM.

## Responsibilities

- Report VM health: CPU, memory, data-disk usage and I/O, network, load, uptime (from `/proc` and `statfs`).
- Report Elasticsearch health: reachability, version, cluster status, node count, shards
  (active/unassigned/relocating and per node), elected master, JVM heap, search and
  indexing rates, document count.
- Perform approved actions. The MVP allowlist is `restart_engine` only
  ([ADR 0007](adr/0007-outbound-only-agent.md)); `drain_nodes` and `undrain_nodes` return with
  scale-down (the Elasticsearch client still implements them, but the executor refuses them).

It exposes **no network listener**, never runs shell commands, and the allowlist is compiled
in and repeated in the control plane. Arguments are decoded strictly (unknown fields rejected).
A refused action is reported back as failed.

## Channels

Every 30 seconds the agent collects a report and:

1. writes it to the GCE guest attribute `byoc/report` (the control plane reads it through the
   Compute API), and
2. if `control_plane_url` is set, sends it as a heartbeat over HTTPS and runs any approved
   actions in the response, posting each result back.

### Registration

The agent asks the metadata server for a Google-signed identity token
(`.../service-accounts/default/identity?audience=byoc-control-plane&format=full`) and calls
`POST /api/v1/agent/register` with its cluster ID and node name. The control plane verifies
the token's signature, audience and project, checks that the instance name and zone match
the node, and returns a random per-node agent token (only its SHA-256 is stored). The token is
kept in `state_dir/agent-token` (mode 0600). If it is ever rejected, the agent registers again.
Short-lived tokens replace the long-lived per-node token in phase P7.

## Configuration

`/etc/byoc/agent.json`, written by the bootstrap script; each field can be overridden with
`BYOC_AGENT_<FIELD>` environment variables:

```json
{
  "cluster_id": "…",
  "node_name": "node-1",
  "control_plane_url": "https://byoc.example.com",
  "identity_source": "gce",
  "identity_audience": "byoc-control-plane",
  "elasticsearch_url": "https://localhost:9200",
  "elasticsearch_username": "elastic",
  "elasticsearch_password_file": "/etc/byoc/elastic-password",
  "elasticsearch_ca_file": "/etc/elasticsearch/certs/byoc/ca.crt",
  "data_path": "/var/lib/elasticsearch",
  "guest_attributes": true,
  "interval_seconds": 30,
  "state_dir": "/var/lib/byoc-agent"
}
```

`control_plane_url` must be HTTPS (`allow_insecure_control_plane` exists for local
development only). `identity_source: static` reads a token from `identity_token_file`, which
is used with the mock-mode control plane (see [mock-mode.md](mock-mode.md)).

## Build, test, debug

```bash
make agent        # dist/byoc-agent-linux-{amd64,arm64}
make test-agent   # go vet + go test -race (in Docker)
byoc-agent -print-report -config /etc/byoc/agent.json   # one report as JSON
byoc-agent -once -config /etc/byoc/agent.json           # one full cycle
journalctl -u byoc-agent                                # JSON logs
```

The backend image contains both linux binaries. On real clusters the GCP provider uploads
the right one (by machine architecture) to the cluster's private artifacts bucket, and the
bootstrap script installs it after verifying its SHA-256.
