# Internal TCP load balancer in front of the coordinating nodes (dedicated layout,
# docs/adr/0016): one private address in the cluster subnet for clients, port 9200 only.
# Never external: the forwarding rule is INTERNAL and the address is from the subnet.

locals {
  lb_zones = local.load_balanced ? distinct([for name in var.load_balancer_nodes : var.nodes[name].zone]) : []
}

resource "google_compute_address" "endpoint" {
  count        = local.load_balanced ? 1 : 0
  project      = var.project_id
  region       = var.region
  name         = "${var.name_prefix}-endpoint"
  address_type = "INTERNAL"
  subnetwork   = module.network.subnetwork
  purpose      = "GCE_ENDPOINT"
  labels       = var.labels
}

# Unmanaged instance groups, one per zone, holding the load-balanced nodes.
resource "google_compute_instance_group" "endpoint" {
  for_each = toset(local.lb_zones)
  project  = var.project_id
  zone     = each.key
  name     = "${var.name_prefix}-endpoint-${substr(md5(each.key), 0, 6)}"
  network  = module.network.network
  instances = [
    for name in var.load_balancer_nodes : module.compute.nodes[name].self_link if var.nodes[name].zone == each.key
  ]
}

resource "google_compute_region_health_check" "endpoint" {
  count   = local.load_balanced ? 1 : 0
  project = var.project_id
  region  = var.region
  name    = "${var.name_prefix}-endpoint"

  # TCP: an HTTPS probe would get 401 from the secured REST API.
  tcp_health_check {
    port = 9200
  }

  check_interval_sec  = 5
  timeout_sec         = 5
  healthy_threshold   = 2
  unhealthy_threshold = 2
}

resource "google_compute_region_backend_service" "endpoint" {
  count                 = local.load_balanced ? 1 : 0
  project               = var.project_id
  region                = var.region
  name                  = "${var.name_prefix}-endpoint"
  load_balancing_scheme = "INTERNAL"
  protocol              = "TCP"
  health_checks         = [google_compute_region_health_check.endpoint[0].id]

  dynamic "backend" {
    for_each = google_compute_instance_group.endpoint
    content {
      group          = backend.value.id
      balancing_mode = "CONNECTION"
    }
  }
}

resource "google_compute_forwarding_rule" "endpoint" {
  count                 = local.load_balanced ? 1 : 0
  project               = var.project_id
  region                = var.region
  name                  = "${var.name_prefix}-endpoint"
  load_balancing_scheme = "INTERNAL"
  ip_protocol           = "TCP"
  ports                 = ["9200"]
  ip_address            = google_compute_address.endpoint[0].address
  network               = module.network.network
  subnetwork            = module.network.subnetwork
  backend_service       = google_compute_region_backend_service.endpoint[0].id
  allow_global_access   = false
  labels                = var.labels
}

# Google's health-check ranges may probe 9200 on the load-balanced nodes only. Clients reach the
# endpoint under the existing internal/client rules (the load balancer is pass-through).
resource "google_compute_firewall" "endpoint_health_checks" {
  count         = local.load_balanced ? 1 : 0
  project       = var.project_id
  name          = "${var.name_prefix}-endpoint-hc"
  network       = module.network.network
  direction     = "INGRESS"
  source_ranges = ["35.191.0.0/16", "130.211.0.0/22"]
  target_tags   = [local.lb_tag]

  allow {
    protocol = "tcp"
    ports    = ["9200"]
  }
}
