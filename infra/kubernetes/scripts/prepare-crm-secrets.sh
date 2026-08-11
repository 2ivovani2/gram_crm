#!/usr/bin/env bash
set -euo pipefail
[[ -n "${KUBECONFIG:-}" ]] || { echo "KUBECONFIG is required." >&2; exit 1; }
prod_env_file="${1:?Usage: prepare-crm-secrets.sh /path/to/prod.env}"
[[ -f "${prod_env_file}" ]] || { echo "Production env file not found." >&2; exit 1; }

set -a
# shellcheck disable=SC1090
source "${prod_env_file}"
set +a

if ! kubectl -n gramly-crm get secret gramly-crm-postgres-app >/dev/null 2>&1; then
  db_password="$(openssl rand -hex 32)"
  kubectl -n gramly-crm create secret generic gramly-crm-postgres-app \
    --from-literal=username=gramly --from-literal=password="${db_password}" >/dev/null
else
  db_password="$(kubectl -n gramly-crm get secret gramly-crm-postgres-app -o jsonpath='{.data.password}' | base64 --decode)"
fi

if ! kubectl -n gramly-crm get secret gramly-crm-valkey-config >/dev/null 2>&1; then
  redis_password="$(openssl rand -hex 32)"
  valkey_config="$(printf 'bind 0.0.0.0\nprotected-mode yes\nport 6379\ndir /data\nappendonly yes\nappendfsync everysec\nrequirepass %s\n' "${redis_password}")"
  kubectl -n gramly-crm create secret generic gramly-crm-valkey-config \
    --from-literal=valkey.conf="${valkey_config}" >/dev/null
  unset valkey_config
else
  redis_password="$(kubectl -n gramly-crm get secret gramly-crm-valkey-config -o jsonpath='{.data.valkey\.conf}' | base64 --decode | sed -n 's/^requirepass //p')"
fi

kubectl -n gramly-crm create secret generic gramly-crm-minio \
  --from-literal=MINIO_ROOT_USER="${AWS_ACCESS_KEY_ID}" \
  --from-literal=MINIO_ROOT_PASSWORD="${AWS_SECRET_ACCESS_KEY}" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null

runtime_keys=(
  SECRET_KEY TEST_BOT_TOKEN PROD_BOT_TOKEN TELEGRAM_BOT_USERNAME
  TELEGRAM_WEBHOOK_SECRET TELEGRAM_WEBHOOK_URL SUBSCRIPTION_CHANNEL_ID
  SUBSCRIPTION_CHANNEL_URL AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
  AWS_STORAGE_BUCKET_NAME AWS_S3_REGION_NAME MEDIA_QUERYSTRING_EXPIRE
  WELCOME_BOT_TOKEN WELCOME_BOT_USERNAME
  WELCOME_WEBHOOK_SECRET WELCOME_WEBHOOK_URL WELCOME_CLIENT_WEBHOOK_BASE_URL
  WELCOME_MEDIA_MAX_BYTES
)
runtime_args=()
for key in "${runtime_keys[@]}"; do
  runtime_args+=("--from-literal=${key}=${!key-}")
done
runtime_args+=(
  "--from-literal=DEBUG=False"
  "--from-literal=BOT_ENV=prod"
  "--from-literal=DOMAIN=gramly.tech"
  "--from-literal=ALLOWED_HOSTS=gramly.tech,www.gramly.tech,hello.gramly.tech,crm.gramly.tech"
  "--from-literal=DATABASE_URL=postgres://gramly:${db_password}@gramly-crm-postgres-rw.gramly-crm.svc.cluster.local:5432/gramly"
  "--from-literal=REDIS_URL=redis://:${redis_password}@gramly-crm-valkey.gramly-crm.svc.cluster.local:6379/0"
  "--from-literal=AWS_S3_ENDPOINT_URL=https://media.gramly.tech"
  "--from-literal=AWS_S3_ADDRESSING_STYLE=path"
  "--from-literal=MEDIA_QUERYSTRING_AUTH=true"
  "--from-literal=MEDIA_S3_PUBLIC_URL="
)
kubectl -n gramly-crm create secret generic gramly-crm-runtime \
  "${runtime_args[@]}" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
unset db_password redis_password AWS_SECRET_ACCESS_KEY PROD_BOT_TOKEN TEST_BOT_TOKEN
echo "CRM runtime, database, Valkey, and MinIO secrets are ready."
