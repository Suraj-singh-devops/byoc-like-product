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
  description = <<-EOT
    Node name => placement. Combined layout: node-1..node-N, the first three master-eligible.
    Dedicated layout (docs/adr/0016): master-N, data-N and coord-N, with their own machine type and
    disk size; coordinating nodes have no roles. config_generation changes one node at a time
    during a rolling restart (docs/adr/0017).
  EOT
  type = map(object({
    zone              = string
    ordinal           = number
    roles             = list(string)
    machine_type      = optional(string)
    data_disk_size_gb = optional(number)
    config_generation = optional(number, 0)
  }))

  validation {
    condition = alltrue([
      for node in values(var.nodes) : alltrue([for role in node.roles : contains(["master", "data", "ingest"], role)])
    ])
    error_message = "Node roles are master, data and ingest; an empty list is a coordinating-only node."
  }

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

# ------------------------------------------------------------ topology and configuration

variable "layout" {
  description = "combined (every node has every role) or dedicated (master, data, coordinating groups)."
  type        = string
  default     = "combined"

  validation {
    condition     = contains(["combined", "dedicated"], var.layout)
    error_message = "layout must be combined or dedicated."
  }
}

variable "forced_awareness_zones" {
  description = "Zones for forced allocation awareness: replicas never pile up in the zones that are left when one fails."
  type        = list(string)
  default     = []
}

variable "load_balancer_nodes" {
  description = "Nodes behind the internal TCP load balancer on 9200 (the coordinating nodes); empty for none."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for name in var.load_balancer_nodes : contains(keys(var.nodes), name)])
    error_message = "load_balancer_nodes must name nodes of this cluster."
  }
}

variable "cluster_settings" {
  description = "Allowlisted dynamic settings the agent applies live on the elected master (docs/adr/0017)."
  type        = map(string)
  default     = {}

  validation {
    condition = alltrue([for key in keys(var.cluster_settings) : contains([
      "cluster.routing.allocation.disk.watermark.low",
      "cluster.routing.allocation.disk.watermark.high",
      "cluster.routing.allocation.disk.watermark.flood_stage",
      "cluster.routing.allocation.enable",
      "cluster.routing.rebalance.enable",
      "cluster.routing.allocation.cluster_concurrent_rebalance",
      "cluster.routing.allocation.node_concurrent_recoveries",
      "indices.recovery.max_bytes_per_sec",
      "cluster.max_shards_per_node",
      "action.destructive_requires_name",
      "search.max_buckets",
      "indices.breaker.total.limit",
    ], key)])
    error_message = "cluster_settings may only contain the platform's allowlisted dynamic settings."
  }

  validation {
    condition     = alltrue([for value in values(var.cluster_settings) : can(regex("^[0-9a-z%._]{1,16}$", value))])
    error_message = "cluster_settings values must be plain numbers, sizes, percentages or keywords."
  }
}

variable "cluster_settings_hash" {
  description = "Hash of cluster_settings, as the agent reports it once they are applied."
  type        = string
  default     = ""
}

variable "node_settings" {
  description = <<-EOT
    The user's elasticsearch.yml settings (docs/adr/0018), written after the platform's own lines
    as quoted strings and applied by a rolling restart. Keys the platform owns are refused; the
    list must match RESERVED_PREFIXES in the backend and RESERVED in scripts/apply-config.sh.
  EOT
  type        = map(string)
  default     = {}

  validation {
    condition     = length(var.node_settings) <= 70
    error_message = "At most 70 elasticsearch.yml settings."
  }

  validation {
    condition     = alltrue([for key in keys(var.node_settings) : can(regex("^[a-z][a-z0-9_]*(\\.[a-z0-9_-]+)+$", key)) && length(key) <= 128])
    error_message = "node_settings keys must be dotted lowercase setting names."
  }

  validation {
    condition = alltrue([for key in keys(var.node_settings) : !anytrue([
      for prefix in [
        "xpack.security.", "xpack.license.", "network.", "http.port", "http.host", "http.bind_host",
        "http.publish_host", "http.publish_port", "transport.", "discovery.", "cluster.initial_master_nodes",
        "cluster.name", "cluster.routing.allocation.awareness.", "node.name", "node.roles", "node.attr.", "path.",
        "bootstrap.",
      ] : endswith(prefix, ".") ? startswith(key, prefix) : (key == prefix || startswith(key, "${prefix}."))
    ])])
    error_message = "node_settings must not contain settings the platform manages (security, TLS, network, discovery, paths, node identity, zone awareness)."
  }

  validation {
    condition     = alltrue([for value in values(var.node_settings) : length(value) >= 1 && length(value) <= 512 && !can(regex("[\\x00-\\x1f\\x7f]", value))])
    error_message = "node_settings values must be single-line and at most 512 characters."
  }
}

variable "heap_percent" {
  description = "JVM heap as a share of the VM's memory (capped at 31 GB)."
  type        = number
  default     = 50

  validation {
    condition     = var.heap_percent >= 25 && var.heap_percent <= 75
    error_message = "heap_percent must be between 25 and 75."
  }
}
