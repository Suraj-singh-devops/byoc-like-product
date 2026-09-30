# Plan-only tests with mocked providers (no credentials needed): terraform test

mock_provider "google" {
  mock_data "google_project" {
    defaults = { number = "123456789012" }
  }
}
mock_provider "google-beta" {}

variables {
  project_id              = "byoc-platform-test"
  master_authorized_cidrs = ["203.0.113.0/24"]
}

run "private_by_default" {
  command = plan

  assert {
    condition     = google_sql_database_instance.platform.settings[0].ip_configuration[0].ipv4_enabled == false
    error_message = "Cloud SQL must have no public IP."
  }

  assert {
    condition     = google_sql_database_instance.platform.settings[0].ip_configuration[0].ssl_mode == "ENCRYPTED_ONLY"
    error_message = "Cloud SQL must require TLS."
  }

  assert {
    condition     = contains([for f in google_sql_database_instance.platform.settings[0].database_flags : "${f.name}=${f.value}"], "cloudsql.iam_authentication=on")
    error_message = "Workloads log in with IAM, so IAM authentication must be on."
  }

  assert {
    condition     = google_container_cluster.platform.enable_autopilot && google_container_cluster.platform.private_cluster_config[0].enable_private_nodes
    error_message = "GKE must be Autopilot with private nodes."
  }

  assert {
    condition     = google_container_cluster.platform.database_encryption[0].state == "ENCRYPTED" && google_container_cluster.platform.secret_manager_config[0].enabled
    error_message = "Kubernetes secrets must be KMS-encrypted and Secret Manager mounted through the add-on."
  }

  assert {
    condition     = google_storage_bucket.state.public_access_prevention == "enforced" && google_storage_bucket.state.versioning[0].enabled
    error_message = "The state bucket must be private and versioned."
  }
}

run "one_identity_per_workload" {
  command = plan

  assert {
    condition     = length(google_service_account.workload) == 6
    error_message = "api, cluster-manager, terraform-runner, monitoring-worker, migrate and redis each get their own identity."
  }

  assert {
    condition     = google_service_account_iam_member.workload_identity["runner"].member == "serviceAccount:byoc-platform-test.svc.id.goog[database-platform/terraform-runner]"
    error_message = "Only the terraform-runner Kubernetes ServiceAccount may act as the runner's identity."
  }

  assert {
    condition     = length(google_sql_user.workload) == 5 && alltrue([for u in google_sql_user.workload : u.type == "CLOUD_IAM_SERVICE_ACCOUNT"])
    error_message = "Database users are IAM users, one per workload; there are no passwords."
  }

  assert {
    condition     = !contains(keys(google_secret_manager_secret_iam_member.readers), "secret-key/redis") && contains(keys(google_secret_manager_secret_iam_member.readers), "redis-password/redis")
    error_message = "Each workload reads only the secrets it needs."
  }

  assert {
    condition     = google_storage_bucket_iam_member.runner.role == "roles/storage.objectAdmin" && google_storage_bucket_iam_member.runner.bucket == "byoc-platform-test-byoc-tfstate"
    error_message = "The terraform-runner is granted the state bucket."
  }
}

run "managed_certificate_when_a_domain_is_set" {
  command = plan

  variables {
    domain = "byoc.example.com"
  }

  assert {
    condition     = length(google_certificate_manager_certificate.console) == 1 && google_certificate_manager_certificate_map_entry.console[0].hostname == "byoc.example.com"
    error_message = "A domain gets a Google-managed certificate."
  }
}

run "control_plane_never_open_to_the_internet" {
  command = plan

  variables {
    master_authorized_cidrs = ["0.0.0.0/0"]
  }

  expect_failures = [var.master_authorized_cidrs]
}
