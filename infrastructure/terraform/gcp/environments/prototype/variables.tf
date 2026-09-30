variable "project_id" {
  type = string
}

variable "region" {
  type    = string
  default = "asia-south1"
}

variable "zone" {
  type    = string
  default = "asia-south1-a"
}

variable "ha_zones" {
  description = "Zones to spread nodes over when high_availability is true (first = primary zone)."
  type        = list(string)
  default     = ["asia-south1-a", "asia-south1-b", "asia-south1-c"]
}

variable "cluster_id" {
  description = "Any stable unique ID; the control plane uses its cluster UUID."
  type        = string
}

variable "cluster_name" {
  type    = string
  default = "production-search"
}

variable "name_prefix" {
  type    = string
  default = "production-search"
}

variable "es_version" {
  description = "Exact version listed in backend/app/providers/database/elasticsearch/versions.yaml."
  type        = string
  default     = "9.5.4"
}

variable "machine_type" {
  type    = string
  default = "e2-standard-8"
}

variable "node_count" {
  type    = number
  default = 3
}

variable "storage_gb" {
  type    = number
  default = 500
}

variable "storage_type" {
  type    = string
  default = "pd-balanced"
}

variable "high_availability" {
  type    = bool
  default = true
}

variable "agent_binary_path" {
  description = "Path to a linux build of the agent (make agent); empty to skip it."
  type        = string
  default     = ""
}

variable "agent_version" {
  type    = string
  default = "0.1.0"
}

variable "control_plane_url" {
  type    = string
  default = ""
}
