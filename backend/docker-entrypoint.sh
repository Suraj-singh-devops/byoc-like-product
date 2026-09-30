#!/bin/sh
set -e

# Secrets mounted as files (Kubernetes / Secret Manager): FOO_FILE=/path exports FOO.
for var in $(env | sed -n 's/^\([A-Z0-9_]*\)_FILE=.*/\1/p'); do
  file=$(printenv "${var}_FILE")
  if [ -r "$file" ]; then
    export "$var=$(cat "$file")"
  fi
done

case "$1" in
  api)
    # On Kubernetes a migration Job runs first and RUN_MIGRATIONS=false.
    if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
      alembic upgrade head
    fi
    exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000 \
      --proxy-headers --forwarded-allow-ips='*' --no-access-log --no-server-header
    ;;
  cluster-manager|terraform-runner|monitoring-worker)
    exec python -m app.workers.main "$1"
    ;;
  worker)
    # All roles in one process: local tools only (docs/adr/0004).
    exec python -m app.workers.main all
    ;;
  migrate)
    alembic upgrade head
    exec python -m app.infrastructure.db_grants
    ;;
  *)
    exec "$@"
    ;;
esac
