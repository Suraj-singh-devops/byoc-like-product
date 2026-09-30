# The platform (control plane) on GKE: implementation plan P3, docs/adr/0004, 0005, 0012.
#
#   VPC (private nodes, Cloud NAT) ─ GKE Autopilot (Workload Identity, Secret Manager add-on,
#   KMS-encrypted Kubernetes secrets) ─ Cloud SQL for PostgreSQL (private IP, IAM login, CMEK)
#   Artifact Registry ─ one Google service account per backend role ─ Secret Manager secrets
#   (containers only: values are added out of band, never in Terraform state) ─ state bucket for
#   customer Terraform (P5) ─ optional managed certificate for the console.

locals {
  apis = toset([
    "artifactregistry.googleapis.com",
    "certificatemanager.googleapis.com",
    "cloudkms.googleapis.com",
    "compute.googleapis.com",
    "container.googleapis.com",
    "iam.googleapis.com",
    "secretmanager.googleapis.com",
    "servicenetworking.googleapis.com",
    "sqladmin.googleapis.com",
    "storage.googleapis.com",
  ])
  labels = { "managed-by" = "terraform", "app" = "byoc-platform" }
}

resource "google_project_service" "this" {
  for_each           = local.apis
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

data "google_project" "this" {
  project_id = var.project_id
}
