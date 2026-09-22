from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_delivery_keda_counts_every_worker_queue_and_only_runnable_chain_steps() -> None:
    manifest = (
        REPO_ROOT / "infra/kubernetes/base/welcome-autoscaling/workers.yaml"
    ).read_text(encoding="utf-8")

    for table in (
        "client_message",
        "broadcast_recipient",
        "delivery_operation",
        "greeting_delivery",
        "join_request",
        "join_request_activation",
        "rotation_recommendation",
    ):
        assert f"FROM {table}" in manifest
    assert "operation.depends_on_operation_id IS NULL OR dependency.status = 'sent'" in manifest
    assert "SUM(pending)::numeric / 25" in manifest
    assert "minReplicaCount: 2" in manifest
    assert "maxReplicaCount: 20" in manifest

    alerts = (
        REPO_ROOT / "infra/kubernetes/apps/observability/gramly-welcome-alerts.yaml"
    ).read_text(encoding="utf-8")
    assert "GramlyWelcomeDeliveryScheduleLag" in alerts
    assert "GramlyWelcomeDeliveryBacklogAtCapacity" in alerts
    assert "GramlyWelcomeJoinRequestWindowExpired" in alerts


def test_expiring_greetings_are_claimed_before_broadcasts() -> None:
    worker = (
        REPO_ROOT / "services/welcome/src/gramly_welcome/workers/delivery.py"
    ).read_text(encoding="utf-8")
    delivery = (
        REPO_ROOT / "services/welcome/src/gramly_welcome/flow_delivery.py"
    ).read_text(encoding="utf-8")

    queue_block = worker[worker.index("queues: list"):worker.index("try:", worker.index("queues: list"))]
    assert queue_block.index("DeliveryOperation") < queue_block.index("BroadcastRecipient")
    assert "FlowDelivery.target_expires_at.asc().nulls_last()" in delivery
    assert 'case((FlowDelivery.target_expires_at.is_not(None), 0), else_=1)' in delivery
