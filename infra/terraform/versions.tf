terraform {
  required_version = ">= 1.9.0, < 2.0.0"

  # Connection details are supplied from a protected backend.hcl at init.
  # This prevents accidental local state and keeps credentials out of Git.
  backend "s3" {}

  required_providers {
    vultr = {
      source  = "vultr/vultr"
      version = "2.32.0"
    }
  }
}

provider "vultr" {
  api_key     = var.vultr_api_key
  rate_limit  = 100
  retry_limit = 5
}
