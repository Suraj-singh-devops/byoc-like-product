# Private bucket in the customer's project holding the node agent binary. VMs download it
# with their own identity, so nothing needs to be publicly hosted and the control plane does
# not have to be reachable from the VMs. Later phases put engine snapshots here too.

terraform {
  required_version = ">= 1.6.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.0, < 9.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.6, < 4.0"
    }
  }
}

variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "cluster_id" {
  type = string
}

variable "reader_member" {
  description = "IAM member (the node service account) allowed to read objects."
  type        = string
}

variable "agent_binary_path" {
  description = "Local path of the agent binary to upload; empty skips the upload."
  type        = string
  default     = ""
}

variable "labels" {
  type    = map(string)
  default = {}
}

resource "random_id" "suffix" {
  byte_length = 4
}

resource "google_storage_bucket" "this" {
  project = var.project_id
  # Derived from the cluster ID: user input never ends up in the globally visible bucket name.
  name                        = "byoc-${substr(md5(var.cluster_id), 0, 10)}-${random_id.suffix.hex}"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true
  labels                      = var.labels
}

resource "google_storage_bucket_object" "agent" {
  count        = var.agent_binary_path == "" ? 0 : 1
  name         = "agent/byoc-agent"
  bucket       = google_storage_bucket.this.name
  source       = var.agent_binary_path
  content_type = "application/octet-stream"
}

resource "google_storage_bucket_iam_member" "reader" {
  bucket = google_storage_bucket.this.name
  role   = "roles/storage.objectViewer"
  member = var.reader_member
}

output "bucket" {
  value = google_storage_bucket.this.name
}

output "agent_object" {
  value = var.agent_binary_path == "" ? "" : google_storage_bucket_object.agent[0].name
}

output "agent_sha256" {
  value = var.agent_binary_path == "" ? "" : filesha256(var.agent_binary_path)
}
