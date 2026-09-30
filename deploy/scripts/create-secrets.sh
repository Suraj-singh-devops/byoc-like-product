#!/bin/sh
# Adds the first version of each platform secret created by infrastructure/terraform/platform.
# Values are generated here and piped straight to Secret Manager: they are never written to disk,
# to Terraform state or to Git (docs/adr/0005). Existing secrets with a version are left alone.
#
#   deploy/scripts/create-secrets.sh <project-id> [name-prefix]
set -eu

project="${1:?usage: create-secrets.sh <project-id> [name-prefix]}"
prefix="${2:-byoc}"

for secret in secret-key redis-password; do
  id="${prefix}-${secret}"
  if gcloud secrets versions list "$id" --project "$project" --limit 1 --format='value(name)' | grep -q .; then
    echo "$id already has a version; leaving it"
    continue
  fi
  openssl rand -base64 48 | tr -d '\n' | gcloud secrets versions add "$id" --project "$project" --data-file=-
  echo "$id: version added"
done
