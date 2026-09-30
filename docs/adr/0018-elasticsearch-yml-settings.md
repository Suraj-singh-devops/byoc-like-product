# 0018. Any elasticsearch.yml setting, except the ones the platform owns

- Status: Accepted (2026-09-30). Supersedes the "static settings" allowlist of
  [0017](0017-configuration-management.md); live settings stay as in 0017.
- Deciders: platform team, at the user's request ("provide option to update flag/config in
  elasticsearch.yml").

## Context

ADR 0017 let users change 17 allowlisted settings: 12 live cluster settings and 5 typed
elasticsearch.yml settings. Users configure Elasticsearch mostly through elasticsearch.yml and
need settings the list does not have (e.g. `indices.query.bool.max_clause_count`,
`xpack.ml.enabled`, `reindex.remote.whitelist`, thread pools, caches). An allowlist would have to
track every Elasticsearch release and plugin.

## Decision

- **Any elasticsearch.yml setting may be set**, with a dotted lowercase name
  (`^[a-z][a-z0-9_]*(\.[a-z0-9_-]+)+$`), except the settings **the platform owns**:
  `xpack.security.*`, `xpack.license.*`, `network.*`, `http.port|host|bind_host|publish_host|publish_port`,
  `transport.*`, `discovery.*`, `cluster.initial_master_nodes`, `cluster.name`,
  `cluster.routing.allocation.awareness.*`, `node.name`, `node.roles`, `node.attr.*`, `path.*`,
  `bootstrap.*`. These keep TLS, authentication, network exposure, cluster formation, topology and
  zone awareness under the platform's control. The same list is enforced by the API, the Terraform
  module's variable validation and `apply-config` on the VM (a test keeps the three identical).
- Values are single-line text (≤ 512 characters; lists are comma-separated) and are always
  written as quoted YAML strings (JSON-encoded), after the platform's own lines. A value can never
  add a line or a key. Elasticsearch parses quoted strings for numeric, boolean and list settings
  (verified with 9.5.4). At most 64 custom settings per cluster.
- The typed settings of 0017 (e.g. `thread_pool.write.queue_size`, heap share) keep their
  validation; the 12 live settings keep being applied through the cluster settings API.
- **Changes roll out one node at a time** (0017), and **a node that does not start is rolled
  back**: `apply-config --restart` keeps the running files, and if Elasticsearch exits or does not
  rejoin within 10 minutes it restores them, restarts the node on its previous configuration and
  records the rejected generation with Elasticsearch's reason (e.g. `unknown setting [x]`,
  `Failed to parse value [y] for setting [z]`). The agent never retries a rejected generation and
  reports it; the operation fails at once with `CONFIG_REJECTED`, the remaining nodes are not
  touched, and the cluster returns to `ACTIVE`. The user corrects or removes the setting and applies
  again. The rolling restart skips only nodes that report the new generation, so a retry never
  carries a rejected setting past the node that refused it.

## Consequences

- Users can express everything elasticsearch.yml offers except what would break the platform's
  guarantees. Typos and unknown settings cost one node restart (twice: new file, then the previous
  one), not an outage.
- A setting that Elasticsearch accepts but that hurts the cluster (e.g. a tiny queue) is the
  user's responsibility; health checks after each node catch a node that does not rejoin.
- New nodes (create, scale) boot with the current settings; a setting that stops Elasticsearch
  fails their bootstrap with the reason shown.
- Per-group settings (different values for data and coordinating nodes) are not supported yet.
