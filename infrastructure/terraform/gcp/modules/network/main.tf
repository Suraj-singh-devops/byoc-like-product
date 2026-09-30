# Private networking for one database cluster.
#
# * An existing customer network registered with the platform (docs/adr/0013): only firewall
#   rules scoped to the cluster's VMs are created. The network and subnet are passed as resource
#   paths and the subnet's primary range comes from the platform's validated lookup, so no data
#   source reads the customer's network (deleting a cluster never depends on it).
# * Or, for clusters created before registered networks, a dedicated VPC and subnet with Cloud
#   Router and NAT (docs/adr/0006).
# * No public IPs: VMs reach the internet (package repositories, the control plane) through
#   Cloud NAT, and Google APIs through Private Google Access.
# * Ingress is closed except for the engine ports from inside the subnet, optional client
#   CIDRs, and optional SSH through Identity-Aware Proxy for break-glass access.

variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "name_prefix" {
  type = string
}

variable "create_network" {
  description = "Create a dedicated VPC and subnet. When false, existing_network and existing_subnetwork are used."
  type        = bool
  default     = true
}

variable "subnet_cidr" {
  description = "Range of the dedicated subnet, or the existing subnet's primary range."
  type        = string
  default     = "10.10.0.0/24"
}

variable "existing_network" {
  description = "Existing VPC network: projects/<project>/global/networks/<name>."
  type        = string
  default     = ""
}

variable "existing_subnetwork" {
  description = "Existing subnetwork: projects/<project>/regions/<region>/subnetworks/<name>."
  type        = string
  default     = ""
}

variable "create_nat" {
  description = "Create Cloud Router + Cloud NAT for outbound internet access (only with create_network)."
  type        = bool
  default     = true
}

variable "target_tags" {
  description = "Network tags of the cluster's VMs."
  type        = list(string)
}

variable "internal_ports" {
  description = "TCP ports open between cluster members and from the subnet."
  type        = list(string)
}

variable "client_port" {
  type = string
}

variable "client_cidrs" {
  description = "Additional CIDR ranges allowed to reach the client port (e.g. peered application subnets)."
  type        = list(string)
  default     = []
}

variable "enable_iap_ssh" {
  description = "Allow SSH from Identity-Aware Proxy (break-glass only; the platform never uses SSH)."
  type        = bool
  default     = false
}

resource "google_compute_network" "this" {
  count                   = var.create_network ? 1 : 0
  project                 = var.project_id
  name                    = "${var.name_prefix}-vpc"
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"
}

resource "google_compute_subnetwork" "this" {
  count                    = var.create_network ? 1 : 0
  project                  = var.project_id
  name                     = "${var.name_prefix}-subnet"
  region                   = var.region
  network                  = google_compute_network.this[0].id
  ip_cidr_range            = var.subnet_cidr
  private_ip_google_access = true
}

locals {
  network_self_link    = var.create_network ? google_compute_network.this[0].self_link : var.existing_network
  subnetwork_self_link = var.create_network ? google_compute_subnetwork.this[0].self_link : var.existing_subnetwork
  subnet_cidr          = var.subnet_cidr
}

resource "google_compute_router" "this" {
  count   = var.create_network && var.create_nat ? 1 : 0
  project = var.project_id
  name    = "${var.name_prefix}-router"
  region  = var.region
  network = google_compute_network.this[0].id
}

resource "google_compute_router_nat" "this" {
  count                              = var.create_network && var.create_nat ? 1 : 0
  project                            = var.project_id
  name                               = "${var.name_prefix}-nat"
  router                             = google_compute_router.this[0].name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "LIST_OF_SUBNETWORKS"

  subnetwork {
    name                    = google_compute_subnetwork.this[0].id
    source_ip_ranges_to_nat = ["ALL_IP_RANGES"]
  }

  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}

resource "google_compute_firewall" "internal" {
  project       = var.project_id
  name          = "${var.name_prefix}-allow-internal"
  network       = local.network_self_link
  direction     = "INGRESS"
  priority      = 1000
  source_ranges = [local.subnet_cidr]
  target_tags   = var.target_tags

  allow {
    protocol = "tcp"
    ports    = var.internal_ports
  }

  lifecycle {
    precondition {
      condition     = var.create_network || (var.existing_network != "" && var.existing_subnetwork != "")
      error_message = "An existing network and subnetwork are required when create_network is false."
    }
  }
}

resource "google_compute_firewall" "clients" {
  count         = length(var.client_cidrs) > 0 ? 1 : 0
  project       = var.project_id
  name          = "${var.name_prefix}-allow-clients"
  network       = local.network_self_link
  direction     = "INGRESS"
  priority      = 1000
  source_ranges = var.client_cidrs
  target_tags   = var.target_tags

  allow {
    protocol = "tcp"
    ports    = [var.client_port]
  }
}

resource "google_compute_firewall" "iap_ssh" {
  count         = var.enable_iap_ssh ? 1 : 0
  project       = var.project_id
  name          = "${var.name_prefix}-allow-iap-ssh"
  network       = local.network_self_link
  direction     = "INGRESS"
  priority      = 1000
  source_ranges = ["35.235.240.0/20"]
  target_tags   = var.target_tags

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}

output "network" {
  value = local.network_self_link
}

output "subnetwork" {
  value = local.subnetwork_self_link
}

output "subnet_cidr" {
  value = local.subnet_cidr
}

output "created" {
  description = "Network resources this module created (none in an existing network)."
  value = {
    network    = length(google_compute_network.this)
    subnetwork = length(google_compute_subnetwork.this)
    router     = length(google_compute_router.this)
    nat        = length(google_compute_router_nat.this)
  }
}

output "ingress_source_ranges" {
  description = "Every source range allowed in by this module's firewall rules."
  value = concat(
    [local.subnet_cidr],
    var.client_cidrs,
    var.enable_iap_ssh ? ["35.235.240.0/20"] : [],
  )
}
