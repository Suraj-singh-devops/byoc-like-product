resource "google_artifact_registry_repository" "images" {
  repository_id = var.name
  location      = var.region
  format        = "DOCKER"
  description   = "BYOC platform images (backend, frontend)"
  labels        = local.labels
  depends_on    = [google_project_service.this]

  docker_config {
    immutable_tags = true
  }
}

resource "google_artifact_registry_repository_iam_member" "nodes" {
  repository = google_artifact_registry_repository.images.name
  location   = var.region
  role       = "roles/artifactregistry.reader"
  member     = google_service_account.nodes.member
}
