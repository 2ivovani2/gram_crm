#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${KUBECONFIG:-}" ]]; then
  echo "KUBECONFIG is not set; refusing to use an implicit cluster context." >&2
  exit 1
fi

if [[ ! -f "$KUBECONFIG" ]]; then
  echo "Kubeconfig does not exist: $KUBECONFIG" >&2
  exit 1
fi

chmod 600 "$KUBECONFIG"

echo "Kubernetes context: $(kubectl config current-context)"
kubectl cluster-info
kubectl get nodes -o wide

server_minor="$(kubectl version -o json | sed -n 's/.*\"minor\": \"\([0-9][0-9]*\)\".*/\1/p' | tail -1)"
if [[ "$server_minor" != "36" ]]; then
  echo "Expected Kubernetes 1.36, got a different server version; stopping." >&2
  exit 1
fi

if kubectl get deploy,statefulset,daemonset --all-namespaces \
  -o jsonpath='{range .items[*]}{.metadata.namespace}{"/"}{.metadata.name}{"\n"}{end}' \
  | grep -Ev '^(kube-system|irsa-system|cert-manager|traefik-public|traefik-private|cnpg-system|identity)/' \
  | grep -q .; then
  echo "Unexpected non-system workloads found; review them before bootstrap." >&2
  exit 1
fi

echo "Preflight passed. No production application data was changed."
