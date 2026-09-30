#!/usr/bin/env bash
# BYOC Elasticsearch node bootstrap (GCE startup script).
#
# Runs on every boot and is idempotent. All configuration comes from instance metadata.
# Progress is published to the guest attribute byoc/bootstrap, which the control plane reads
# through the Compute API, so it works even when the VM cannot reach the control plane.
set -Eeuo pipefail
umask 022
exec > >(tee -a /var/log/byoc-bootstrap.log) 2>&1

readonly MD="http://metadata.google.internal/computeMetadata/v1"
STEP="reading configuration"

md() { curl -fsS --retry 5 --retry-connrefused -H "Metadata-Flavor: Google" "${MD}/$1"; }
attr() { md "instance/attributes/$1" 2>/dev/null || printf '%s' "${2:-}"; }

json_escape() {
  local s="${1//\\/\\\\}"
  s="${s//\"/\\\"}"
  printf '%s' "$s" | tr -d '\000-\037' | cut -c1-500
}

report() {
  local body
  body=$(printf '{"status":"%s","message":"%s","updated_at":"%s"}' \
    "$1" "$(json_escape "${2:-}")" "$(date -u +%Y-%m-%dT%H:%M:%SZ)")
  curl -fsS -X PUT --data "$body" -H "Metadata-Flavor: Google" \
    "${MD}/instance/guest-attributes/byoc/bootstrap" >/dev/null || true
  echo "[byoc] $1: ${2:-}"
}

on_error() {
  report failed "Failed while ${STEP} (line $1). See /var/log/byoc-bootstrap.log on the VM."
}
trap 'on_error $LINENO' ERR

step() {
  STEP="$2"
  report "$1" "$2"
}

retry() {
  local attempts="$1" n=1
  shift
  until "$@"; do
    if ((n >= attempts)); then
      return 1
    fi
    sleep $((n * 5))
    n=$((n + 1))
  done
}

# ------------------------------------------------------------------ configuration
NODE_NAME=$(attr byoc-node-name)
NODE_ROLES=$(attr byoc-node-roles "master,data,ingest")
CLUSTER_ID=$(attr byoc-cluster-id)
CLUSTER_NAME=$(attr byoc-cluster-name)
ES_VERSION=$(attr byoc-es-version)
ES_APT_REPOSITORY=$(attr byoc-es-apt-repository)
ES_SIGNING_KEY_URL=$(attr byoc-es-signing-key-url)
ES_SIGNING_KEY_FINGERPRINT=$(attr byoc-es-signing-key-fingerprint)
ES_PACKAGE_SHA256=$(attr byoc-es-package-sha256)
SEED_HOSTS=$(attr byoc-seed-hosts)
INITIAL_MASTERS=$(attr byoc-initial-masters)
ZONE_AWARENESS=$(attr byoc-zone-awareness false)
SECRET_PREFIX=$(attr byoc-secret-prefix)
BUCKET=$(attr byoc-artifacts-bucket)
AGENT_OBJECT=$(attr byoc-agent-object)
AGENT_SHA256=$(attr byoc-agent-sha256)
AGENT_VERSION=$(attr byoc-agent-version)
CONTROL_PLANE_URL=$(attr byoc-control-plane-url)
AGENT_AUDIENCE=$(attr byoc-agent-audience byoc-control-plane)
ZONE=$(md instance/zone)
ZONE=${ZONE##*/}
IP=$(md instance/network-interfaces/0/ip)

for required in NODE_NAME CLUSTER_ID CLUSTER_NAME ES_VERSION ES_APT_REPOSITORY ES_SIGNING_KEY_URL \
  ES_SIGNING_KEY_FINGERPRINT ES_PACKAGE_SHA256 SEED_HOSTS SECRET_PREFIX; do
  if [ -z "${!required}" ]; then
    report failed "Instance metadata is missing ${required}"
    exit 1
  fi
done

# ------------------------------------------------------------------------ data disk
step installing "preparing the data disk"
DISK=/dev/disk/by-id/google-data
for _ in $(seq 1 60); do
  [ -e "$DISK" ] && break
  sleep 2
done
if [ ! -e "$DISK" ]; then
  report failed "The data disk ($DISK) is not attached"
  exit 1
fi
if ! blkid "$DISK" >/dev/null 2>&1; then
  mkfs.ext4 -q -m 0 -E lazy_itable_init=0,lazy_journal_init=0,discard -L esdata "$DISK"
fi
DISK_UUID=$(blkid -s UUID -o value "$DISK")
mkdir -p /var/lib/elasticsearch
if ! grep -q "UUID=${DISK_UUID}" /etc/fstab; then
  echo "UUID=${DISK_UUID} /var/lib/elasticsearch ext4 defaults,nofail,discard,noatime 0 2" >>/etc/fstab
fi
mountpoint -q /var/lib/elasticsearch || mount /var/lib/elasticsearch

step installing "tuning kernel settings"
cat >/etc/sysctl.d/99-byoc-elasticsearch.conf <<'EOF'
vm.max_map_count = 262144
vm.swappiness = 1
EOF
sysctl -q --system
swapoff -a || true

# ------------------------------------------------------------------------- install
# Exactly the catalog version (no "latest", no wildcard). The repository's signing key is
# trusted only if its fingerprint matches the catalog; APT then verifies the signed index,
# and the package is compared with the catalog's SHA-256 before it is installed.
export DEBIAN_FRONTEND=noninteractive
installed=$(dpkg-query -W -f='${Status} ${Version}' elasticsearch 2>/dev/null || true)
if [ "$installed" != "install ok installed ${ES_VERSION}" ]; then
  step installing "installing Elasticsearch ${ES_VERSION} from ${ES_APT_REPOSITORY}"
  retry 10 apt-get update -q
  retry 5 apt-get install -y -q apt-transport-https ca-certificates curl gnupg jq
  retry 5 curl -fsSL "$ES_SIGNING_KEY_URL" -o /tmp/elastic.asc
  fingerprint=$(gpg --batch --show-keys --with-colons /tmp/elastic.asc | awk -F: '$1 == "fpr" {print $10; exit}')
  if [ "$fingerprint" != "$ES_SIGNING_KEY_FINGERPRINT" ]; then
    report failed "The package signing key fingerprint ${fingerprint:-?} does not match the version catalog"
    exit 1
  fi
  gpg --batch --yes --dearmor -o /usr/share/keyrings/elastic.gpg /tmp/elastic.asc
  echo "deb [signed-by=/usr/share/keyrings/elastic.gpg] ${ES_APT_REPOSITORY} stable main" \
    >/etc/apt/sources.list.d/elastic.list
  retry 10 apt-get update -q
  retry 5 apt-get install -y -q --download-only "elasticsearch=${ES_VERSION}"
  package=$(find /var/cache/apt/archives -maxdepth 1 -name "elasticsearch_${ES_VERSION}_*.deb" | head -n 1)
  if [ -z "$package" ] || ! echo "${ES_PACKAGE_SHA256}  ${package}" | sha256sum -c --status; then
    report failed "The Elasticsearch ${ES_VERSION} package does not match the SHA-256 in the version catalog"
    exit 1
  fi
  retry 5 apt-get install -y -q "elasticsearch=${ES_VERSION}"
  apt-mark hold elasticsearch >/dev/null
fi
command -v jq >/dev/null || retry 5 apt-get install -y -q jq

# ------------------------------------------------------------------------- secrets
step configuring "fetching TLS certificates and credentials from Secret Manager"
token() { md "instance/service-accounts/default/token" | jq -r .access_token; }
secret() {
  local response
  response=$(curl -fsS -H "Authorization: Bearer $(token)" \
    "https://secretmanager.googleapis.com/v1/${SECRET_PREFIX}$1/versions/latest:access") || return 1
  printf '%s' "$response" | jq -r .payload.data | base64 -d
}
CERTS=/etc/elasticsearch/certs/byoc
install -d -m 0750 -o root -g elasticsearch "$CERTS"
fetch_secret() {
  local tmp
  tmp=$(mktemp)
  # IAM grants on new secrets can take a minute to propagate.
  retry 12 secret "$1" >"$tmp"
  install -m 0640 -o root -g elasticsearch "$tmp" "$2"
  rm -f "$tmp"
}
fetch_secret ca-cert "$CERTS/ca.crt"
fetch_secret node-cert "$CERTS/node.crt"
fetch_secret node-key "$CERTS/node.key"
install -d -m 0700 /etc/byoc
ELASTIC_PASSWORD=$(retry 12 secret elastic-password)
(
  umask 077
  printf '%s' "$ELASTIC_PASSWORD" >/etc/byoc/elastic-password
)

# ---------------------------------------------------------------------- configure
step configuring "writing elasticsearch.yml, the keystore and JVM options"
yaml_list() {
  local out="" item
  IFS=',' read -ra items <<<"$1"
  for item in "${items[@]}"; do
    [ -n "$item" ] && out+="\"${item}\", "
  done
  printf '[%s]' "${out%, }"
}
FORMED=/var/lib/byoc/cluster-formed
mkdir -p /var/lib/byoc
{
  echo "# Managed by the BYOC platform; rewritten at every boot."
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
} >/etc/elasticsearch/elasticsearch.yml
chown root:elasticsearch /etc/elasticsearch/elasticsearch.yml
chmod 0660 /etc/elasticsearch/elasticsearch.yml

MEM_MB=$(($(awk '/^MemTotal:/ {print $2}' /proc/meminfo) / 1024))
HEAP_MB=$((MEM_MB / 2))
if ((HEAP_MB > 31744)); then
  HEAP_MB=31744
fi
printf -- '-Xms%sm\n-Xmx%sm\n' "$HEAP_MB" "$HEAP_MB" >/etc/elasticsearch/jvm.options.d/byoc-heap.options

# Replace the package's security auto-configuration with the cluster's own certificates.
KEYSTORE=/etc/elasticsearch/elasticsearch.keystore
rm -f "$KEYSTORE" /etc/elasticsearch/certs/http_ca.crt /etc/elasticsearch/certs/http.p12 \
  /etc/elasticsearch/certs/transport.p12
/usr/share/elasticsearch/bin/elasticsearch-keystore create >/dev/null
printf '%s' "$ELASTIC_PASSWORD" |
  /usr/share/elasticsearch/bin/elasticsearch-keystore add --stdin --force bootstrap.password >/dev/null
chown root:elasticsearch "$KEYSTORE"
chmod 0660 "$KEYSTORE"
chown elasticsearch:elasticsearch /var/lib/elasticsearch

# -------------------------------------------------------------------------- start
step starting "starting Elasticsearch"
systemctl daemon-reload
systemctl enable elasticsearch.service >/dev/null 2>&1
if ! systemctl restart elasticsearch.service; then
  reason=$(journalctl -u elasticsearch.service -n 50 --no-pager -o cat | grep -m1 -iE 'exception|error' || true)
  report failed "Elasticsearch did not start: ${reason:-see journalctl -u elasticsearch}"
  exit 1
fi

es() { curl -fsS --cacert "$CERTS/ca.crt" -u "elastic:${ELASTIC_PASSWORD}" "https://localhost:9200$1"; }
step starting "waiting for ${NODE_NAME} to form or join the cluster"
joined=false
for _ in $(seq 1 120); do
  if es "/_cluster/health?timeout=5s" >/dev/null 2>&1; then
    joined=true
    break
  fi
  sleep 5
done
if [ "$joined" != true ]; then
  report failed "Elasticsearch did not form or join the cluster within 10 minutes"
  exit 1
fi
touch "$FORMED"
sed -i '/^cluster.initial_master_nodes:/d' /etc/elasticsearch/elasticsearch.yml

# -------------------------------------------------------------------------- agent
if [ -n "$AGENT_OBJECT" ]; then
  step starting "installing the BYOC agent ${AGENT_VERSION}"
  tmp=$(mktemp)
  object=$(jq -rn --arg o "$AGENT_OBJECT" '$o|@uri')
  retry 5 curl -fsS -H "Authorization: Bearer $(token)" -o "$tmp" \
    "https://storage.googleapis.com/storage/v1/b/${BUCKET}/o/${object}?alt=media"
  echo "${AGENT_SHA256}  ${tmp}" | sha256sum -c --status
  install -m 0755 "$tmp" /usr/local/bin/byoc-agent
  rm -f "$tmp"
  install -d -m 0700 /var/lib/byoc-agent
  jq -n \
    --arg cluster_id "$CLUSTER_ID" \
    --arg node_name "$NODE_NAME" \
    --arg control_plane_url "$CONTROL_PLANE_URL" \
    --arg audience "$AGENT_AUDIENCE" \
    '{
      cluster_id: $cluster_id,
      node_name: $node_name,
      control_plane_url: $control_plane_url,
      identity_source: "gce",
      identity_audience: $audience,
      elasticsearch_url: "https://localhost:9200",
      elasticsearch_username: "elastic",
      elasticsearch_password_file: "/etc/byoc/elastic-password",
      elasticsearch_ca_file: "/etc/elasticsearch/certs/byoc/ca.crt",
      data_path: "/var/lib/elasticsearch",
      guest_attributes: true,
      interval_seconds: 30,
      state_dir: "/var/lib/byoc-agent"
    }' >/etc/byoc/agent.json
  chmod 0600 /etc/byoc/agent.json
  cat >/etc/systemd/system/byoc-agent.service <<'EOF'
[Unit]
Description=BYOC node agent
After=network-online.target elasticsearch.service
Wants=network-online.target

[Service]
ExecStart=/usr/local/bin/byoc-agent -config /etc/byoc/agent.json
Restart=always
RestartSec=5
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable byoc-agent.service >/dev/null 2>&1
  systemctl restart byoc-agent.service
fi

VERSION=$(es / | jq -r .version.number)
report ready "Elasticsearch ${VERSION} running as ${NODE_NAME}"
