# Secret containers only. Values are added out of band (deploy/scripts/create-secrets.sh), so
# they never appear in Terraform state (docs/adr/0005).
resource "google_secret_manager_secret" "platform" {
  for_each  = local.secret_readers
  secret_id = "${var.name}-${each.key}"
  labels    = local.labels

  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }

  depends_on = [google_project_service.this]
}

resource "google_secret_manager_secret_iam_member" "readers" {
  for_each  = local.secret_grants
  secret_id = google_secret_manager_secret.platform[each.value.secret].id
  role      = "roles/secretmanager.secretAccessor"
  member    = google_service_account.workload[each.value.workload].member
}
