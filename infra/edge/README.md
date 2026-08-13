# Gramly Moscow L4 edge

The Moscow edge at `45.146.131.207` is a data-free compatibility proxy for
networks that cannot reliably reach the VKE LoadBalancer at `45.77.149.91`.

It forwards only:

- TCP/80 to the public ingress;
- TCP/443 to the public ingress without terminating TLS;
- UDP/3478 to NetBird STUN.

TLS certificates, application routing, authentication, workloads, secrets,
databases, and persistent data remain in Kubernetes. The edge must never host
an application workload or an application backup.

Public DNS records point to `45.146.131.207`. NetBird split-DNS records continue
to point public/bootstrap names directly to `45.77.149.91` and private service
names to their private ingress addresses, so enrolled devices bypass the edge.

## Installed files

- `nginx.conf` -> `/etc/nginx/nginx.conf`
- `99-gramly-edge.conf` -> `/etc/sysctl.d/99-gramly-edge.conf`
- `99-gramly-edge-ssh.conf` -> `/etc/ssh/sshd_config.d/99-gramly-edge.conf`
- `gramly-edge-healthcheck` -> `/usr/local/sbin/gramly-edge-healthcheck`
- `gramly-edge-health.service` and `.timer` -> `/etc/systemd/system/`

Inbound firewall access is limited to SSH, HTTP, HTTPS, and NetBird STUN.
Password and keyboard-interactive SSH authentication are disabled.

## Verification

```bash
nginx -t
systemctl is-active nginx gramly-edge-health.timer
ufw status verbose
ss -lntup | grep -E ':(22|80|443|3478) '
```

TLS remains end-to-end to Kubernetes, so a hostname can be tested before DNS
cutover with:

```bash
curl --resolve gramly.tech:443:45.146.131.207 https://gramly.tech/
```
