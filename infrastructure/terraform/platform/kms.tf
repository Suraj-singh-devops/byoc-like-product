resource "google_kms_key_ring" "platform" {
  name       = "${var.name}-platform"
  location   = var.region
  depends_on = [google_project_service.this]
}

resource "google_kms_crypto_key" "this" {
  for_each        = toset(["gke-secrets", "cloudsql", "state"])
  name            = each.key
  key_ring        = google_kms_key_ring.platform.id
  rotation_period = "7776000s" # 90 days
  labels          = local.labels

  lifecycle {
    prevent_destroy = true
  }
}

# Service agents that encrypt with the keys.
resource "google_project_service_identity" "sql" {
  provider = google-beta
  project  = var.project_id
  service  = "sqladmin.googleapis.com"
}

locals {
  kms_users = {
    "gke-secrets" = "serviceAccount:service-${data.google_project.this.number}@container-engine-robot.iam.gserviceaccount.com"
    "cloudsql"    = "serviceAccount:${google_project_service_identity.sql.email}"
    "state"       = "serviceAccount:service-${data.google_project.this.number}@gs-project-accounts.iam.gserviceaccount.com"
  }
}

resource "google_kms_crypto_key_iam_member" "service_agents" {
  for_each      = local.kms_users
  crypto_key_id = google_kms_crypto_key.this[each.key].id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = each.value
}
