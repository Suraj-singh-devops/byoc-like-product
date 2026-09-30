resource "google_sql_database_instance" "platform" {
  name                = "${var.name}-platform"
  region              = var.region
  database_version    = "POSTGRES_17"
  encryption_key_name = google_kms_crypto_key.this["cloudsql"].id
  deletion_protection = var.deletion_protection

  settings {
    tier                        = var.sql_tier
    edition                     = "ENTERPRISE"
    availability_type           = var.sql_high_availability ? "REGIONAL" : "ZONAL"
    deletion_protection_enabled = var.deletion_protection
    user_labels                 = local.labels

    # Private IP only, TLS only.
    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.platform.id
      ssl_mode        = "ENCRYPTED_ONLY"
    }

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
    }

    # Workloads log in as their own Google service accounts; there are no database passwords.
    database_flags {
      name  = "cloudsql.iam_authentication"
      value = "on"
    }
  }

  depends_on = [google_service_networking_connection.sql, google_kms_crypto_key_iam_member.service_agents]
}

resource "google_sql_database" "byoc" {
  name     = "byoc"
  instance = google_sql_database_instance.platform.name
}

resource "google_sql_user" "workload" {
  for_each = local.database_users
  instance = google_sql_database_instance.platform.name
  # Cloud SQL names IAM service-account users by the email without ".gserviceaccount.com".
  name = trimsuffix(google_service_account.workload[each.key].email, ".gserviceaccount.com")
  type = "CLOUD_IAM_SERVICE_ACCOUNT"
}
