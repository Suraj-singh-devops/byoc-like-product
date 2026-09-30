# Reference deployment of one Elasticsearch cluster, runnable by hand:
#
#   cp terraform.tfvars.example terraform.tfvars   # then edit
#   tofu init && tofu apply                         # or terraform
#
# The control plane generates the equivalent root module for every cluster it manages
# (see backend/app/providers/cloud/gcp/terraform.py); keep the two in sync.

terraform {
  required_version = ">= 1.6.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.0, < 9.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.6, < 4.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = ">= 4.0, < 5.0"
    }
  }

  # Keep state in your own project for anything beyond a test:
  # backend "gcs" {
  #   bucket = "customer-byoc-tfstate"
  #   prefix = "byoc/prototype"
  # }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  # The control plane's version catalog is the single source of exact versions and checksums.
  catalog  = yamldecode(file("${path.module}/../../../../../backend/app/providers/database/elasticsearch/versions.yaml"))
  releases = { for v in local.catalog.versions : v.version => v if v.status != "withdrawn" }
  release  = local.releases[var.es_version] # a version missing from the catalog fails here, by name
  es_package = {
    apt_repository          = local.release.package.apt_repository
    signing_key_url         = local.release.package.signing_key_url
    signing_key_fingerprint = local.release.package.signing_key_fingerprint
    sha256                  = { for arch, artifact in local.release.package.artifacts : arch => artifact.sha256 }
  }
  zones = var.high_availability ? var.ha_zones : [var.zone]
  nodes = {
    for i in range(1, var.node_count + 1) : "node-${i}" => {
      zone    = local.zones[(i - 1) % length(local.zones)]
      ordinal = i
      roles   = i <= 3 ? ["master", "data", "ingest"] : ["data", "ingest"]
    }
  }
  masters = [for i in range(1, min(var.node_count, 3) + 1) : "node-${i}"]
}

module "elasticsearch" {
  source               = "../../modules/elasticsearch"
  cluster_id           = var.cluster_id
  name_prefix          = var.name_prefix
  project_id           = var.project_id
  region               = var.region
  nodes                = local.nodes
  machine_type         = var.machine_type
  architecture         = startswith(var.machine_type, "t2a-") ? "arm64" : "x86_64"
  data_disk_size_gb    = var.storage_gb
  data_disk_type       = var.storage_type
  es_cluster_name      = var.cluster_name
  es_version           = var.es_version
  es_package           = local.es_package
  seed_nodes           = local.masters
  initial_master_nodes = local.masters
  zone_awareness       = var.high_availability && length(local.zones) > 1
  agent_binary_path    = var.agent_binary_path
  agent_version        = var.agent_version
  control_plane_url    = var.control_plane_url
  labels = {
    "managed-by"      = "byoc"
    "byoc-cluster-id" = var.cluster_id
    "byoc-engine"     = "elasticsearch"
  }
}

output "nodes" {
  value = module.elasticsearch.nodes
}

output "http_endpoints" {
  value = module.elasticsearch.http_endpoints
}

output "secrets" {
  value = module.elasticsearch.secrets
}

output "ca_certificate_pem" {
  value = module.elasticsearch.ca_certificate_pem
}
