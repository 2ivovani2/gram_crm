#!/usr/bin/env bash
set -euo pipefail

[[ -n "${KUBECONFIG:-}" ]] || { echo "KUBECONFIG is required." >&2; exit 1; }
welcome_env_file="${1:?Usage: prepare-welcome-secrets.sh /path/to/welcome.env staging|production}"
environment="${2:?Usage: prepare-welcome-secrets.sh /path/to/welcome.env staging|production}"
[[ -f "${welcome_env_file}" ]] || { echo "Welcome env file not found." >&2; exit 1; }

case "${environment}" in
  staging)
    target_namespace=gramly-staging
    database_name=gramly_welcome_staging
    database_user=gramly_welcome_staging
    role_secret=gramly-welcome-staging-postgres-role
    crypto_pay_default_base_url=https://testnet-pay.crypt.bot
    ;;
  production)
    target_namespace=gramly-welcome
    database_name=gramly_welcome
    database_user=gramly_welcome
    role_secret=gramly-welcome-postgres-role
    crypto_pay_default_base_url=https://pay.crypt.bot
    ;;
  *) echo "Environment must be staging or production." >&2; exit 1 ;;
esac

set -a
# shellcheck disable=SC1090
source "${welcome_env_file}"
set +a

required=(
  WELCOME_VALKEY_URL WELCOME_S3_ENDPOINT_URL WELCOME_S3_REGION_NAME
  WELCOME_S3_BUCKET_NAME WELCOME_S3_ACCESS_KEY_ID WELCOME_S3_SECRET_ACCESS_KEY
)
for key in "${required[@]}"; do
  [[ -n "${!key-}" ]] || { echo "${key} is required." >&2; exit 1; }
done

kubectl get namespace "${target_namespace}" >/dev/null 2>&1 \
  || kubectl create namespace "${target_namespace}" >/dev/null
kubectl label namespace "${target_namespace}" \
  gramly.tech/contour=public \
  gramly.tech/environment="${environment}" \
  --overwrite >/dev/null
if [[ "${environment}" == staging ]]; then
  kubectl label namespace "${target_namespace}" \
    gramly.tech/access-plane=business --overwrite >/dev/null
fi

if kubectl -n gramly-crm get secret "${role_secret}" >/dev/null 2>&1; then
  database_password="$(kubectl -n gramly-crm get secret "${role_secret}" \
    -o jsonpath='{.data.password}' | base64 --decode)"
else
  database_password="$(openssl rand -hex 32)"
fi
kubectl -n gramly-crm create secret generic "${role_secret}" \
  --type=kubernetes.io/basic-auth \
  --from-literal=username="${database_user}" \
  --from-literal=password="${database_password}" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl -n gramly-crm label secret "${role_secret}" cnpg.io/reload=true --overwrite >/dev/null

if kubectl -n "${target_namespace}" get secret gramly-welcome-runtime >/dev/null 2>&1; then
  token_keys="$(kubectl -n "${target_namespace}" get secret gramly-welcome-runtime \
    -o jsonpath='{.data.WELCOME_TOKEN_ENCRYPTION_KEYS}' | base64 --decode)"
  interface_secret="$(kubectl -n "${target_namespace}" get secret gramly-welcome-runtime \
    -o jsonpath='{.data.WELCOME_INTERFACE_WEBHOOK_SECRET}' | base64 --decode)"
  crypto_webhook_secret="$(kubectl -n "${target_namespace}" get secret gramly-welcome-runtime \
    -o jsonpath='{.data.WELCOME_CRYPTO_PAY_WEBHOOK_SECRET}' | base64 --decode)"
  existing_crypto_token="$(kubectl -n "${target_namespace}" get secret gramly-welcome-runtime \
    -o jsonpath='{.data.WELCOME_CRYPTO_PAY_API_TOKEN}' | base64 --decode)"
else
  fernet_key="$(openssl rand -base64 32 | tr '/+' '_-')"
  token_keys="{\"1\":\"${fernet_key}\"}"
  interface_secret="$(openssl rand -hex 32)"
  crypto_webhook_secret="$(openssl rand -hex 32)"
  existing_crypto_token=""
  unset fernet_key
fi
[[ -n "${crypto_webhook_secret}" ]] || crypto_webhook_secret="$(openssl rand -hex 32)"
crypto_pay_token="${WELCOME_CRYPTO_PAY_API_TOKEN:-${existing_crypto_token}}"

database_host=gramly-crm-postgres-rw.gramly-crm.svc.cluster.local
runtime_args=(
  "--from-literal=WELCOME_DATABASE_URL=postgresql+asyncpg://${database_user}:${database_password}@${database_host}:5432/${database_name}"
  "--from-literal=WELCOME_KEDA_DATABASE_URL=postgresql://${database_user}:${database_password}@${database_host}:5432/${database_name}?sslmode=require"
  "--from-literal=WELCOME_INTERFACE_BOT_TOKEN=${WELCOME_INTERFACE_BOT_TOKEN:-}"
  "--from-literal=WELCOME_INTERFACE_BOT_USERNAME=${WELCOME_INTERFACE_BOT_USERNAME:-}"
  "--from-literal=WELCOME_INTERFACE_WEBHOOK_SECRET=${interface_secret}"
  "--from-literal=WELCOME_MINI_APP_URL=${WELCOME_MINI_APP_URL:-https://hello.gramly.tech/app/}"
  "--from-literal=WELCOME_CRYPTO_PAY_API_TOKEN=${crypto_pay_token}"
  "--from-literal=WELCOME_CRYPTO_PAY_API_BASE_URL=${WELCOME_CRYPTO_PAY_API_BASE_URL:-${crypto_pay_default_base_url}}"
  "--from-literal=WELCOME_CRYPTO_PAY_WEBHOOK_SECRET=${crypto_webhook_secret}"
  "--from-literal=WELCOME_PUBLIC_WEBHOOK_BASE_URL=${WELCOME_PUBLIC_WEBHOOK_BASE_URL:-https://gramly.tech/welcome/client}"
  "--from-literal=WELCOME_TOKEN_ENCRYPTION_KEYS=${token_keys}"
  "--from-literal=WELCOME_VALKEY_URL=${WELCOME_VALKEY_URL}"
  "--from-literal=WELCOME_S3_ENDPOINT_URL=${WELCOME_S3_ENDPOINT_URL}"
  "--from-literal=WELCOME_S3_REGION_NAME=${WELCOME_S3_REGION_NAME}"
  "--from-literal=WELCOME_S3_BUCKET_NAME=${WELCOME_S3_BUCKET_NAME}"
  "--from-literal=WELCOME_S3_ACCESS_KEY_ID=${WELCOME_S3_ACCESS_KEY_ID}"
  "--from-literal=WELCOME_S3_SECRET_ACCESS_KEY=${WELCOME_S3_SECRET_ACCESS_KEY}"
  "--from-literal=WELCOME_S3_ADDRESSING_STYLE=${WELCOME_S3_ADDRESSING_STYLE:-path}"
  "--from-literal=WELCOME_TELEGRAM_API_BASE_URL=https://api.telegram.org"
)
kubectl -n "${target_namespace}" create secret generic gramly-welcome-runtime \
  "${runtime_args[@]}" --dry-run=client -o yaml | kubectl apply -f - >/dev/null

kubectl -n gramly-crm get secret gramly-crm-registry -o json \
  | jq --arg namespace "${target_namespace}" \
      '.metadata = {name:"gramly-welcome-registry", namespace:$namespace} |
       del(.metadata.creationTimestamp, .metadata.resourceVersion, .metadata.uid, .metadata.managedFields)' \
  | kubectl apply -f - >/dev/null

unset database_password token_keys interface_secret crypto_webhook_secret crypto_pay_token \
  existing_crypto_token WELCOME_S3_SECRET_ACCESS_KEY
echo "Welcome ${environment} database role, runtime, and registry secrets are ready."
