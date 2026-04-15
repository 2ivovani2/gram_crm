"""
Business logic for client/link/assignment management.

Key operations:
  AssignmentService.auto_assign(client_link)
    → find best available worker → create LinkAssignment + replace WorkLink
    → if none found → return None (caller notifies admins)

  AssignmentService.unassign(assignment, reason)
    → mark assignment inactive, archive WorkLink, optionally notify worker

  AssignmentService.deactivate_link(client_link, note)
    → deactivate link, unassign active worker, notify assigned worker + admins

  JoinService.submit(user, message)
    → create JoinRequest (if no pending exists)

  JoinService.approve(request, admin_user)
    → activate user, close request

  JoinService.reject(request, admin_user)
    → close request with REJECTED status

Auto-assignment rule:
  Pick an ACTIVE worker (role=WORKER or CURATOR) using two-step selection:
    1. Find the minimum active-assignment count across all eligible workers.
    2. Collect all workers sharing that minimum load.
    3. Choose one at random from that pool (fair distribution, no deterministic tie-breaking).
  Worker gets a new WorkLink set to the client link's URL (count starts at 0).
"""
from __future__ import annotations

import logging
import random
from typing import Optional

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


# ─── Join Request Service ──────────────────────────────────────────────────────

class JoinServiceError(Exception):
    pass


class JoinService:

    @staticmethod
    def get_pending(user) -> Optional["JoinRequest"]:
        from apps.users.models import JoinRequest, JoinRequestStatus
        return JoinRequest.objects.filter(user=user, status=JoinRequestStatus.PENDING).first()

    @staticmethod
    def get_any_request(user) -> Optional["JoinRequest"]:
        """Return the latest request (any status) for display."""
        from apps.users.models import JoinRequest
        return JoinRequest.objects.filter(user=user).order_by("-created_at").first()

    @staticmethod
    def submit(user, message: str = "") -> "JoinRequest":
        """Create a new join request. Raises JoinServiceError if one already pending."""
        from apps.users.models import JoinRequest, JoinRequestStatus
        if JoinRequest.objects.filter(user=user, status=JoinRequestStatus.PENDING).exists():
            raise JoinServiceError("У вас уже есть активная заявка на рассмотрении.")
        if user.is_activated:
            raise JoinServiceError("Вы уже активированы.")
        return JoinRequest.objects.create(user=user, message=message)

    @staticmethod
    @transaction.atomic
    def approve(request: "JoinRequest", admin_user) -> None:
        from apps.users.models import JoinRequestStatus
        if not request.is_pending:
            raise JoinServiceError("Заявка уже обработана.")
        request.user.activate()
        request.status = JoinRequestStatus.APPROVED
        request.reviewed_by = admin_user
        request.reviewed_at = timezone.now()
        request.save(update_fields=["status", "reviewed_by", "reviewed_at"])

    @staticmethod
    @transaction.atomic
    def reject(request: "JoinRequest", admin_user) -> None:
        from apps.users.models import JoinRequestStatus
        if not request.is_pending:
            raise JoinServiceError("Заявка уже обработана.")
        request.status = JoinRequestStatus.REJECTED
        request.reviewed_by = admin_user
        request.reviewed_at = timezone.now()
        request.save(update_fields=["status", "reviewed_by", "reviewed_at"])

    @staticmethod
    def get_pending_list():
        from apps.users.models import JoinRequest, JoinRequestStatus
        return JoinRequest.objects.filter(
            status=JoinRequestStatus.PENDING
        ).select_related("user").order_by("-created_at")

    @staticmethod
    def count_pending() -> int:
        from apps.users.models import JoinRequest, JoinRequestStatus
        return JoinRequest.objects.filter(status=JoinRequestStatus.PENDING).count()


# ─── Assignment Service ────────────────────────────────────────────────────────

class AssignmentService:

    # Selection rule: prefer workers with 0 active assignments, then fewest assignments.
    # Workers with ACTIVE status, not blocked. WORKER or CURATOR roles.
    INACTIVITY_DAYS = 3

    @staticmethod
    def _find_best_worker():
        """
        Pick a worker with the lowest current active-assignment count.
        When multiple workers share the minimum load, choose one at random
        (fair distribution — no deterministic tie-breaking by pk / created_at).
        """
        from apps.users.models import User, UserRole, UserStatus
        from django.db.models import Count, Q

        candidates = list(
            User.objects
            .filter(
                role__in=[UserRole.WORKER, UserRole.CURATOR],
                status=UserStatus.ACTIVE,
                is_blocked_bot=False,
                is_activated=True,
            )
            .annotate(active_assignments=Count(
                "link_assignments",
                filter=Q(link_assignments__is_active=True),
            ))
            .order_by("active_assignments")   # only sort by load; no secondary key
        )

        if not candidates:
            return None

        min_load = candidates[0].active_assignments
        pool = [w for w in candidates if w.active_assignments == min_load]
        return random.choice(pool)

    @staticmethod
    @transaction.atomic
    def auto_assign(client_link: "ClientLink") -> Optional["LinkAssignment"]:
        """
        Find best available worker, assign to client_link.
        Returns the new LinkAssignment, or None if no worker found.
        Sets the worker's WorkLink URL to client_link.url.
        """
        from apps.clients.models import LinkAssignment
        from apps.users.services import UserService

        # Sanity: only one active assignment per link
        if client_link.assignments.filter(is_active=True).exists():
            logger.warning("auto_assign called but link %s already has active assignment", client_link.pk)
            return client_link.assignments.filter(is_active=True).first()

        worker = AssignmentService._find_best_worker()
        if not worker:
            return None

        # Replace work link so count starts at 0 for this assignment
        work_link, _ = UserService.replace_work_link(worker, client_link.url)

        assignment = LinkAssignment.objects.create(
            client_link=client_link,
            worker=worker,
            work_link=work_link,
            last_count_updated_at=timezone.now(),
        )
        logger.info("auto_assign: link %s → worker %s (assignment %s)", client_link.pk, worker.pk, assignment.pk)
        return assignment

    @staticmethod
    @transaction.atomic
    def manual_assign(client_link: "ClientLink", worker) -> "LinkAssignment":
        """Admin manually assigns a specific worker to a link."""
        from apps.clients.models import LinkAssignment, UnassignReason
        from apps.users.services import UserService

        # Unassign current worker if any
        existing = client_link.assignments.filter(is_active=True).first()
        if existing:
            AssignmentService.unassign(existing, UnassignReason.REASSIGNED)

        work_link, _ = UserService.replace_work_link(worker, client_link.url)
        assignment = LinkAssignment.objects.create(
            client_link=client_link,
            worker=worker,
            work_link=work_link,
            last_count_updated_at=timezone.now(),
        )
        return assignment

    @staticmethod
    @transaction.atomic
    def unassign(assignment: "LinkAssignment", reason: str) -> None:
        """Unassign worker from link, archive their WorkLink."""
        from apps.users.services import UserService
        from apps.clients.models import UnassignReason

        assignment.is_active = False
        assignment.unassigned_at = timezone.now()
        assignment.unassign_reason = reason
        assignment.save(update_fields=["is_active", "unassigned_at", "unassign_reason"])

        # Archive the WorkLink (freeze attracted_count), give worker empty URL
        if reason != UnassignReason.LINK_DEACTIVATED:
            UserService.replace_work_link(assignment.worker, "")
        else:
            # On deactivation just clear URL without creating a new active link
            UserService.clear_work_url(assignment.worker)

        logger.info("unassign: assignment %s, reason=%s", assignment.pk, reason)

    @staticmethod
    @transaction.atomic
    def deactivate_link(client_link: "ClientLink", note: str = "") -> list:
        """
        Deactivate a client link.
        - Set link status to INACTIVE
        - Unassign active worker (reason: LINK_DEACTIVATED)
        Returns list of worker telegram_ids that were unassigned (for notifications).
        """
        from apps.clients.models import UnassignReason

        client_link.deactivate(note=note)

        unassigned_workers = []
        active = client_link.assignments.filter(is_active=True).select_related("worker")
        for assignment in active:
            unassigned_workers.append(assignment.worker.telegram_id)
            AssignmentService.unassign(assignment, UnassignReason.LINK_DEACTIVATED)

        return unassigned_workers

    @staticmethod
    def touch_count_updated(assignment: "LinkAssignment") -> None:
        """Call this whenever admin updates attracted_count for the assigned worker."""
        assignment.last_count_updated_at = timezone.now()
        assignment.save(update_fields=["last_count_updated_at"])

    @staticmethod
    def get_inactive_assignments(days: int = INACTIVITY_DAYS):
        """Return active assignments where last_count_updated_at is older than `days` days."""
        from apps.clients.models import LinkAssignment
        cutoff = timezone.now() - timezone.timedelta(days=days)
        return (
            LinkAssignment.objects
            .filter(
                is_active=True,
                last_count_updated_at__lt=cutoff,
                last_count_updated_at__isnull=False,
            )
            .select_related("worker", "client_link__client")
        )
