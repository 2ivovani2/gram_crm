from __future__ import annotations

import logging

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.crm.access import effective_permissions
from apps.crm.models import Workspace, WorkspaceMembership

from apps.crm.provisioning import (
    ensure_default_workspace_membership,
    inspect_default_workspace_membership,
)
from apps.users.models import User, UserStatus

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Audit CRM-owned access and create missing memberships, independently of SSO."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--dry-run", action="store_true", help="Print changes without writing them")
        mode.add_argument("--apply", action="store_true", help="Create safe missing memberships")
        parser.add_argument("--telegram-id", type=int, help="Limit reconciliation to one Telegram ID")

    def handle(self, *args, **options):
        users = User.objects.filter(
            is_active=True,
            status=UserStatus.ACTIVE,
        ).order_by("pk")
        if options["telegram_id"]:
            users = users.filter(telegram_id=options["telegram_id"])
            if not users.exists():
                raise CommandError("Active CRM user was not found")

        if options["apply"]:
            # All-or-nothing preflight; serializes against interactive access
            # changes. Never silently interpret an unusual legacy assignment.
            with transaction.atomic():
                Workspace.objects.select_for_update().get(slug="gramly")
                locked_users = list(users.select_for_update())
                ambiguous = [u.pk for u in locked_users if inspect_default_workspace_membership(u).action == "ambiguous_legacy_role"]
                if ambiguous:
                    raise CommandError(f"Ambiguous legacy assignments; no changes applied. CRM IDs: {ambiguous}")
                self._reconcile(locked_users, apply=True)
            return
        self._reconcile(users, apply=False)

    def _reconcile(self, users, *, apply):

        counts: dict[str, int] = {}
        for user in users:
            result = (
                ensure_default_workspace_membership(user)
                if apply
                else inspect_default_workspace_membership(user)
            )
            counts[result.action] = counts.get(result.action, 0) + 1
            membership = WorkspaceMembership.objects.filter(pk=result.membership_id).first()
            future = effective_permissions(user.role, membership.permission_overrides if membership else {})
            if result.action in {"revoked", "skipped_role", "workspace_missing", "ambiguous_legacy_role"}:
                future = frozenset()
            self.stdout.write(
                " ".join([
                    f"crm_user_id={user.pk}",
                    f"telegram_id={user.telegram_id}",
                    f"role={user.role}",
                    f"action={result.action}",
                    f"desired_role={result.desired_role or '-'}",
                    f"current_role={result.current_role or '-'}",
                    f"future_permissions={','.join(sorted(future)) or '-'}",
                ])
            )
            logger.info(
                "crm_membership_reconciliation crm_user_id=%s telegram_id=%s mode=%s action=%s",
                user.pk,
                user.telegram_id,
                "apply" if apply else "dry_run",
                result.action,
            )

        summary = " ".join(f"{key}={value}" for key, value in sorted(counts.items())) or "no_users=1"
        self.stdout.write(self.style.SUCCESS(f"CRM membership reconciliation complete: {summary}"))
