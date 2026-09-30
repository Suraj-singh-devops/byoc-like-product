# One Google service account per workload (docs/adr/0004). The Kubernetes ServiceAccount of each
# Deployment may act as its GSA and nothing else. No keys are ever created for any of them.

locals {
  # short name (GSA) => Kubernetes ServiceAccount of the Helm chart
  workloads = {
    api      = "api"
    manager  = "cluster-manager"
    runner   = "terraform-runner"
    monitor  = "monitoring-worker"
    migrator = "migrate"
    redis    = "redis"
  }
  # Workloads that log in to Cloud SQL, each as its own IAM database user.
  database_users = toset(["api", "manager", "runner", "monitor", "migrator"])
  # Secret => workloads that may read it.
  secret_readers = {
    "secret-key"     = ["api", "manager", "runner", "monitor", "migrator"]
    "redis-password" = ["api", "manager", "runner", "monitor", "redis"]
  }
  secret_grants = merge([
    for secret, readers in local.secret_readers : { for w in readers : "${secret}/${w}" => { secret = secret, workload = w } }
  ]...)
}

resource "google_service_account" "workload" {
  for_each     = local.workloads
  account_id   = "${var.name}-${each.key}"
  display_name = "BYOC platform: ${each.value}"
  depends_on   = [google_project_service.this]
}

resource "google_service_account_iam_member" "workload_identity" {
  for_each           = local.workloads
  service_account_id = google_service_account.workload[each.key].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[${var.namespace}/${each.value}]"
}

resource "google_project_iam_member" "cloudsql" {
  for_each = { for pair in setproduct(tolist(local.database_users), ["roles/cloudsql.client", "roles/cloudsql.instanceUser"]) : "${pair[0]}/${pair[1]}" => { workload = pair[0], role = pair[1] } }
  project  = var.project_id
  role     = each.value.role
  member   = google_service_account.workload[each.value.workload].member
}

# Nodes run as a dedicated service account with only what nodes need.
resource "google_service_account" "nodes" {
  account_id   = "${var.name}-gke-nodes"
  display_name = "BYOC platform: GKE nodes"
}

resource "google_project_iam_member" "nodes" {
  for_each = toset([
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/monitoring.viewer",
    "roles/stackdriver.resourceMetadata.writer",
    "roles/autoscaling.metricsWriter",
  ])
  project = var.project_id
  role    = each.value
  member  = google_service_account.nodes.member
}
