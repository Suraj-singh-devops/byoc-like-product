# One persistent data disk per node, separate from the boot disk so data survives VM
# re-creation. Persistent Disks are always encrypted at rest (Google-managed keys by
# default, or a customer-managed Cloud KMS key).

variable "project_id" {
  type = string
}

variable "name_prefix" {
  type = string
}

variable "nodes" {
  description = "Node name => zone, and optionally the node's own disk size (docs/adr/0016)."
  type        = map(object({ zone = string, size_gb = optional(number) }))
}

variable "size_gb" {
  description = "Disk size of nodes that do not set their own."
  type        = number
}

variable "type" {
  type    = string
  default = "pd-balanced"

  validation {
    condition     = contains(["pd-balanced", "pd-ssd", "pd-standard"], var.type)
    error_message = "Supported disk types: pd-balanced, pd-ssd, pd-standard."
  }
}

variable "kms_key_self_link" {
  description = "Optional Cloud KMS key for customer-managed encryption (CMEK)."
  type        = string
  default     = ""
}

variable "labels" {
  type    = map(string)
  default = {}
}

resource "google_compute_disk" "data" {
  for_each = var.nodes
  project  = var.project_id
  name     = "${var.name_prefix}-${each.key}-data"
  zone     = each.value.zone
  type     = var.type
  size     = coalesce(each.value.size_gb, var.size_gb)
  labels   = merge(var.labels, { "byoc-node" = each.key, "byoc-disk" = "data" })

  dynamic "disk_encryption_key" {
    for_each = var.kms_key_self_link == "" ? [] : [var.kms_key_self_link]
    content {
      kms_key_self_link = disk_encryption_key.value
    }
  }
}

output "disks" {
  value = { for name, disk in google_compute_disk.data : name => { self_link = disk.self_link, name = disk.name } }
}

output "sizes" {
  value = { for name, disk in google_compute_disk.data : name => disk.size }
}
