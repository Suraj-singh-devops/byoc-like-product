#!/usr/bin/env bash
# Tests for apply-config.sh with stand-ins for the metadata server, Elasticsearch and systemd.
# Runs as root in a throwaway container (it writes to /etc/elasticsearch and /var/lib/byoc):
#
#   docker run --rm -v "$PWD/infrastructure/terraform/gcp/modules/elasticsearch/scripts":/scripts:ro \
#     alpine:3.22 sh -c 'apk add -q bash jq coreutils grep sed && bash /scripts/tests/apply-config.test.sh'
set -Eeuo pipefail

SCRIPT=/scripts/apply-config.sh
STUB=$(mktemp -d)
MD="$STUB/metadata"
STATE="$STUB/state"
mkdir -p "$MD" "$STATE" "$STUB/bin" /etc/elasticsearch/jvm.options.d /var/lib/byoc /var/log/elasticsearch /etc/byoc
getent group elasticsearch >/dev/null || addgroup -S elasticsearch 2>/dev/null || groupadd -r elasticsearch
printf 'secret' >/etc/byoc/elastic-password
YML=/etc/elasticsearch/elasticsearch.yml

# --------------------------------------------------------------------------- stand-ins
cat >"$STUB/bin/curl" <<EOF
#!/usr/bin/env bash
url="\${*: -1}"
case "\$url" in
  http://metadata.google.internal/computeMetadata/v1/*)
    path="\${url#http://metadata.google.internal/computeMetadata/v1/}"
    file="$MD/\${path//\//__}"
    [ -f "\$file" ] || exit 22
    cat "\$file" ;;
  https://localhost:9200/*)
    [ -f "$STATE/es_up" ] || exit 7
    echo '{}' ;;
  *) exit 1 ;;
esac
EOF
# Elasticsearch refuses keys starting with "indices.not." at startup, like an unknown setting.
cat >"$STUB/bin/systemctl" <<EOF
#!/usr/bin/env bash
case "\$1" in
  restart)
    echo restart >>"$STATE/restarts"
    if grep -q '^indices\.not\.' "$YML"; then
      rm -f "$STATE/es_up"; touch "$STATE/failed"
      key=\$(grep -o '^indices\.not\.[a-z.]*' "$YML" | head -n 1)
      printf '{"log.level":"ERROR","message":"fatal exception while booting Elasticsearch","error.message":"unknown setting [%s] please check that any required plugins are installed"}\n' "\$key" \
        >>/var/log/elasticsearch/test-cluster_server.json
      exit 1
    fi
    touch "$STATE/es_up"; rm -f "$STATE/failed" ;;
  is-failed) [ -f "$STATE/failed" ] ;;
  reset-failed) rm -f "$STATE/failed" ;;
esac
EOF
printf '#!/usr/bin/env bash\nexit 0\n' >"$STUB/bin/journalctl"
chmod +x "$STUB/bin/"*
export PATH="$STUB/bin:$PATH"

attr() { printf '%s' "$2" >"$MD/instance__attributes__$1"; }
attr byoc-node-name data-1
attr byoc-node-roles "data,ingest"
attr byoc-cluster-name test-cluster
attr byoc-seed-hosts "h1:9300,h2:9300,h3:9300"
attr byoc-initial-masters "master-1,master-2,master-3"
attr byoc-zone-awareness true
attr byoc-es-forced-awareness "z-a,z-b,z-c"
attr byoc-es-heap-percent 40
printf 'projects/1/zones/z-a' >"$MD/instance__zone"
printf '10.0.0.9' >"$MD/instance__network-interfaces__0__ip"

failures=0
check() {
  if eval "$2"; then
    echo "  ok   $1"
  else
    echo "  FAIL $1"
    failures=$((failures + 1))
  fi
}

# ------------------------------------------------------------------------------ render
echo "render"
attr byoc-config-generation 1
attr byoc-es-node-settings '{"indices.query.bool.max_clause_count":"8192","xpack.ml.enabled":"false","reindex.remote.whitelist":"a:9200,b:9200","network.host":"0.0.0.0","indices.x":"1\nnetwork.bind_host: 0.0.0.0","indices.re":"a\\d+","Bad Key":"1"}'
bash "$SCRIPT" --record 2>"$STUB/stderr"
check "user settings are quoted strings" "grep -qx 'indices.query.bool.max_clause_count: \"8192\"' $YML"
check "list settings stay comma-separated" "grep -qx 'reindex.remote.whitelist: \"a:9200,b:9200\"' $YML"
check "reserved keys are ignored" "grep -qxF 'network.host: [\"_local_\", \"10.0.0.9\"]' $YML && [ \$(grep -c '^network\\.' $YML) -eq 2 ]"
check "a value cannot add a line" "! grep -q '^network.bind_host' $YML && grep -qxF 'indices.x: \"1\\nnetwork.bind_host: 0.0.0.0\"' $YML"
check "backslashes are kept as they are" "grep -qxF 'indices.re: \"a\\\\d+\"' $YML"
check "invalid names are ignored" "! grep -q 'Bad Key' $YML"
check "platform lines come first" "[ \$(grep -n 'xpack.security.enabled: true' $YML | cut -d: -f1) -lt \$(grep -n 'xpack.ml.enabled' $YML | cut -d: -f1) ]"
check "node roles and zone" "grep -qxF 'node.roles: [\"data\", \"ingest\"]' $YML && grep -qxF 'node.attr.zone: \"z-a\"' $YML"
check "forced awareness" "grep -qxF 'cluster.routing.allocation.awareness.force.zone.values: [\"z-a\", \"z-b\", \"z-c\"]' $YML"
check "heap share" "grep -q '^-Xmx' /etc/elasticsearch/jvm.options.d/byoc-heap.options"
check "generation recorded" "[ \$(cat /var/lib/byoc/config-generation) = 1 ]"

# ---------------------------------------------------------------- restart, accepted
echo "restart with valid settings"
touch "$STATE/es_up"
attr byoc-config-generation 2
attr byoc-es-node-settings '{"thread_pool.write.queue_size":"20000"}'
bash "$SCRIPT" --restart >/dev/null 2>&1
check "new file in place" "grep -qx 'thread_pool.write.queue_size: \"20000\"' $YML"
check "generation 2 recorded" "[ \$(cat /var/lib/byoc/config-generation) = 2 ] && [ ! -f /var/lib/byoc/config-failed ]"

# ----------------------------------------------------------------- restart, rejected
echo "restart with a setting Elasticsearch refuses"
cp "$YML" "$STUB/good.yml"
attr byoc-config-generation 3
attr byoc-es-node-settings '{"thread_pool.write.queue_size":"20000","indices.not.real":"1"}'
status=0
bash "$SCRIPT" --restart >/dev/null 2>&1 || status=$?
check "exits non-zero" "[ $status -ne 0 ]"
check "previous file restored" "cmp -s $YML $STUB/good.yml"
check "node restarted on the previous file" "[ -f $STATE/es_up ]"
check "still on generation 2" "[ \$(cat /var/lib/byoc/config-generation) = 2 ]"
check "rejection recorded with the reason" "grep -q \$'^3\\tunknown setting \\\\[indices.not.real\\\\]' /var/lib/byoc/config-failed"

# ------------------------------------------------------------- next generation fixes it
echo "a later generation clears the rejection"
attr byoc-config-generation 4
attr byoc-es-node-settings '{"thread_pool.write.queue_size":"20000"}'
bash "$SCRIPT" --restart >/dev/null 2>&1
check "generation 4 recorded, rejection cleared" "[ \$(cat /var/lib/byoc/config-generation) = 4 ] && [ ! -f /var/lib/byoc/config-failed ]"

echo
if ((failures)); then
  echo "$failures check(s) failed"
  exit 1
fi
echo "all checks passed"
