# An Elasticsearch cluster on Compute Engine, in the customer's project.
#
# Composes the generic network, iam, artifacts, storage and compute modules and adds the
# engine-specific parts: a cluster CA and node certificate, the elastic password (all in
# Secret Manager, readable only by this cluster's VMs) and the bootstrap script.

locals {
  package_architecture = var.architecture == "arm64" ? "arm64" : "amd64"
  network_tag          = "byoc-es-${substr(md5(var.cluster_id), 0, 12)}"
  hostnames            = { for name, node in var.nodes : name => "${var.name_prefix}-${name}.${node.zone}.c.${var.project_id}.internal" }
  seed_hosts           = [for name in var.seed_nodes : "${local.hostnames[name]}:9300"]
  node_zones           = distinct([for node in values(var.nodes) : node.zone])
}

module "network" {
  source              = "../network"
  project_id          = var.project_id
  region              = var.region
  name_prefix         = var.name_prefix
  create_network      = var.network.create
  subnet_cidr         = var.network.subnet_cidr
  existing_network    = var.network.existing_network
  existing_subnetwork = var.network.existing_subnetwork
  target_tags         = [local.network_tag]
  internal_ports      = ["9200", "9300"]
  client_port         = "9200"
  client_cidrs        = var.client_cidrs
  enable_iap_ssh      = var.enable_iap_ssh
}

module "iam" {
  source      = "../iam"
  project_id  = var.project_id
  cluster_id  = var.cluster_id
  name_prefix = var.name_prefix
}

module "artifacts" {
  source            = "../artifacts"
  project_id        = var.project_id
  region            = var.region
  cluster_id        = var.cluster_id
  reader_member     = module.iam.member
  agent_binary_path = var.agent_binary_path
  labels            = var.labels
}

# ------------------------------------------------------------------ TLS + credentials

resource "tls_private_key" "ca" {
  algorithm   = "ECDSA"
  ecdsa_curve = "P256"
}

resource "tls_self_signed_cert" "ca" {
  private_key_pem       = tls_private_key.ca.private_key_pem
  is_ca_certificate     = true
  validity_period_hours = 87600
  allowed_uses          = ["cert_signing", "crl_signing", "digital_signature"]

  subject {
    common_name  = "${var.es_cluster_name} CA"
    organization = "BYOC Database Platform"
  }
}

resource "tls_private_key" "node" {
  algorithm   = "ECDSA"
  ecdsa_curve = "P256"
}

resource "tls_cert_request" "node" {
  private_key_pem = tls_private_key.node.private_key_pem
  dns_names       = concat(["localhost"], [for zone in local.node_zones : "*.${zone}.c.${var.project_id}.internal"])
  ip_addresses    = ["127.0.0.1"]

  subject {
    common_name  = var.es_cluster_name
    organization = "BYOC Database Platform"
  }
}

resource "tls_locally_signed_cert" "node" {
  cert_request_pem      = tls_cert_request.node.cert_request_pem
  ca_private_key_pem    = tls_private_key.ca.private_key_pem
  ca_cert_pem           = tls_self_signed_cert.ca.cert_pem
  validity_period_hours = 17520
  allowed_uses          = ["digital_signature", "key_encipherment", "server_auth", "client_auth"]
}

resource "random_password" "elastic" {
  length  = 32
  special = false
}

locals {
  secret_values = {
    "ca-cert"          = tls_self_signed_cert.ca.cert_pem
    "node-cert"        = tls_locally_signed_cert.node.cert_pem
    "node-key"         = tls_private_key.node.private_key_pem_pkcs8
    "elastic-password" = random_password.elastic.result
  }
}

resource "google_secret_manager_secret" "this" {
  for_each  = toset(["ca-cert", "node-cert", "node-key", "elastic-password"])
  project   = var.project_id
  secret_id = "${var.name_prefix}-es-${each.key}"
  labels    = var.labels

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "this" {
  for_each    = google_secret_manager_secret.this
  secret      = each.value.id
  secret_data = local.secret_values[each.key]
}

resource "google_secret_manager_secret_iam_member" "node" {
  for_each  = google_secret_manager_secret.this
  project   = var.project_id
  secret_id = each.value.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = module.iam.member
}

# -------------------------------------------------------------------------- nodes

module "storage" {
  source            = "../storage"
  project_id        = var.project_id
  name_prefix       = var.name_prefix
  nodes             = { for name, node in var.nodes : name => { zone = node.zone } }
  size_gb           = var.data_disk_size_gb
  type              = var.data_disk_type
  kms_key_self_link = var.kms_key_self_link
  labels            = var.labels
}

module "compute" {
  source                = "../compute"
  project_id            = var.project_id
  name_prefix           = var.name_prefix
  nodes                 = var.nodes
  machine_type          = var.machine_type
  architecture          = var.architecture
  boot_disk_size_gb     = var.boot_disk_size_gb
  subnetwork            = module.network.subnetwork
  service_account_email = module.iam.email
  network_tags          = [local.network_tag]
  data_disks            = module.storage.disks
  startup_script        = file("${path.module}/scripts/startup.sh")
  labels                = var.labels

  metadata = {
    "byoc-cluster-id"                 = var.cluster_id
    "byoc-cluster-name"               = var.es_cluster_name
    "byoc-es-version"                 = var.es_version
    "byoc-es-apt-repository"          = var.es_package.apt_repository
    "byoc-es-signing-key-url"         = var.es_package.signing_key_url
    "byoc-es-signing-key-fingerprint" = var.es_package.signing_key_fingerprint
    "byoc-es-package-sha256"          = var.es_package.sha256[local.package_architecture]
    "byoc-seed-hosts"                 = join(",", local.seed_hosts)
    "byoc-initial-masters"            = join(",", var.initial_master_nodes)
    "byoc-zone-awareness"             = var.zone_awareness ? "true" : "false"
    "byoc-secret-prefix"              = "projects/${var.project_id}/secrets/${var.name_prefix}-es-"
    "byoc-artifacts-bucket"           = module.artifacts.bucket
    "byoc-agent-object"               = module.artifacts.agent_object
    "byoc-agent-sha256"               = module.artifacts.agent_sha256
    "byoc-agent-version"              = var.agent_version
    "byoc-control-plane-url"          = var.control_plane_url
    "byoc-agent-audience"             = var.agent_audience
  }

  node_metadata = {
    for name, node in var.nodes : name => {
      "byoc-node-name"  = name
      "byoc-node-roles" = join(",", node.roles)
    }
  }

  # Secrets and bucket access must exist before the VMs boot and try to read them.
  depends_on = [
    google_secret_manager_secret_version.this,
    google_secret_manager_secret_iam_member.node,
    module.artifacts,
  ]
}
