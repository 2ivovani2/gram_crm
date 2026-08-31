"""Safe workspace provisioning for Authentik-backed CRM identities."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from apps.crm.models import CRMRole, Workspace, WorkspaceMembership
from apps.users.models import UserRole, UserStatus

logger = logging.getLogger(__name__)

DEFAULT_WORKSPACE_SLUG = "gramly"
OIDC_WORKSPACE_ROLE_MAP = {
    UserRole.ADMIN: CRMRole.OWNER,
    UserRole.ACCOUNTANT: CRMRole.FINANCE,
    UserRole.CURATOR: CRMRole.VIEWER,
    UserRole.WORKER: CRMRole.VIEWER,
}


@dataclass(frozen=True)
class MembershipProvisionResult:
    action: str
    desired_role: str | None = None
    current_role: str | None = None
    membership_id: int | None = None
    workspace_id: int | None = None


def inspect_default_workspace_membership(user) -> MembershipProvisionResult:
    """Describe what automatic provisioning would do without mutating data."""

    desired_role = OIDC_WORKSPACE_ROLE_MAP.get(user.role)
    if not user.is_active or user.status != UserStatus.ACTIVE:
        return MembershipProvisionResult("skipped_inactive", desired_role=desired_role)
    if not user.oidc_subject:
        return MembershipProvisionResult("skipped_unlinked", desired_role=desired_role)
    if desired_role is None:
        return MembershipProvisionResult("skipped_role")

    workspace = Workspace.objects.filter(
        slug=DEFAULT_WORKSPACE_SLUG,
        is_active=True,
    ).first()
    if workspace is None:
        return MembershipProvisionResult("workspace_missing", desired_role=desired_role)

    membership = WorkspaceMembership.objects.filter(
        workspace=workspace,
        user=user,
    ).first()
    if membership is None:
        return MembershipProvisionResult(
            "create",
            desired_role=desired_role,
            workspace_id=workspace.pk,
        )
    return MembershipProvisionResult(
        "exists" if membership.is_active else "revoked",
        desired_role=desired_role,
        current_role=membership.role,
        membership_id=membership.pk,
        workspace_id=workspace.pk,
    )


def ensure_default_workspace_membership(user) -> MembershipProvisionResult:
    """Create a missing default membership without changing explicit access.

    Existing active memberships keep their role. Existing inactive memberships
    represent an administrator revocation and are never reactivated here.
    """

    with transaction.atomic():
        locked_user = user.__class__.objects.select_for_update().get(pk=user.pk)
        result = inspect_default_workspace_membership(locked_user)
        if result.action != "create":
            logger.info(
                "crm_workspace_provisioning crm_user_id=%s action=%s desired_role=%s current_role=%s",
                user.pk,
                result.action,
                result.desired_role or "none",
                result.current_role or "none",
            )
            return result

        membership, created = WorkspaceMembership.objects.get_or_create(
            workspace_id=result.workspace_id,
            user=locked_user,
            defaults={
                "role": result.desired_role,
                "is_active": True,
                "joined_at": timezone.now(),
            },
        )
        if not created:
            concurrent_result = MembershipProvisionResult(
                "exists" if membership.is_active else "revoked",
                desired_role=result.desired_role,
                current_role=membership.role,
                membership_id=membership.pk,
                workspace_id=membership.workspace_id,
            )
            logger.info(
                "crm_workspace_provisioning crm_user_id=%s action=%s desired_role=%s current_role=%s",
                locked_user.pk,
                concurrent_result.action,
                concurrent_result.desired_role,
                concurrent_result.current_role,
            )
            return concurrent_result
        created_result = MembershipProvisionResult(
            "created",
            desired_role=result.desired_role,
            current_role=membership.role,
            membership_id=membership.pk,
            workspace_id=membership.workspace_id,
        )
        logger.info(
            "crm_workspace_provisioned crm_user_id=%s workspace_id=%s membership_id=%s role=%s",
            locked_user.pk,
            membership.workspace_id,
            membership.pk,
            membership.role,
        )
        return created_result
