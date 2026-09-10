"""Run on PostgreSQL in CI; SQLite cannot validate row-lock semantics."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.core.exceptions import PermissionDenied
from django.db import close_old_connections, connection

from apps.crm.access import StaleAccess, has_permission, update_access
from apps.crm.models import AccessChange, Workspace, WorkspaceMembership
from apps.users.models import User

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def people():
    if connection.vendor != "postgresql":
        pytest.skip("Requires real PostgreSQL row locks")
    ws = Workspace.objects.create(slug="gramly", name="Local concurrency")
    users = []
    for i, role in enumerate(["admin", "admin", "worker"]):
        user = User.objects.create_user(username=f"lock-test-{i}", telegram_id=897000+i, role=role, status="active")
        WorkspaceMembership.objects.create(workspace=ws, user=user, role="owner" if role == "admin" else "viewer")
        users.append(user)
    return ws, users


def parallel(*jobs):
    barrier = Barrier(len(jobs))
    def call(job):
        close_old_connections()
        try:
            barrier.wait(timeout=5)
            job()
            return "saved"
        except StaleAccess:
            return "stale"
        except PermissionDenied:
            return "denied"
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        return list(pool.map(call, jobs))


def test_competing_saves_cannot_overwrite_other_admin(people):
    ws, (a, b, target) = people
    results = parallel(
        lambda: update_access(a, target, "worker", {"reports.view": True}, 0, workspace=ws),
        lambda: update_access(b, target, "worker", {"templates.view": True}, 0, workspace=ws),
    )
    assert sorted(results) == ["saved", "stale"]
    assert AccessChange.objects.filter(target=target).count() == 1
    assert WorkspaceMembership.objects.get(user=target).access_version == 1


def test_competing_admin_demotions_leave_one_active_admin(people):
    ws, (a, b, target) = people
    results = parallel(
        lambda: update_access(a, b, "worker", {}, 0, workspace=ws, confirm_role_change=True),
        lambda: update_access(b, a, "worker", {}, 0, workspace=ws, confirm_role_change=True),
    )
    assert sorted(results) == ["denied", "saved"]
    assert sum(has_permission(user, "access.manage", ws) for user in [a, b]) == 1


def test_two_first_assignments_create_one_membership(people):
    ws, (a, b, target) = people
    WorkspaceMembership.objects.filter(user=target).delete()
    results = parallel(
        lambda: update_access(a, target, "worker", {"reports.view": True}, 0, workspace=ws),
        lambda: update_access(b, target, "worker", {"templates.view": True}, 0, workspace=ws),
    )
    assert sorted(results) == ["saved", "stale"]
    assert WorkspaceMembership.objects.filter(user=target).count() == 1
