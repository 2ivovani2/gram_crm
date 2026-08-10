#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${KUBECONFIG:-}" ]]; then
  echo "KUBECONFIG is not set; refusing to use an implicit cluster context." >&2
  exit 1
fi

for secret_ref in \
  identity/netbird-postgres-app \
  vpn/netbird-config; do
  namespace="${secret_ref%%/*}"
  secret_name="${secret_ref##*/}"
  if kubectl -n "$namespace" get secret "$secret_name" >/dev/null 2>&1; then
    echo "Secret $secret_ref already exists; refusing to rotate VPN credentials implicitly." >&2
    exit 1
  fi
done

db_password="$(openssl rand -hex 32)"
relay_secret="$(openssl rand -base64 32 | tr -d '=')"
encryption_key="$(openssl rand -base64 32)"
owner_password="$(openssl rand -hex 20)"

kubectl -n identity create secret generic netbird-postgres-app \
  --type=kubernetes.io/basic-auth \
  --from-literal=username=netbird \
  --from-literal=password="$db_password" \
  --dry-run=client -o yaml \
  | kubectl label --local -f - cnpg.io/reload=true -o yaml \
  | kubectl apply -f - >/dev/null

config="$(printf '%s\n' \
  'server:' \
  '  listenAddress: ":80"' \
  '  exposedAddress: "https://vpn.gramly.tech:443"' \
  '  stunPorts:' \
  '    - 3478' \
  '  metricsPort: 9090' \
  '  healthcheckAddress: ":9000"' \
  '  logLevel: "info"' \
  '  logFile: "console"' \
  "  authSecret: \"$relay_secret\"" \
  '  dataDir: "/var/lib/netbird"' \
  '  auth:' \
  '    issuer: "https://vpn.gramly.tech/oauth2"' \
  '    signKeyRefreshEnabled: true' \
  '    dashboardRedirectURIs:' \
  '      - "https://vpn.gramly.tech/nb-auth"' \
  '      - "https://vpn.gramly.tech/nb-silent-auth"' \
  '    cliRedirectURIs:' \
  '      - "http://localhost:53000/"' \
  '    owner:' \
  '      email: "avyaroslavskiy@miem.hse.ru"' \
  "      password: \"$owner_password\"" \
  '  reverseProxy:' \
  '    trustedHTTPProxies:' \
  '      - "10.244.0.0/16"' \
  '    trustedPeers:' \
  '      - "10.244.0.0/16"' \
  '  store:' \
  '    engine: "postgres"' \
  "    encryptionKey: \"$encryption_key\"" \
  "    dsn: \"host=identity-postgres-rw.identity.svc.cluster.local user=netbird password=$db_password dbname=netbird port=5432 sslmode=require\"" \
  '  activityStore:' \
  '    engine: "postgres"' \
  "    dsn: \"host=identity-postgres-rw.identity.svc.cluster.local user=netbird password=$db_password dbname=netbird port=5432 sslmode=require\"")"

kubectl -n vpn create secret generic netbird-config \
  --from-literal=config.yaml="$config" \
  --from-literal=bootstrap-owner-password="$owner_password" >/dev/null

unset config db_password relay_secret encryption_key owner_password
echo "NetBird secrets created. Values were not written to disk or stdout."
