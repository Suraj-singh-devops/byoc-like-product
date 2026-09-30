# Terraform state of customer clusters (used from P5, docs/adr/0005): private, versioned, CMEK.
resource "google_storage_bucket" "state" {
  name                        = "${var.project_id}-${var.name}-tfstate"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  labels                      = local.labels

  versioning {
    enabled = true
  }

  encryption {
    default_kms_key_name = google_kms_crypto_key.this["state"].id
  }

  depends_on = [google_kms_crypto_key_iam_member.service_agents]
}

# Only the terraform-runner reads and writes state (per-organization prefixes and IAM conditions in P5).
resource "google_storage_bucket_iam_member" "runner" {
  bucket = google_storage_bucket.state.name
  role   = "roles/storage.objectAdmin"
  member = google_service_account.workload["runner"].member
}
