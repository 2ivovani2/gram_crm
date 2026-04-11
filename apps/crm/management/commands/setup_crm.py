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

    def handle(self, *args, **options):
        from apps.crm.services import WorkspaceService

        ws = WorkspaceService.get_or_create_default()
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
