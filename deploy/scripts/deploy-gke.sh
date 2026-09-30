#!/bin/sh
# Builds and pushes the images, then installs or upgrades the platform on the GKE cluster created
# by infrastructure/terraform/platform (implementation plan P3).
#
#   deploy/scripts/deploy-gke.sh [image-tag] [extra helm args...]
#
# Uses its own kubeconfig file, so your current kubectl context is never changed.
set -eu

cd "$(dirname "$0")/../.."
tag="${1:-$(date +%Y%m%d-%H%M%S)}"
[ $# -gt 0 ] && shift
tf="terraform -chdir=infrastructure/terraform/platform"

repo=$($tf output -raw image_repository)
cluster=$($tf output -json cluster)
name=$(printf '%s' "$cluster" | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')
location=$(printf '%s' "$cluster" | python3 -c 'import json,sys; print(json.load(sys.stdin)["location"])')
project=$($tf output -raw helm_values | python3 -c 'import sys,re; print(re.search(r"projectId: (\S+)", sys.stdin.read()).group(1))')

echo "Building and pushing $repo/{backend,frontend}:$tag (linux/amd64)"
docker buildx build --platform linux/amd64 -f backend/Dockerfile -t "$repo/backend:$tag" --push .
docker buildx build --platform linux/amd64 -t "$repo/frontend:$tag" --push frontend

workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT
$tf output -raw helm_values > "$workdir/values.yaml"
export KUBECONFIG="$workdir/kubeconfig"
gcloud container clusters get-credentials "$name" --location "$location" --project "$project"

kubectl create namespace database-platform --dry-run=client -o yaml | kubectl apply -f -
kubectl label namespace database-platform --overwrite \
  pod-security.kubernetes.io/enforce=restricted pod-security.kubernetes.io/warn=restricted

helm upgrade --install platform deploy/helm/database-platform --namespace database-platform \
  -f "$workdir/values.yaml" \
  --set image.backend.repository="$repo/backend" --set image.backend.tag="$tag" \
  --set image.frontend.repository="$repo/frontend" --set image.frontend.tag="$tag" \
  --wait --timeout 15m "$@"
echo "Deployed $tag. Console address: $($tf output -raw console_address)"
