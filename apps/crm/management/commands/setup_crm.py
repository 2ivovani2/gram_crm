"""
Management command: python manage.py setup_crm

Creates the default GRAMLY workspace and optionally adds a user as OWNER.

Usage:
  python manage.py setup_crm
  python manage.py setup_crm --add-owner <telegram_id>
"""
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create default GRAMLY CRM workspace and optionally add an owner"

    def add_arguments(self, parser):
        parser.add_argument(
            "--add-owner",
            dest="telegram_id",
            type=int,
            metavar="TELEGRAM_ID",
            help="Telegram ID of user to add as OWNER of the default workspace",
        )
        parser.add_argument(
            "--workspace",
            dest="workspace_slug",
            type=str,
            default="gramly",
            help="Workspace slug to operate on (default: gramly)",
        )
        parser.add_argument(
            "--fix-created-by",
            action="store_true",
            help="Create OWNER memberships for all workspaces where created_by has no membership",
        )

    def handle(self, *args, **options):
        from apps.crm.services import WorkspaceService
        from apps.crm.models import Workspace, WorkspaceMembership
        from django.utils import timezone as tz

        # --fix-created-by: scan all workspaces, create OWNER membership for created_by
        if options.get("fix_created_by"):
            fixed = 0
            for ws in Workspace.objects.filter(created_by__isnull=False):
                _, created = WorkspaceMembership.objects.get_or_create(
                    workspace=ws,
                    user=ws.created_by,
                    defaults={"role": "owner", "is_active": True, "joined_at": tz.now()},
                )
                if created:
                    self.stdout.write(self.style.SUCCESS(
                        f"  Created OWNER membership: {ws.created_by.display_name} → {ws.name}"
                    ))
                    fixed += 1
                else:
                    self.stdout.write(f"  Already has membership: {ws.created_by.display_name} → {ws.name}")
            self.stdout.write(self.style.SUCCESS(f"Fixed {fixed} workspace(s)."))
            return

        slug = options.get("workspace_slug", "gramly")
        if slug == "gramly":
            ws = WorkspaceService.get_or_create_default()
        else:
            ws = Workspace.objects.filter(slug=slug).first()
            if not ws:
                raise CommandError(f"Workspace with slug='{slug}' not found.")

        self.stdout.write(self.style.SUCCESS(f"Workspace: {ws.name} (slug={ws.slug})"))

        telegram_id = options.get("telegram_id")
        if telegram_id:
            from apps.users.models import User
            user = User.objects.filter(telegram_id=telegram_id).first()
            if not user:
                raise CommandError(
                    f"User with telegram_id={telegram_id} not found. "
                    "Make sure the user has started the bot first."
                )
            WorkspaceService.add_member(ws, user, role="owner")
            self.stdout.write(
                self.style.SUCCESS(
                    f"Added {user.display_name} (tg_id={telegram_id}) as OWNER of '{ws.name}'"
                )
            )

        self.stdout.write("")
        self.stdout.write("  Members:")
        from apps.crm.models import WorkspaceMembership
        for m in WorkspaceMembership.objects.filter(workspace=ws).select_related("user"):
            status = "✓" if m.is_active else "✗"
            self.stdout.write(f"    {status} {m.user.display_name} [{m.get_role_display()}]")
