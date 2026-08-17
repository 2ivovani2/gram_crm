# Gramly external data plane

This stack describes paid Vultr resources and must never be applied from a
developer laptop with local state.

1. Configure an encrypted remote Terraform backend and state locking.
2. Export `TF_VAR_vultr_api_key` through the protected CI environment.
3. Copy `terraform.tfvars.example` outside Git and fill IDs from the Vultr API.
4. Run `terraform init`, `terraform plan -out=tfplan`, and review the exact
   monthly resources before a separately approved `terraform apply tfplan`.
5. Inject sensitive outputs into Kubernetes Secrets; never commit outputs.

All stateful resources use `prevent_destroy`. Removing them requires an
explicit lifecycle change in a separate reviewed MR. Valkey uses `noeviction`
because accepted Telegram events live in PostgreSQL and rate-limit state must
fail closed rather than silently evict keys.
