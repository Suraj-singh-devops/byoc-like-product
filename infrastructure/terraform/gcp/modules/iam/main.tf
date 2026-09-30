# Identity of the database VMs.
#
# The service account has no project-level roles by default. The engine module grants it
# access to exactly this cluster's secrets and artifacts bucket (resource-level IAM).

variable "project_id" {
  type = string
}

variable "cluster_id" {
  type = string
}

variable "name_prefix" {
  type = string
}

variable "project_roles" {
  description = "Optional project roles, e.g. roles/logging.logWriter for the Ops Agent. Needs setIamPolicy on the project."
  type        = list(string)
  default     = []
}

resource "google_service_account" "node" {
  project      = var.project_id
  account_id   = "byoc-${substr(md5(var.cluster_id), 0, 16)}"
  display_name = "BYOC ${var.name_prefix} nodes"
  description  = "Database VMs of BYOC cluster ${var.cluster_id}. Access limited to this cluster's secrets and artifacts."
}

resource "google_project_iam_member" "node" {
  for_each = toset(var.project_roles)
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.node.email}"
}

output "email" {
  value = google_service_account.node.email
}

output "member" {
  value = "serviceAccount:${google_service_account.node.email}"
}
