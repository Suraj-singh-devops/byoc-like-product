variable "cluster_id" {
  description = "Control-plane ID of the cluster (UUID)."
  type        = string
}

variable "name_prefix" {
  description = "Prefix of every resource name, e.g. production-search-3f9a."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,43}[a-z0-9]$", var.name_prefix))
    error_message = "name_prefix must be 3-45 lowercase letters, digits or hyphens."
  }
}

variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "nodes" {
  description = "Node name => placement. Names are node-1..node-N; the first three are master-eligible."
  type = map(object({
    zone    = string
    ordinal = number
    roles   = list(string)
  }))

  validation {
    condition     = length(var.nodes) >= 1
    error_message = "At least one node is required."
  }
}

variable "machine_type" {
  type = string
}

variable "architecture" {
  type    = string
  default = "x86_64"
}

variable "boot_disk_size_gb" {
  type    = number
  default = 20
}

variable "data_disk_size_gb" {
  type = number
}

variable "data_disk_type" {
  type    = string
  default = "pd-balanced"
}

variable "kms_key_self_link" {
  description = "Optional Cloud KMS key to encrypt the data disks (CMEK)."
  type        = string
  default     = ""
}

variable "network" {
  description = <<-EOT
    The customer's registered network (create = false; docs/adr/0013): resource paths of the VPC
    and subnet and the subnet's primary range, from the platform's validated lookup. The default
    creates a dedicated VPC, kept for clusters created before registered networks.
  EOT
  type = object({
    create              = bool
    subnet_cidr         = string
    existing_network    = string
    existing_subnetwork = string
  })
  default = {
    create              = true
    subnet_cidr         = "10.10.0.0/24"
    existing_network    = ""
    existing_subnetwork = ""
  }

  validation {
    condition = var.network.create || (
      can(regex("^projects/[a-z][-a-z0-9]{4,28}[a-z0-9]/global/networks/[a-z]([-a-z0-9]{0,61}[a-z0-9])?$", var.network.existing_network)) &&
      can(regex("^projects/[a-z][-a-z0-9]{4,28}[a-z0-9]/regions/[a-z0-9-]+/subnetworks/[a-z]([-a-z0-9]{0,61}[a-z0-9])?$", var.network.existing_subnetwork))
    )
    error_message = "With create = false, existing_network and existing_subnetwork must be resource paths (projects/<project>/global/networks/<name>, projects/<project>/regions/<region>/subnetworks/<name>)."
  }

  validation {
    condition     = can(cidrhost(var.network.subnet_cidr, 0)) && !startswith(var.network.subnet_cidr, "0.0.0.0/")
    error_message = "subnet_cidr must be the subnet's range, never 0.0.0.0/0."
  }
}

variable "client_cidrs" {
  description = "Extra CIDRs allowed to reach port 9200 (by default only the cluster subnet can)."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for cidr in var.client_cidrs : can(cidrhost(cidr, 0)) && !startswith(cidr, "0.0.0.0/")])
    error_message = "client_cidrs must be CIDR ranges; 0.0.0.0/0 is never allowed to reach the database."
  }
}

variable "enable_iap_ssh" {
  type    = bool
  default = false
}

variable "labels" {
  type    = map(string)
  default = {}
}

variable "es_cluster_name" {
  type = string
}

variable "es_version" {
  description = "Exact Elasticsearch version from the platform's version catalog, e.g. 9.5.4."
  type        = string

  validation {
    condition     = can(regex("^[0-9]+\\.[0-9]+\\.[0-9]+$", var.es_version))
    error_message = "es_version must be an exact version such as 9.5.4 (no latest, no wildcards)."
  }
}

variable "es_package" {
  description = "Package source and verification data for es_version (from the version catalog)."
  type = object({
    apt_repository          = string
    signing_key_url         = string
    signing_key_fingerprint = string
    sha256                  = map(string)
  })

  validation {
    condition     = startswith(var.es_package.apt_repository, "https://") && startswith(var.es_package.signing_key_url, "https://")
    error_message = "The package repository and its signing key must be fetched over HTTPS."
  }

  validation {
    condition     = can(regex("^[0-9A-F]{40}$", var.es_package.signing_key_fingerprint))
    error_message = "signing_key_fingerprint must be a 40-hex-digit OpenPGP fingerprint."
  }

  validation {
    condition     = alltrue([for arch in ["amd64", "arm64"] : can(regex("^[0-9a-f]{64}$", lookup(var.es_package.sha256, arch, "")))])
    error_message = "sha256 must give the package checksum for amd64 and arm64."
  }
}

variable "seed_nodes" {
  description = "Master-eligible node names used for discovery."
  type        = list(string)
}

variable "initial_master_nodes" {
  description = "Nodes that bootstrap the cluster the first time. Must never change afterwards."
  type        = list(string)
}

variable "zone_awareness" {
  description = "Enable shard allocation awareness on the zone attribute (HA across zones)."
  type        = bool
  default     = false
}

variable "agent_binary_path" {
  description = "Local path of the byoc-agent binary to upload for the VMs; empty skips the agent."
  type        = string
  default     = ""
}

variable "agent_version" {
  type    = string
  default = ""
}

variable "control_plane_url" {
  description = "HTTPS URL agents use to reach the control plane; empty means guest attributes only."
  type        = string
  default     = ""
}

variable "agent_audience" {
  description = "Audience of the VM identity token the agent presents when registering."
  type        = string
  default     = "byoc-control-plane"
}
