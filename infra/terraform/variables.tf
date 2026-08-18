variable "vultr_api_key" {
  description = "Vultr API key supplied only as TF_VAR_vultr_api_key by protected CI."
  type        = string
  sensitive   = true
}

variable "region" {
  description = "Vultr region shared with the VKE cluster."
  type        = string
  default     = "ewr"
}

variable "vpc_id" {
  description = "VPC attached to VKE and Managed Valkey."
  type        = string
}

variable "valkey_plan" {
  description = "HA Managed Database plan with at least one failover replica."
  type        = string
}

variable "valkey_version" {
  description = "Managed Valkey engine version available in the selected region."
  type        = string
  default     = "8"
}

variable "trusted_sources" {
  description = "Additional trusted CIDRs; keep empty when VPC-only access is sufficient."
  type        = list(string)
  default     = []
}

variable "object_storage_cluster_id" {
  description = "Vultr Object Storage cluster ID in or near the VKE region."
  type        = number
}

variable "object_storage_tier_id" {
  description = "Vultr Object Storage tier ID."
  type        = number
}
