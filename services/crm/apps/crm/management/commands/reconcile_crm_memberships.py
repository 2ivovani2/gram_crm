from __future__ import annotations

import logging

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from apps.crm.provisioning import (
    ensure_default_workspace_membership,
    inspect_default_workspace_membership,
)
from apps.users.models import User, UserStatus

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Audit or create missing default workspace memberships for SSO-linked CRM users."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--dry-run", action="store_true", help="Print changes without writing them")
        mode.add_argument("--apply", action="store_true", help="Create safe missing memberships")
        parser.add_argument("--telegram-id", type=int, help="Limit reconciliation to one Telegram ID")

    def handle(self, *args, **options):
        users = User.objects.filter(
            is_active=True,
            status=UserStatus.ACTIVE,
        ).exclude(Q(oidc_subject__isnull=True) | Q(oidc_subject="")).order_by("pk")
        if options["telegram_id"]:
            users = users.filter(telegram_id=options["telegram_id"])
            if not users.exists():
                raise CommandError("Active SSO-linked CRM user was not found")

        counts: dict[str, int] = {}
        for user in users.iterator():
            result = (
                ensure_default_workspace_membership(user)
                if options["apply"]
                else inspect_default_workspace_membership(user)
            )
            counts[result.action] = counts.get(result.action, 0) + 1
            self.stdout.write(
                " ".join([
                    f"crm_user_id={user.pk}",
                    f"telegram_id={user.telegram_id}",
                    f"role={user.role}",
                    f"action={result.action}",
                    f"desired_role={result.desired_role or '-'}",
                    f"current_role={result.current_role or '-'}",
                ])
            )
            logger.info(
                "crm_membership_reconciliation crm_user_id=%s telegram_id=%s mode=%s action=%s",
                user.pk,
                user.telegram_id,
                "apply" if options["apply"] else "dry_run",
                result.action,
            )

        summary = " ".join(f"{key}={value}" for key, value in sorted(counts.items())) or "no_users=1"
        self.stdout.write(self.style.SUCCESS(f"CRM membership reconciliation complete: {summary}"))
