resource "google_container_cluster" "platform" {
  name             = "${var.name}-platform"
  location         = var.region
  enable_autopilot = true
  network          = google_compute_network.platform.id
  subnetwork       = google_compute_subnetwork.nodes.id

  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }

  # Nodes have no public IPs; the control plane endpoint accepts only the listed ranges.
  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false
  }

  master_authorized_networks_config {
    gcp_public_cidrs_access_enabled = false
    dynamic "cidr_blocks" {
      for_each = var.master_authorized_cidrs
      content {
        cidr_block = cidr_blocks.value
      }
    }
  }

  cluster_autoscaling {
    auto_provisioning_defaults {
      service_account = google_service_account.nodes.email
      oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
    }
  }

  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  # Kubernetes Secrets encrypted with our KMS key; Secret Manager mounted through the add-on.
  database_encryption {
    state    = "ENCRYPTED"
    key_name = google_kms_crypto_key.this["gke-secrets"].id
  }

  secret_manager_config {
    enabled = true
  }

  gateway_api_config {
    channel = "CHANNEL_STANDARD"
  }

  release_channel {
    channel = "REGULAR"
  }

  resource_labels     = local.labels
  deletion_protection = var.deletion_protection

  depends_on = [google_kms_crypto_key_iam_member.service_agents, google_project_iam_member.nodes]
}
