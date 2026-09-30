# Plan-only tests with mocked providers: no cloud credentials or network needed.
#   terraform test   (or: tofu test)

mock_provider "google" {}
mock_provider "random" {}
mock_provider "tls" {}

variables {
  cluster_id  = "3f9a2c1e-0000-4000-8000-000000000001"
  name_prefix = "production-search-3f9a"
  project_id  = "customer-prod"
  region      = "asia-south1"
  nodes = {
    "node-1" = { zone = "asia-south1-a", ordinal = 1, roles = ["master", "data", "ingest"] }
    "node-2" = { zone = "asia-south1-b", ordinal = 2, roles = ["master", "data", "ingest"] }
    "node-3" = { zone = "asia-south1-c", ordinal = 3, roles = ["master", "data", "ingest"] }
  }
  machine_type      = "e2-standard-8"
  data_disk_size_gb = 500
  es_cluster_name   = "production-search"
  es_version        = "9.5.4"
  es_package = {
    apt_repository          = "https://artifacts.elastic.co/packages/9.x/apt"
    signing_key_url         = "https://artifacts.elastic.co/GPG-KEY-elasticsearch"
    signing_key_fingerprint = "46095ACC8548582C1A2699A9D27D666CD88E42B4"
    sha256 = {
      amd64 = "9530a71cabc0e47e895023f32eb0e587bedd9f03ff3d0f5ab05de0534c3b52a7"
      arm64 = "155d265ec0b855998288b646069d451b822235caf38e5662e6a974450abef9eb"
    }
  }
  seed_nodes           = ["node-1", "node-2", "node-3"]
  initial_master_nodes = ["node-1", "node-2", "node-3"]
  zone_awareness       = true
  labels               = { "managed-by" = "byoc" }
}

run "ha_cluster" {
  command = plan

  assert {
    condition     = length(module.compute.nodes) == 3
    error_message = "Expected one VM per node."
  }

  assert {
    condition     = module.compute.has_public_ip == false
    error_message = "Database VMs must not get public IP addresses."
  }

  assert {
    condition     = module.network.ingress_source_ranges == ["10.10.0.0/24"]
    error_message = "By default only the cluster subnet may reach the database ports."
  }

  assert {
    condition     = length(google_secret_manager_secret_iam_member.node) == 4
    error_message = "The node service account must get access to exactly the four cluster secrets."
  }

  assert {
    condition     = google_secret_manager_secret.this["elastic-password"].secret_id == "production-search-3f9a-es-elastic-password"
    error_message = "Unexpected secret name."
  }

  assert {
    condition     = module.compute.image == "debian-cloud/debian-12"
    error_message = "x86_64 nodes use the Debian 12 image."
  }

  assert {
    condition     = random_password.elastic.special == false && random_password.elastic.length == 32
    error_message = "The elastic password must be long and shell/URL safe."
  }

  assert {
    condition     = output.engine_package.version == "9.5.4" && output.engine_package.sha256 == "9530a71cabc0e47e895023f32eb0e587bedd9f03ff3d0f5ab05de0534c3b52a7"
    error_message = "x86_64 nodes must install exactly 9.5.4 and verify the amd64 package checksum."
  }
}

run "existing_network" {
  command = plan

  variables {
    network = {
      create              = false
      subnet_cidr         = "10.20.16.0/20"
      existing_network    = "projects/customer-prod/global/networks/prod-vpc"
      existing_subnetwork = "projects/customer-prod/regions/asia-south1/subnetworks/db-subnet"
    }
  }

  assert {
    condition     = module.network.created == { network = 0, subnetwork = 0, router = 0, nat = 0 }
    error_message = "In a registered network the platform must not create a VPC, subnet, router or NAT."
  }

  assert {
    condition     = module.network.network == "projects/customer-prod/global/networks/prod-vpc" && module.network.subnetwork == "projects/customer-prod/regions/asia-south1/subnetworks/db-subnet"
    error_message = "The cluster must use the registered VPC and subnet."
  }

  assert {
    condition     = module.network.ingress_source_ranges == ["10.20.16.0/20"]
    error_message = "Only the registered subnet's range may reach the database ports."
  }

  assert {
    condition     = length(module.compute.nodes) == 3 && module.compute.has_public_ip == false
    error_message = "Nodes run in the registered subnet without public IPs."
  }
}

run "dedicated_network_for_older_clusters" {
  command = plan

  assert {
    condition     = module.network.created == { network = 1, subnetwork = 1, router = 1, nat = 1 }
    error_message = "Clusters created before registered networks keep their dedicated VPC, subnet and NAT."
  }
}

run "rejects_existing_network_names" {
  command = plan

  variables {
    network = {
      create              = false
      subnet_cidr         = "10.20.16.0/20"
      existing_network    = "prod-vpc"
      existing_subnetwork = "db-subnet"
    }
  }

  expect_failures = [var.network]
}

run "rejects_public_client_range" {
  command = plan

  variables {
    client_cidrs = ["0.0.0.0/0"]
  }

  expect_failures = [var.client_cidrs]
}

run "arm_single_node_with_client_access" {
  command = plan

  variables {
    nodes = {
      "node-1" = { zone = "asia-south1-a", ordinal = 1, roles = ["master", "data", "ingest"] }
    }
    machine_type         = "t2a-standard-4"
    architecture         = "arm64"
    seed_nodes           = ["node-1"]
    initial_master_nodes = ["node-1"]
    zone_awareness       = false
    client_cidrs         = ["10.20.0.0/16"]
  }

  assert {
    condition     = module.compute.image == "debian-cloud/debian-12-arm64"
    error_message = "arm64 machine types need the arm64 image."
  }

  assert {
    condition     = length(module.compute.nodes) == 1
    error_message = "Expected a single node."
  }

  assert {
    condition     = contains(module.network.ingress_source_ranges, "10.20.0.0/16")
    error_message = "Client CIDRs must be allowed in when requested."
  }

  assert {
    condition     = output.engine_package.architecture == "arm64" && output.engine_package.sha256 == "155d265ec0b855998288b646069d451b822235caf38e5662e6a974450abef9eb"
    error_message = "arm64 nodes must verify the arm64 package checksum."
  }
}

run "rejects_minor_line" {
  command = plan

  variables {
    es_version = "9.5"
  }

  expect_failures = [var.es_version]
}

run "rejects_latest" {
  command = plan

  variables {
    es_version = "latest"
  }

  expect_failures = [var.es_version]
}

run "rejects_unverifiable_package" {
  command = plan

  variables {
    es_package = {
      apt_repository          = "http://artifacts.elastic.co/packages/9.x/apt"
      signing_key_url         = "https://artifacts.elastic.co/GPG-KEY-elasticsearch"
      signing_key_fingerprint = "D88E42B4"
      sha256                  = { amd64 = "abc" }
    }
  }

  expect_failures = [var.es_package]
}

run "dedicated_ha_layout" {
  command = plan

  variables {
    layout = "dedicated"
    nodes = {
      "master-1" = { zone = "asia-south1-a", ordinal = 1, roles = ["master"], machine_type = "e2-standard-4", data_disk_size_gb = 20 }
      "master-2" = { zone = "asia-south1-b", ordinal = 2, roles = ["master"], machine_type = "e2-standard-4", data_disk_size_gb = 20 }
      "master-3" = { zone = "asia-south1-c", ordinal = 3, roles = ["master"], machine_type = "e2-standard-4", data_disk_size_gb = 20 }
      "data-1"   = { zone = "asia-south1-a", ordinal = 4, roles = ["data", "ingest"], machine_type = "e2-standard-8", data_disk_size_gb = 500 }
      "data-2"   = { zone = "asia-south1-b", ordinal = 5, roles = ["data", "ingest"], machine_type = "e2-standard-8", data_disk_size_gb = 500 }
      "data-3"   = { zone = "asia-south1-c", ordinal = 6, roles = ["data", "ingest"], machine_type = "e2-standard-8", data_disk_size_gb = 500, config_generation = 2 }
      "coord-1"  = { zone = "asia-south1-a", ordinal = 7, roles = [], machine_type = "e2-standard-4", data_disk_size_gb = 20 }
      "coord-2"  = { zone = "asia-south1-b", ordinal = 8, roles = [], machine_type = "e2-standard-4", data_disk_size_gb = 20 }
    }
    seed_nodes             = ["master-1", "master-2", "master-3"]
    initial_master_nodes   = ["master-1", "master-2", "master-3"]
    forced_awareness_zones = ["asia-south1-a", "asia-south1-b", "asia-south1-c"]
    load_balancer_nodes    = ["coord-1", "coord-2"]
    cluster_settings       = { "search.max_buckets" = "20000" }
    cluster_settings_hash  = "971a936de98cf3f8"
    node_settings          = { "thread_pool.write.queue_size" = "20000" }
    heap_percent           = 40
  }

  assert {
    condition     = length(module.compute.nodes) == 8
    error_message = "Expected 3 masters, 3 data and 2 coordinating VMs."
  }

  assert {
    condition     = module.compute.has_public_ip == false
    error_message = "Database VMs must not get public IP addresses."
  }

  assert {
    condition     = google_compute_forwarding_rule.endpoint[0].load_balancing_scheme == "INTERNAL" && google_compute_forwarding_rule.endpoint[0].ports == toset(["9200"])
    error_message = "The endpoint must be an internal load balancer on 9200 only."
  }

  assert {
    condition     = google_compute_address.endpoint[0].address_type == "INTERNAL"
    error_message = "The endpoint address must be private."
  }

  assert {
    condition     = length(google_compute_instance_group.endpoint) == 2
    error_message = "One instance group per zone of the coordinating nodes."
  }

  assert {
    condition     = google_compute_firewall.endpoint_health_checks[0].source_ranges == toset(["35.191.0.0/16", "130.211.0.0/22"]) && google_compute_firewall.endpoint_health_checks[0].target_tags == toset(["${local.lb_tag}"])
    error_message = "Only Google's health checkers may probe, and only the load-balanced nodes."
  }

  assert {
    condition     = module.storage.disks["data-1"].name == "production-search-3f9a-data-1-data"
    error_message = "Every node keeps its own data disk."
  }
}

run "dedicated_ha_node_details" {
  command = plan

  variables {
    layout = "dedicated"
    nodes = {
      "master-1" = { zone = "asia-south1-a", ordinal = 1, roles = ["master"], machine_type = "e2-standard-4", data_disk_size_gb = 20 }
      "data-1"   = { zone = "asia-south1-a", ordinal = 2, roles = ["data"], machine_type = "e2-standard-8", data_disk_size_gb = 500, config_generation = 3 }
      "coord-1"  = { zone = "asia-south1-a", ordinal = 3, roles = [], machine_type = "e2-standard-4", data_disk_size_gb = 20 }
    }
    seed_nodes           = ["master-1"]
    initial_master_nodes = ["master-1"]
    load_balancer_nodes  = ["coord-1"]
    node_settings        = { "http.max_content_length" = "200mb" }
    heap_percent         = 40
  }

  assert {
    condition     = module.compute.instances["data-1"].machine_type == "e2-standard-8" && module.compute.instances["master-1"].machine_type == "e2-standard-4"
    error_message = "Every group gets its own machine type."
  }

  assert {
    condition     = module.storage.sizes["data-1"] == 500 && module.storage.sizes["coord-1"] == 20
    error_message = "Every group gets its own disk size."
  }

  assert {
    condition     = module.compute.instances["data-1"].metadata["byoc-config-generation"] == "3" && module.compute.instances["coord-1"].metadata["byoc-node-roles"] == "none"
    error_message = "Per-node config generation and empty roles for coordinating nodes."
  }

  assert {
    condition     = jsondecode(module.compute.instances["data-1"].metadata["byoc-es-node-settings"])["http.max_content_length"] == "200mb" && module.compute.instances["data-1"].metadata["byoc-es-heap-percent"] == "40"
    error_message = "Static settings and heap share travel in metadata."
  }

  assert {
    condition     = contains(module.compute.instances["coord-1"].tags, "${local.lb_tag}") && !contains(module.compute.instances["data-1"].tags, "${local.lb_tag}")
    error_message = "Only load-balanced nodes carry the health-check tag."
  }
}

run "rejects_platform_managed_settings" {
  command = plan

  variables {
    node_settings = { "xpack.security.enabled" = "false" }
  }

  expect_failures = [var.node_settings]
}

run "rejects_unsafe_setting_values" {
  command = plan

  variables {
    node_settings = { "http.max_content_length" = "1mb\nnetwork.host: 0.0.0.0" }
  }

  expect_failures = [var.node_settings]
}

run "custom_elasticsearch_yml_settings" {
  command = plan

  variables {
    node_settings = {
      "indices.query.bool.max_clause_count" = "8192"
      "xpack.ml.enabled"                    = "false"
      "reindex.remote.whitelist"            = "10.0.0.5:9200,10.0.0.6:9200"
      "http.max_content_length"             = "200mb"
    }
  }

  assert {
    condition     = jsondecode(module.compute.instances["node-1"].metadata["byoc-es-node-settings"])["xpack.ml.enabled"] == "false"
    error_message = "Custom elasticsearch.yml settings travel in metadata."
  }
}

run "rejects_network_settings" {
  command = plan

  variables {
    node_settings = { "network.bind_host" = "0.0.0.0" }
  }

  expect_failures = [var.node_settings]
}

run "rejects_discovery_and_paths" {
  command = plan

  variables {
    node_settings = { "path.repo" = "/mnt", "discovery.type" = "single-node" }
  }

  expect_failures = [var.node_settings]
}

run "rejects_invalid_setting_names" {
  command = plan

  variables {
    node_settings = { "Bad Key" = "1" }
  }

  expect_failures = [var.node_settings]
}
