# Console address and Google-managed certificate (Certificate Manager), when a domain is set.
resource "google_compute_global_address" "console" {
  name = "${var.name}-console"
}

resource "google_certificate_manager_dns_authorization" "console" {
  count  = var.domain == "" ? 0 : 1
  name   = "${var.name}-console"
  domain = var.domain
}

resource "google_certificate_manager_certificate" "console" {
  count = var.domain == "" ? 0 : 1
  name  = "${var.name}-console"

  managed {
    domains            = [var.domain]
    dns_authorizations = [google_certificate_manager_dns_authorization.console[0].id]
  }
}

resource "google_certificate_manager_certificate_map" "console" {
  count = var.domain == "" ? 0 : 1
  name  = "${var.name}-console"
}

resource "google_certificate_manager_certificate_map_entry" "console" {
  count        = var.domain == "" ? 0 : 1
  name         = "${var.name}-console"
  map          = google_certificate_manager_certificate_map.console[0].name
  certificates = [google_certificate_manager_certificate.console[0].id]
  hostname     = var.domain
}
