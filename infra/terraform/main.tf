resource "vultr_database" "gramly_valkey" {
  database_engine         = "valkey"
  database_engine_version = var.valkey_version
  region                  = var.region
  plan                    = var.valkey_plan
  label                   = "gramly-production-valkey"
  tag                     = "gramly"
  vpc_id                  = var.vpc_id
  trusted_ips             = var.trusted_sources
  eviction_policy         = "noeviction"
  maintenance_dow         = "sunday"
  maintenance_time        = "03:00"

  lifecycle {
    prevent_destroy = true
  }
}

resource "vultr_object_storage" "gramly" {
  cluster_id = var.object_storage_cluster_id
  tier_id    = var.object_storage_tier_id
  label      = "gramly-production-storage"

  lifecycle {
    prevent_destroy = true
  }
}

resource "vultr_object_storage_bucket" "crm_media" {
  object_storage_id = vultr_object_storage.gramly.id
  name              = "gramly-crm-media"
  enable_versioning = true
  enable_lock       = false

  lifecycle {
    prevent_destroy = true
  }
}

resource "vultr_object_storage_bucket" "welcome_media" {
  object_storage_id = vultr_object_storage.gramly.id
  name              = "gramly-welcome-media"
  enable_versioning = true
  enable_lock       = false

  lifecycle {
    prevent_destroy = true
  }
}

resource "vultr_object_storage_bucket" "backups" {
  object_storage_id = vultr_object_storage.gramly.id
  name              = "gramly-backups"
  enable_versioning = true
  enable_lock       = true

  lifecycle {
    prevent_destroy = true
  }
}
