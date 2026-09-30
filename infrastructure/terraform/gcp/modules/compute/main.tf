# Database VMs: Shielded VMs with no external IP, a dedicated service account, the data
# disk attached as "data", guest attributes enabled (bootstrap/agent status), OS Login on
# and project-wide SSH keys blocked.

variable "project_id" {
  type = string
}

variable "name_prefix" {
  type = string
}

variable "nodes" {
  type = map(object({
    zone    = string
    ordinal = number
    roles   = list(string)
  }))
}

variable "machine_type" {
  type = string
}

variable "architecture" {
  type    = string
  default = "x86_64"

  validation {
    condition     = contains(["x86_64", "arm64"], var.architecture)
    error_message = "architecture must be x86_64 or arm64."
  }
}

variable "boot_disk_size_gb" {
  type    = number
  default = 20
}

variable "subnetwork" {
  type = string
}

variable "service_account_email" {
  type = string
}

variable "network_tags" {
  type = list(string)
}

variable "data_disks" {
  description = "Node name => data disk (from the storage module)."
  type        = map(object({ self_link = string, name = string }))
}

variable "startup_script" {
  type = string
}

variable "metadata" {
  description = "Metadata shared by every node."
  type        = map(string)
  default     = {}
}

variable "node_metadata" {
  description = "Node name => extra metadata for that node."
  type        = map(map(string))
  default     = {}
}

variable "labels" {
  type    = map(string)
  default = {}
}

locals {
  image = var.architecture == "arm64" ? "debian-cloud/debian-12-arm64" : "debian-cloud/debian-12"
}

resource "google_compute_instance" "node" {
  for_each                  = var.nodes
  project                   = var.project_id
  name                      = "${var.name_prefix}-${each.key}"
  zone                      = each.value.zone
  machine_type              = var.machine_type
  tags                      = var.network_tags
  labels                    = merge(var.labels, { "byoc-node" = each.key })
  allow_stopping_for_update = true
  deletion_protection       = false

  boot_disk {
    auto_delete = true
    initialize_params {
      image = local.image
      size  = var.boot_disk_size_gb
      type  = "pd-balanced"
    }
  }

  attached_disk {
    source      = var.data_disks[each.key].self_link
    device_name = "data"
    mode        = "READ_WRITE"
  }

  network_interface {
    subnetwork = var.subnetwork
    # No access_config block: the VM gets no external IP address.
  }

  service_account {
    email  = var.service_account_email
    scopes = ["cloud-platform"]
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  scheduling {
    automatic_restart   = true
    on_host_maintenance = var.architecture == "arm64" ? "TERMINATE" : "MIGRATE"
  }

  metadata = merge(
    var.metadata,
    lookup(var.node_metadata, each.key, {}),
    {
      "startup-script"          = var.startup_script
      "enable-guest-attributes" = "TRUE"
      "enable-oslogin"          = "TRUE"
      "block-project-ssh-keys"  = "TRUE"
    },
  )

  lifecycle {
    # The image family moves to newer images over time; that must never replace a data node.
    ignore_changes = [boot_disk[0].initialize_params[0].image]
  }
}

output "nodes" {
  value = {
    for name, vm in google_compute_instance.node : name => {
      instance_name = vm.name
      instance_id   = vm.instance_id
      zone          = vm.zone
      private_ip    = vm.network_interface[0].network_ip
      hostname      = "${vm.name}.${vm.zone}.c.${var.project_id}.internal"
    }
  }
}

output "image" {
  value = local.image
}

output "has_public_ip" {
  value = anytrue([for vm in google_compute_instance.node : length(vm.network_interface[0].access_config) > 0])
}
