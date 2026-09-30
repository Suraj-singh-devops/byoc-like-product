output "cluster" {
  value = {
    name     = google_container_cluster.platform.name
    location = google_container_cluster.platform.location
  }
}

output "image_repository" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "console_address" {
  value = google_compute_global_address.console.address
}

output "dns_records" {
  description = "Records to create for the console domain: A for the address, CNAME for certificate validation."
  value = var.domain == "" ? [] : [
    { name = var.domain, type = "A", data = google_compute_global_address.console.address },
    {
      name = google_certificate_manager_dns_authorization.console[0].dns_resource_record[0].name
      type = google_certificate_manager_dns_authorization.console[0].dns_resource_record[0].type
      data = google_certificate_manager_dns_authorization.console[0].dns_resource_record[0].data
    },
  ]
}

output "helm_values" {
  description = "Values for deploy/helm/database-platform generated from this platform."
  value = yamlencode({
    gcp = {
      projectId       = var.project_id
      region          = var.region
      staticIpName    = google_compute_global_address.console.name
      certificateMap  = var.domain == "" ? "" : google_certificate_manager_certificate_map.console[0].name
      stateBucket     = google_storage_bucket.state.name
      serviceAccounts = { for k, v in local.workloads : v => google_service_account.workload[k].email }
    }
    database = {
      mode                   = "cloudsql"
      instanceConnectionName = google_sql_database_instance.platform.connection_name
      name                   = google_sql_database.byoc.name
      users                  = { for k in local.database_users : local.workloads[k] => google_sql_user.workload[k].name }
    }
    secrets = {
      source = "secretManager"
      names  = { for k, v in google_secret_manager_secret.platform : k => v.secret_id }
    }
    ingress = { host = var.domain }
  })
}
