# Gramly external data plane

This stack describes paid Vultr resources and must never be applied with local
state. The empty S3 backend block in `versions.tf` makes remote state an
explicit prerequisite for every init.

1. Provision one Object Storage subscription and a private
   `gramly-terraform-state` bucket as the bootstrap resource.
2. Copy `backend.hcl.example` outside Git, replace its endpoint, and export the
   Object Storage credentials as `AWS_ACCESS_KEY_ID` and
   `AWS_SECRET_ACCESS_KEY` through the protected CI environment.
3. Run `terraform init -backend-config=/secure/backend.hcl`; the backend uses
   S3 lockfiles and must never be initialized without this configuration.
   Vultr encrypts Object Storage at rest at the platform layer; do not enable
   the AWS SSE-S3 `encrypt` backend option because Vultr rejects that header.
4. Import the bootstrap Object Storage subscription into
   `vultr_object_storage.gramly` before the first plan.
5. Export `TF_VAR_vultr_api_key` through the protected CI environment.
6. Copy `terraform.tfvars.example` outside Git and fill IDs from the Vultr API.
7. Run `terraform plan -out=tfplan`, and review the exact monthly resources
   before a separately approved `terraform apply tfplan`.
8. Inject sensitive outputs into Kubernetes Secrets; never commit outputs.

All stateful resources use `prevent_destroy`. Removing them requires an
explicit lifecycle change in a separate reviewed MR. Valkey uses `noeviction`
because accepted Telegram events live in PostgreSQL and rate-limit state must
fail closed rather than silently evict keys.

The production VKE cluster is in `ewr`; do not reuse the historical `lhr`
default. Bootstrap values must be verified against the Vultr API before every
apply.
