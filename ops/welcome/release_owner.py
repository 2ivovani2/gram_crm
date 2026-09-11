"""Scoped, phased owner-worker cutover. No SSO, CRM, DNS or historical replay.

Requires PyYAML and kubectl. Default is read-only; --apply requires a fresh,
completed CNPG backup. Run the existing run-cnpg-backup.sh before this command.
Rollback must drain owner workers before disabling the separate-worker flag;
do not remove receipts or replay historical owner inbox rows.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
K = shlex.split(os.environ.get("KUBECTL_COMMAND", "kubectl"))
NAMESPACE = "gramly-welcome"


def kubectl(*args: str, data: Any = None) -> str:
    return subprocess.run([*K, *args], input=json.dumps(data) if data is not None else None,
                          text=True, capture_output=True, check=True).stdout.strip()


def patch_worker(name: str, image: str, *, separate: bool = False) -> None:
    container: dict[str, Any] = {"name": "api" if name == "gramly-welcome-api" else "worker", "image": image}
    if separate:
        container["env"] = [{"name": "WELCOME_SEPARATE_OWNER_WORKER", "value": "true"}]
    if name in {"gramly-welcome-worker-events", "gramly-welcome-worker-delivery"}:
        container["ports"] = [{"name": "health", "containerPort": 9091}]
        container["startupProbe"] = {"httpGet": {"path": "/health/live", "port": "health"},
                                     "periodSeconds": 5, "failureThreshold": 24}
        container["livenessProbe"] = {"httpGet": {"path": "/health/live", "port": "health"},
                                      "periodSeconds": 10, "failureThreshold": 3}
        container["readinessProbe"] = {"httpGet": {"path": "/health/ready", "port": "health"},
                                       "periodSeconds": 10, "failureThreshold": 2}
    patch = {"spec": {"template": {"spec": {"containers": [container]}}}}
    print(kubectl("-n", NAMESPACE, "patch", "deployment", name, "--type=strategic", "-p", json.dumps(patch)), flush=True)
    print(kubectl("-n", NAMESPACE, "rollout", "status", f"deployment/{name}", "--timeout=5m"), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--backup", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"git\.gramly\.tech/gramly/gramly-welcome@sha256:[0-9a-f]{64}", args.image):
        raise SystemExit("Only an immutable image from the private Gramly Forgejo registry is allowed")
    backup = json.loads(kubectl("-n", "gramly-crm", "get", "backup", args.backup, "-o", "json"))
    stopped = backup.get("status", {}).get("stoppedAt", "")
    if (backup.get("spec", {}).get("cluster", {}).get("name") != "gramly-crm-postgres"
            or backup.get("status", {}).get("phase") != "completed" or not stopped
            or datetime.fromisoformat(stopped.replace("Z", "+00:00")) < datetime.now(UTC) - timedelta(hours=6)):
        raise SystemExit("A completed backup of the Welcome CNPG cluster, less than six hours old, is required")
    workloads = ["gramly-welcome-api", *[f"gramly-welcome-worker-{kind}" for kind in
                                       ("events", "delivery", "billing", "notifications")]]
    previous = {}
    for name in workloads:
        deployment = json.loads(kubectl("-n", NAMESPACE, "get", "deployment", name, "-o", "json"))
        previous[name] = [container["image"] for container in deployment["spec"]["template"]["spec"]["containers"]]
    print(json.dumps({"previous_images": previous, "next_image": args.image, "backup": args.backup}), flush=True)
    if not args.apply:
        return
    manifest = kubectl("kustomize", str(ROOT / "infra/kubernetes/overlays/production/welcome-migrations"))
    job = next(item for item in yaml.safe_load_all(manifest) if item and item["kind"] == "Job")
    job["metadata"]["name"] = f"welcome-owner-migrate-{args.image[-12:]}"
    job["spec"]["template"]["spec"]["containers"][0]["image"] = args.image
    print(kubectl("apply", "-f", "-", data=job), flush=True)
    print(kubectl("-n", NAMESPACE, "wait", "job/" + job["metadata"]["name"],
                  "--for=condition=Complete", "--timeout=5m"), flush=True)
    patch_worker(workloads[0], args.image)
    # Crucial first-cutover barrier: old event pods finish before any dedicated
    # owner consumer starts. Webhooks remain durable while owner updates wait.
    patch_worker(workloads[1], args.image, separate=True)
    rendered = kubectl("kustomize", str(ROOT / "infra/kubernetes/overlays/production/welcome-cutover"))
    for resource in yaml.safe_load_all(rendered):
        if not resource:
            continue
        name = resource["metadata"]["name"]
        if name not in ("gramly-welcome-worker-owner", "allow-worker-metrics"):
            continue
        if resource["kind"] == "Deployment":
            resource["spec"]["template"]["spec"]["containers"][0]["image"] = args.image
        print(kubectl("apply", "-f", "-", data=resource), flush=True)
    print(kubectl("-n", NAMESPACE, "rollout", "status", "deployment/gramly-welcome-worker-owner", "--timeout=5m"), flush=True)
    for name in workloads[2:]:
        patch_worker(name, args.image, separate=name.endswith("delivery"))
    patch = {"spec": {"jobTemplate": {"spec": {"template": {"spec": {
        "containers": [{"name": "maintenance", "image": args.image}],
    }}}}}}
    print(kubectl("-n", NAMESPACE, "patch", "cronjob", "gramly-welcome-maintenance",
                  "--type=strategic", "-p", json.dumps(patch)), flush=True)
    print(kubectl("apply", "-f", str(ROOT / "infra/kubernetes/apps/observability/gramly-welcome-alerts.yaml")), flush=True)
    print("Owner cutover complete. Check queue/response metrics and a TEST owner conversation; no historical replay.")


if __name__ == "__main__":
    main()
