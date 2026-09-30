resource "google_compute_network" "platform" {
  name                    = "${var.name}-platform"
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"
  depends_on              = [google_project_service.this]
}

resource "google_compute_subnetwork" "nodes" {
  name                     = "${var.name}-nodes"
  region                   = var.region
  network                  = google_compute_network.platform.id
  ip_cidr_range            = var.network_cidrs.nodes
  private_ip_google_access = true

  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = var.network_cidrs.pods
  }

  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = var.network_cidrs.services
  }
}

# Private nodes reach the internet (image pulls, package mirrors) only through NAT.
resource "google_compute_router" "platform" {
  name    = "${var.name}-router"
  region  = var.region
  network = google_compute_network.platform.id
}

resource "google_compute_router_nat" "platform" {
  name                               = "${var.name}-nat"
  router                             = google_compute_router.platform.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"

  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}

# Private Service Access: Cloud SQL gets a private IP inside this VPC, no public IP.
resource "google_compute_global_address" "sql" {
  name          = "${var.name}-sql-range"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  network       = google_compute_network.platform.id
  address       = cidrhost(var.network_cidrs.sql, 0)
  prefix_length = tonumber(split("/", var.network_cidrs.sql)[1])
}

resource "google_service_networking_connection" "sql" {
  network                 = google_compute_network.platform.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.sql.name]
}
