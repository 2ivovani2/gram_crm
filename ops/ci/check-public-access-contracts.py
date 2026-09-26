"""Validate public service routing preserves the application authentication edge."""
from pathlib import Path
import subprocess

import yaml

ROOT = Path(__file__).resolve().parents[2]
INFRA = ROOT / "infra/kubernetes"


def documents(path):
    return [item for item in yaml.safe_load_all((INFRA / path).read_text()) if item]


expected = {
    "crm.gramly.tech": ("gramly-crm", "gramly-crm-web", "base/crm-web/app.yaml"),
    "git.gramly.tech": ("devtools", "forgejo", "apps/devtools/forgejo.yaml"),
    "tasks.gramly.tech": ("devtools", "vikunja", "apps/devtools/vikunja.yaml"),
    "docs.gramly.tech": ("devtools", "outline", "apps/devtools/outline.yaml"),
    "grafana.gramly.tech": ("observability", "kube-prometheus-stack-grafana", "apps/observability/routes.yaml"),
    "cluster.gramly.tech": ("observability", "observability-oauth2-proxy", "apps/observability/routes.yaml"),
    "hello-admin.gramly.tech": ("gramly-welcome", "gramly-welcome-admin-auth", "base/welcome-web/routes.yaml"),
}
gateway = next(d for d in documents("platform/gateway/public-gateway.yaml")
               if d["metadata"]["name"] == "gramly-public-access")
listeners = {listener["hostname"]: listener for listener in gateway["spec"]["listeners"]}
values = documents("platform/gateway/traefik-public-values.yaml")[0]
for host, (namespace, backend, path) in expected.items():
    listener = listeners[host]
    assert listener["protocol"] == "HTTPS"
    assert listener["allowedRoutes"]["namespaces"]["selector"]["matchLabels"] == {
        "kubernetes.io/metadata.name": namespace
    }, host
    route = next(d for d in documents(path) if d.get("kind") == "HTTPRoute"
                 and host in d["spec"].get("hostnames", []))
    assert route["spec"]["parentRefs"] == [{
        "name": "gramly-public-access", "namespace": "traefik-public",
        "sectionName": listener["name"],
    }], host
    assert {b["name"] for rule in route["spec"]["rules"]
            for b in rule.get("backendRefs", [])} == {backend}, host
    for provider in ("kubernetesGateway", "kubernetesCRD"):
        assert namespace in values["providers"][provider]["namespaces"], host

assert all(d.get("metadata", {}).get("name") != "gramly-private-access-gate"
           for d in documents("base/public-web/public-web.yaml"))
forgejo = next(d for d in documents("apps/devtools/forgejo.yaml") if d["kind"] == "Deployment")
forgejo_env = {v["name"]: v.get("value") for v in forgejo["spec"]["template"]["spec"]["containers"][0]["env"]}
assert forgejo_env["FORGEJO__service__REQUIRE_SIGNIN_VIEW"] == "true"
assert forgejo_env["FORGEJO__service__DISABLE_REGISTRATION"] == "true"
for path in (ROOT / "ops/welcome/release_production.sh",
             INFRA / "scripts/deploy-outline.sh", INFRA / "scripts/deploy-observability.sh"):
    text = path.read_text()
    assert "ensure-private-app-records" not in text, path
    assert "deploy-private-admin-gateway" not in text, path
assert "Application.objects.filter(slug='netbird')" not in (
    INFRA / "apps/identity/configure-access-control.sh").read_text()

# Runner job networks nest inside the VKE Calico interface (MTU 1380).
# Setting only --mtu fixes docker0 but leaves auto-created job bridges at 1500.
runner = next(d for d in documents("apps/devtools/forgejo-runner.yaml")
              if d["kind"] == "Deployment")
docker = next(c for c in runner["spec"]["template"]["spec"]["containers"]
              if c["name"] == "docker")
assert "--mtu=1380" in docker["args"], "DinD default bridge must fit Calico MTU"
assert "--default-network-opt=bridge=com.docker.network.driver.mtu=1380" in docker["args"], (
    "Per-job Docker networks must inherit Calico-compatible MTU"
)
# Telegram must reach the public ingress directly, independently of the edge
# used by human browser traffic. Both registration/reconciliation paths must
# agree; otherwise an event worker can silently restore the failing edge pin.
cutover = documents("overlays/production/welcome-cutover/kustomization.yaml")[0]
webhook_pins = {}
for patch in cutover["patches"]:
    if patch["target"]["kind"] != "Deployment":
        continue
    pins = [op["value"]["value"] for op in yaml.safe_load(patch["patch"])
            if isinstance(op.get("value"), dict)
            and op["value"].get("name") == "WELCOME_WEBHOOK_IP_ADDRESS"]
    if pins:
        assert len(pins) == 1, "Duplicate Telegram webhook IP pin"
        webhook_pins[patch["target"]["name"]] = pins[0]
assert webhook_pins == {
    "gramly-welcome-api": "45.77.149.91",
    "gramly-welcome-worker-events": "45.77.149.91",
    "gramly-welcome-worker-owner": "45.77.149.91",
}, "Telegram webhooks must bypass the browser edge via the direct public ingress"
# Check rendered PodSpecs, not only patch text: a typo in a regex target must
# not silently leave one image consumer unable to pull after rescheduling.
def rendered(path):
    return [d for d in yaml.safe_load_all(subprocess.check_output(
        ["kubectl", "kustomize", str(INFRA / path)], text=True)) if d]


def pull_secrets(resource):
    spec = resource["spec"]
    if resource["kind"] == "CronJob":
        spec = spec["jobTemplate"]["spec"]
    return [s["name"] for s in spec["template"]["spec"].get("imagePullSecrets", [])]


expected_consumers = {
    "gramly-welcome-api", "gramly-welcome-web", "gramly-welcome-worker-events",
    "gramly-welcome-worker-owner", "gramly-welcome-worker-view",
    "gramly-welcome-worker-delivery", "gramly-welcome-worker-delivery-relay",
    "gramly-welcome-worker-interactive", "gramly-welcome-worker-bulk",
    "gramly-welcome-worker-billing",
    "gramly-welcome-worker-notifications", "gramly-welcome-maintenance",
    "gramly-welcome-migrate",
}
seen_consumers = set()
for overlay in ("welcome-cutover", "welcome-migrations"):
    for resource in rendered(f"overlays/production/{overlay}"):
        if resource["kind"] not in {"Deployment", "CronJob", "Job"}:
            continue
        name = resource["metadata"]["name"]
        secrets = pull_secrets(resource)
        if name in expected_consumers:
            assert sorted(secrets) == ["gramly-forgejo-registry", "gramly-welcome-registry"], name
            seen_consumers.add(name)
        else:
            assert "gramly-forgejo-registry" not in secrets, name
assert seen_consumers == expected_consumers
staging_registry_consumers = {"gramly-welcome-worker-view-staging", "gramly-welcome-migrate-staging"}
for overlay in ("overlays/staging", "overlays/staging/welcome-migrations"):
    for resource in rendered(overlay):
        if resource["kind"] in {"Deployment", "CronJob", "Job"}:
            name = resource["metadata"]["name"]
            has_forgejo_secret = "gramly-forgejo-registry" in pull_secrets(resource)
            assert has_forgejo_secret == (name in staging_registry_consumers), name
print("Public SSO, Telegram ingress and production pull-secret contracts passed.")
