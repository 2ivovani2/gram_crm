output "valkey_connection" {
  description = "Connection metadata used to build the Kubernetes Secret outside Terraform state."
  sensitive   = true
  value = {
    host           = vultr_database.gramly_valkey.host
    port           = vultr_database.gramly_valkey.port
    user           = vultr_database.gramly_valkey.user
    password       = vultr_database.gramly_valkey.password
    ca_certificate = vultr_database.gramly_valkey.ca_certificate
  }
}

output "object_storage_connection" {
  description = "S3 endpoint and credentials; inject through the CI secret store."
  sensitive   = true
  value = {
    endpoint   = "https://${vultr_object_storage.gramly.s3_hostname}"
    access_key = vultr_object_storage.gramly.s3_access_key
    secret_key = vultr_object_storage.gramly.s3_secret_key
  }
}

output "bucket_names" {
  value = {
    crm_media     = vultr_object_storage_bucket.crm_media.name
    welcome_media = vultr_object_storage_bucket.welcome_media.name
    backups       = vultr_object_storage_bucket.backups.name
  }
}
