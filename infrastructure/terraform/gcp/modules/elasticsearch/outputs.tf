output "nodes" {
  description = "Node name => instance name, ID, zone, private IP and internal hostname."
  value       = module.compute.nodes
}

output "network" {
  value = module.network.network
}

output "subnetwork" {
  value = module.network.subnetwork
}

output "service_account_email" {
  value = module.iam.email
}

output "secrets" {
  description = "Secret Manager IDs. Read the elastic password in your project with: gcloud secrets versions access latest --secret=<id>"
  value = {
    elastic_password = google_secret_manager_secret.this["elastic-password"].secret_id
    ca_certificate   = google_secret_manager_secret.this["ca-cert"].secret_id
  }
}

output "artifacts_bucket" {
  value = module.artifacts.bucket
}

output "http_endpoints" {
  description = "Private HTTPS endpoints (reachable from the cluster subnet and client_cidrs)."
  value       = [for node in values(module.compute.nodes) : "https://${node.private_ip}:9200"]
}

output "ca_certificate_pem" {
  description = "Cluster CA certificate (public) for clients verifying the HTTPS endpoint."
  value       = tls_self_signed_cert.ca.cert_pem
}

output "has_public_ip" {
  value = module.compute.has_public_ip
}

output "image" {
  value = module.compute.image
}

output "ingress_source_ranges" {
  value = module.network.ingress_source_ranges
}

output "engine_package" {
  description = "The exact package every node installs and how it is verified."
  value = {
    version                 = var.es_version
    architecture            = local.package_architecture
    sha256                  = var.es_package.sha256[local.package_architecture]
    signing_key_fingerprint = var.es_package.signing_key_fingerprint
  }
}

output "endpoint" {
  description = "The cluster's single private HTTPS endpoint (internal load balancer), or null without one."
  value       = local.load_balanced ? "https://${google_compute_address.endpoint[0].address}:9200" : null
}
