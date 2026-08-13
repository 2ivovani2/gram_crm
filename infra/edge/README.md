# Gramly public L4 edge

`nginx.conf` is the temporary compatibility edge for networks that cannot
reliably reach the Vultr Kubernetes LoadBalancer at `45.77.149.91`.

The edge forwards:

- TCP/80 to the public ingress;
- TCP/443 to the public ingress without terminating TLS;
- UDP/3478 to NetBird STUN.

TLS certificates, routing, authentication, application workloads, and all data
remain in Kubernetes. The edge must not contain application secrets.

The TCP timeout must stay aligned with the one-hour timeout configured on the
Vultr LoadBalancer and Traefik because NetBird uses long-lived HTTP/2 and
WebSocket connections.

Public DNS and the corresponding public records in the NetBird split-DNS zone
must point to this edge at `192.248.148.140`. Private application records
continue to resolve to their private ingress addresses for enrolled devices.
