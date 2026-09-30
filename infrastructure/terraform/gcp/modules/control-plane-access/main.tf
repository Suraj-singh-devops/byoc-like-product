# One-time customer onboarding: run in the customer's project to let the BYOC control plane
# manage database infrastructure there, with least privilege.
#
# Creates a service account with a custom role holding exactly the permissions the platform
# needs (kept in sync with backend/app/providers/cloud/gcp/permissions.py by a unit test),
# enables the required APIs and, optionally:
#   * lets the platform's own identity impersonate that account (keyless, recommended);
#   * creates a versioned bucket for Terraform state, so the state stays in your project.

terraform {
  required_version = ">= 1.6.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.0, < 9.0"
    }
  }
}

variable "project_id" {
  type = string
}

variable "service_account_id" {
  type    = string
  default = "byoc-control-plane"
}

variable "platform_principal" {
  description = "Identity of the BYOC control plane allowed to impersonate the service account, e.g. serviceAccount:control-plane@byoc-platform.iam.gserviceaccount.com. Empty: use a key instead."
  type        = string
  default     = ""
}

variable "state_bucket_name" {
  description = "Create this bucket for Terraform state (empty: none)."
  type        = string
  default     = ""
}

variable "state_bucket_location" {
  type    = string
  default = "US"
}

locals {
  apis = [
    "compute.googleapis.com",
    "iam.googleapis.com",
    "secretmanager.googleapis.com",
    "storage.googleapis.com",
    "cloudresourcemanager.googleapis.com",
  ]
}

resource "google_project_service" "apis" {
  for_each           = toset(local.apis)
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_project_iam_custom_role" "control_plane" {
  project     = var.project_id
  role_id     = "byocControlPlane"
  title       = "BYOC control plane"
  description = "Permissions the BYOC platform needs to manage database clusters in this project."
  permissions = local.control_plane_permissions
}

resource "google_service_account" "control_plane" {
  project      = var.project_id
  account_id   = var.service_account_id
  display_name = "BYOC control plane"
  description  = "Used by the BYOC platform to provision and operate database clusters."
}

resource "google_project_iam_member" "control_plane" {
  project = var.project_id
  role    = google_project_iam_custom_role.control_plane.id
  member  = "serviceAccount:${google_service_account.control_plane.email}"
}

resource "google_service_account_iam_member" "impersonation" {
  count              = var.platform_principal == "" ? 0 : 1
  service_account_id = google_service_account.control_plane.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = var.platform_principal
}

resource "google_storage_bucket" "state" {
  count                       = var.state_bucket_name == "" ? 0 : 1
  project                     = var.project_id
  name                        = var.state_bucket_name
  location                    = var.state_bucket_location
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = true
  }
}

resource "google_storage_bucket_iam_member" "state" {
  count  = var.state_bucket_name == "" ? 0 : 1
  bucket = google_storage_bucket.state[0].name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.control_plane.email}"
}

output "service_account_email" {
  value = google_service_account.control_plane.email
}

output "custom_role" {
  value = google_project_iam_custom_role.control_plane.id
}

output "state_bucket" {
  value = var.state_bucket_name == "" ? "" : google_storage_bucket.state[0].name
}
