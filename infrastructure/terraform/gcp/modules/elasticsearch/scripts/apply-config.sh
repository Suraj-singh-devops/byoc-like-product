#!/usr/bin/env bash
# BYOC Elasticsearch node configuration (installed as /opt/byoc/bin/apply-config).
#
# Renders elasticsearch.yml and the JVM heap options from instance metadata (docs/adr/0016,
# docs/adr/0017, docs/adr/0018). Security, TLS, network binding, discovery, paths, node identity
# and zone awareness are fixed here; the user's settings (byoc-es-node-settings) are added after
# them, except keys the platform owns (RESERVED), which are ignored.
#
#   apply-config            render only (the startup script starts Elasticsearch itself)
#   apply-config --record   render and record byoc-config-generation as applied
#   apply-config --restart  render, restart Elasticsearch, wait until it has rejoined the
#                           cluster, then record the generation (run by the agent when this
#                           node's byoc-config-generation changes; one node at a time). If the
#                           node does not come back, the previous files are restored, the node is
#                           restarted with them, and the generation is recorded as rejected.
set -Eeuo pipefail
umask 022

readonly MD="http://metadata.google.internal/computeMetadata/v1"
readonly STATE_DIR=/var/lib/byoc
readonly FORMED="${STATE_DIR}/cluster-formed"
readonly GENERATION_FILE="${STATE_DIR}/config-generation"
readonly FAILED_FILE="${STATE_DIR}/config-failed"
readonly YML=/etc/elasticsearch/elasticsearch.yml
readonly HEAP_FILE=/etc/elasticsearch/jvm.options.d/byoc-heap.options
readonly CERTS=/etc/elasticsearch/certs/byoc

# Keys the platform owns. Must match RESERVED_PREFIXES in
# backend/app/providers/database/elasticsearch/settings.py ("a.b." reserves a namespace, "a.b" the
# key and its children).
RESERVED=(xpack.security. xpack.license. network. http.port http.host http.bind_host http.publish_host http.publish_port transport. discovery. cluster.initial_master_nodes cluster.name cluster.routing.allocation.awareness. node.name node.roles node.attr. path. bootstrap.)

MODE="${1:-render}"
case "$MODE" in
render | --record | --restart) ;;
*)
  echo "usage: apply-config [--record|--restart]" >&2
  exit 2
  ;;
esac

md() { curl -fsS --retry 5 --retry-connrefused -H "Metadata-Flavor: Google" "${MD}/$1"; }
attr() { md "instance/attributes/$1" 2>/dev/null || printf '%s' "${2:-}"; }

yaml_list() {
  local out="" item
  IFS=',' read -ra items <<<"$1"
  for item in "${items[@]}"; do
    [ -n "$item" ] && out+="\"${item}\", "
  done
  printf '[%s]' "${out%, }"
}

reserved() {
  local prefix
  for prefix in "${RESERVED[@]}"; do
    if [[ "$prefix" == *. ]]; then
      [[ "$1" == "$prefix"* ]] && return 0
    elif [[ "$1" == "$prefix" || "$1" == "$prefix".* ]]; then
      return 0
    fi
  done
  return 1
}

NODE_NAME=$(attr byoc-node-name)
NODE_ROLES=$(attr byoc-node-roles "master,data,ingest")
CLUSTER_NAME=$(attr byoc-cluster-name)
SEED_HOSTS=$(attr byoc-seed-hosts)
INITIAL_MASTERS=$(attr byoc-initial-masters)
ZONE_AWARENESS=$(attr byoc-zone-awareness false)
FORCED_AWARENESS=$(attr byoc-es-forced-awareness)
NODE_SETTINGS=$(attr byoc-es-node-settings "{}")
HEAP_PERCENT=$(attr byoc-es-heap-percent 50)
GENERATION=$(attr byoc-config-generation 0)
ZONE=$(md instance/zone)
ZONE=${ZONE##*/}
IP=$(md instance/network-interfaces/0/ip)

for required in NODE_NAME CLUSTER_NAME SEED_HOSTS; do
  if [ -z "${!required}" ]; then
    echo "[byoc] instance metadata is missing ${required}" >&2
    exit 1
  fi
done
# Coordinating-only nodes have no roles ("none" in metadata, an empty list in Elasticsearch).
if [ "$NODE_ROLES" = "none" ]; then
  NODE_ROLES=""
fi
if ! [[ "$HEAP_PERCENT" =~ ^[0-9]+$ ]] || ((HEAP_PERCENT < 25 || HEAP_PERCENT > 75)); then
  echo "[byoc] invalid byoc-es-heap-percent ${HEAP_PERCENT}; using 50" >&2
  HEAP_PERCENT=50
fi
if ! [[ "$GENERATION" =~ ^[0-9]+$ ]]; then
  echo "[byoc] invalid byoc-config-generation ${GENERATION}" >&2
  exit 1
fi

# ------------------------------------------------------------------ elasticsearch.yml
mkdir -p "$STATE_DIR"
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
{
  echo "# Managed by the BYOC platform (apply-config); local changes are overwritten."
  echo "cluster.name: \"${CLUSTER_NAME}\""
  echo "node.name: \"${NODE_NAME}\""
  echo "node.roles: $(yaml_list "$NODE_ROLES")"
  echo "node.attr.zone: \"${ZONE}\""
  echo "path.data: /var/lib/elasticsearch"
  echo "path.logs: /var/log/elasticsearch"
  echo "network.host: [\"_local_\", \"${IP}\"]"
  echo "network.publish_host: \"${IP}\""
  echo "http.port: 9200"
  echo "transport.port: 9300"
  echo "discovery.seed_hosts: $(yaml_list "$SEED_HOSTS")"
  # Only the original master nodes bootstrap the cluster, and only before it first forms.
  # Nodes added later must join the existing cluster, never start a new one.
  if [ ! -f "$FORMED" ] && [[ ",${INITIAL_MASTERS}," == *",${NODE_NAME},"* ]]; then
    echo "cluster.initial_master_nodes: $(yaml_list "$INITIAL_MASTERS")"
  fi
  if [ "$ZONE_AWARENESS" = "true" ]; then
    echo "cluster.routing.allocation.awareness.attributes: zone"
    # Forced awareness: when a zone fails, its replicas stay unassigned instead of piling up on
    # the surviving zones (which could run them out of disk).
    if [ -n "$FORCED_AWARENESS" ]; then
      echo "cluster.routing.allocation.awareness.force.zone.values: $(yaml_list "$FORCED_AWARENESS")"
    fi
  fi
  cat <<'EOF'
xpack.security.enabled: true
xpack.security.enrollment.enabled: false
xpack.security.transport.ssl.enabled: true
xpack.security.transport.ssl.verification_mode: certificate
xpack.security.transport.ssl.key: certs/byoc/node.key
xpack.security.transport.ssl.certificate: certs/byoc/node.crt
xpack.security.transport.ssl.certificate_authorities: ["certs/byoc/ca.crt"]
xpack.security.http.ssl.enabled: true
xpack.security.http.ssl.key: certs/byoc/node.key
xpack.security.http.ssl.certificate: certs/byoc/node.crt
xpack.security.http.ssl.certificate_authorities: ["certs/byoc/ca.crt"]
EOF
  echo "# Settings from the platform's Configuration page."
  # Keys are dotted lowercase names; values are written as quoted YAML strings (JSON strings are
  # valid YAML), so a value can never add another line or key.
  while IFS=$'\t' read -r key value; do
    [ -z "$key" ] && continue
    if ! [[ "$key" =~ ^[a-z][a-z0-9_]*(\.[a-z0-9_-]+)+$ ]] || reserved "$key"; then
      echo "[byoc] ignoring setting ${key}: managed by the platform or not a setting name" >&2
      continue
    fi
    echo "${key}: ${value}"
  done < <(printf '%s' "$NODE_SETTINGS" | jq -r 'to_entries[] | "\(.key)\t\(.value | tostring | tojson)"')
} >"$tmp"
if [ "$MODE" = "--restart" ]; then
  # Keep the running configuration so a node that does not start can be rolled back.
  cp -p "$YML" "${YML}.byoc-previous"
  cp -p "$HEAP_FILE" "${HEAP_FILE}.byoc-previous" 2>/dev/null || true
fi
install -m 0660 -o root -g elasticsearch "$tmp" "$YML"

# ------------------------------------------------------------------------ JVM heap
MEM_MB=$(($(awk '/^MemTotal:/ {print $2}' /proc/meminfo) / 1024))
HEAP_MB=$((MEM_MB * HEAP_PERCENT / 100))
if ((HEAP_MB > 31744)); then
  HEAP_MB=31744
fi
printf -- '-Xms%sm\n-Xmx%sm\n' "$HEAP_MB" "$HEAP_MB" >"$HEAP_FILE"

record() {
  printf '%s\n' "$GENERATION" >"$GENERATION_FILE"
  rm -f "$FAILED_FILE"
}

case "$MODE" in
render) exit 0 ;;
--record)
  record
  exit 0
  ;;
esac

# --------------------------------------------------------------------- restart
es() {
  curl -fsS --max-time 10 --cacert "$CERTS/ca.crt" -u "elastic:$(cat /etc/byoc/elastic-password)" \
    "https://localhost:9200$1"
}
# Waits until the local node answers and is part of a cluster; fails fast if the service died
# (e.g. Elasticsearch refused a setting at startup).
wait_joined() {
  local _
  for _ in $(seq 1 120); do
    if es "/_cluster/health?timeout=5s" >/dev/null 2>&1 && es "/_nodes/_local" >/dev/null 2>&1; then
      return 0
    fi
    if systemctl is-failed --quiet elasticsearch.service; then
      return 1
    fi
    sleep 5
  done
  return 1
}

# The reason Elasticsearch gave, e.g. "unknown setting [x] ..." or "Failed to parse value [y] for
# setting [z]": the last error.message of its JSON log, else the matching line of the journal.
startup_error() {
  local line="" file
  for file in "/var/log/elasticsearch/${CLUSTER_NAME}_server.json" "/var/log/elasticsearch/${CLUSTER_NAME}.json"; do
    [ -f "$file" ] || continue
    line=$(tail -n 400 "$file" | grep -oE '"error\.message":"[^"]*"' | tail -n 1 | sed -E 's/^"error\.message":"(.*)"$/\1/' || true)
    [ -n "$line" ] && break
  done
  if [ -z "$line" ]; then
    line=$(journalctl -u elasticsearch.service --since "-15 min" --no-pager -o cat 2>/dev/null |
      grep -oE 'unknown setting \[[^]]*\][^"]*|Failed to parse value \[[^]]*\] for setting \[[^]]*\]|IllegalArgumentException: [^"]*' |
      head -n 1 || true)
  fi
  printf '%s' "${line:-Elasticsearch did not rejoin the cluster within 10 minutes}" | tr -d '\t\r\n' | cut -c1-400
}

echo "[byoc] restarting Elasticsearch on ${NODE_NAME} for configuration generation ${GENERATION}"
# Flush first so shard recovery after the restart is fast; failure is not fatal.
es "/_flush" -X POST >/dev/null 2>&1 || true
systemctl restart elasticsearch.service || true
if wait_joined; then
  record
  echo "[byoc] ${NODE_NAME} rejoined the cluster with generation ${GENERATION}"
  exit 0
fi

reason=$(startup_error)
echo "[byoc] ${NODE_NAME} did not start with generation ${GENERATION}: ${reason}; restoring the previous configuration" >&2
install -m 0660 -o root -g elasticsearch "${YML}.byoc-previous" "$YML"
if [ -f "${HEAP_FILE}.byoc-previous" ]; then
  cp -p "${HEAP_FILE}.byoc-previous" "$HEAP_FILE"
fi
# The agent does not retry a rejected generation; it reports it with the reason.
printf '%s\t%s\n' "$GENERATION" "$reason" >"$FAILED_FILE"
systemctl reset-failed elasticsearch.service 2>/dev/null || true
systemctl restart elasticsearch.service || true
if wait_joined; then
  echo "[byoc] ${NODE_NAME} is back on its previous configuration" >&2
else
  echo "[byoc] ${NODE_NAME} did not come back on its previous configuration either" >&2
fi
exit 1
