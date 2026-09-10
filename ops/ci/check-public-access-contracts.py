"""Validate public service routing preserves the application authentication edge."""
from pathlib import Path

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
print("Public SSO routing contracts passed for all seven services.")
