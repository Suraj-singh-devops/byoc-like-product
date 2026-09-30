variable "project_id" {
  description = "The platform's own project (not a customer project)."
  type        = string
}

variable "region" {
  type    = string
  default = "asia-south1"
}

variable "name" {
  description = "Prefix of every resource name."
  type        = string
  default     = "byoc"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,14}$", var.name))
    error_message = "name must be 2-15 lowercase letters, digits or hyphens."
  }
}

variable "namespace" {
  description = "Kubernetes namespace of the Helm release (Workload Identity bindings name it)."
  type        = string
  default     = "database-platform"
}

variable "network_cidrs" {
  description = "Node, Pod and Service ranges of the platform VPC."
  type = object({
    nodes    = string
    pods     = string
    services = string
    sql      = string # reserved range for Private Service Access (Cloud SQL private IP)
  })
  default = {
    nodes    = "10.100.0.0/20"
    pods     = "10.104.0.0/14"
    services = "10.108.0.0/20"
    sql      = "10.109.0.0/20"
  }
}

variable "master_authorized_cidrs" {
  description = "Ranges allowed to reach the GKE control plane endpoint (for example an office or CI egress IP)."
  type        = list(string)
  default     = []

  validation {
    condition     = !contains(var.master_authorized_cidrs, "0.0.0.0/0")
    error_message = "The GKE control plane must not be open to 0.0.0.0/0."
  }
}

variable "domain" {
  description = "Hostname of the console (e.g. byoc.example.com). Empty: no managed certificate yet."
  type        = string
  default     = ""
}

variable "sql_tier" {
  type    = string
  default = "db-custom-2-7680"
}

variable "sql_high_availability" {
  description = "Regional (HA) Cloud SQL instance."
  type        = bool
  default     = false
}

variable "deletion_protection" {
  description = "Protects the GKE cluster and the Cloud SQL instance from terraform destroy."
  type        = bool
  default     = true
}
