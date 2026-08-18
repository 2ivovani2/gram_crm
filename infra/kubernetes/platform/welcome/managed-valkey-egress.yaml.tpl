apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-managed-valkey
  namespace: __WELCOME_NAMESPACE__
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/name: gramly-welcome-worker-delivery
  policyTypes: [Egress]
  egress:
    - to:
        - ipBlock: {cidr: __WELCOME_VALKEY_CIDR__}
      ports:
        - {protocol: TCP, port: __WELCOME_VALKEY_PORT__}
