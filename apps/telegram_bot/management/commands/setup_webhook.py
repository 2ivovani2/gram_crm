"""
Management command: register / delete / inspect the Telegram webhook.

The active bot is selected by BOT_ENV:
  BOT_ENV=dev  → TEST_BOT_TOKEN  (use with --url <ngrok_url>/bot/webhook/)
  BOT_ENV=prod → PROD_BOT_TOKEN  (use with TELEGRAM_WEBHOOK_URL in .env)

Usage:
    python manage.py setup_webhook --url https://<ngrok>.ngrok-free.app/bot/webhook/
    python manage.py setup_webhook           # uses TELEGRAM_WEBHOOK_URL from .env
    python manage.py setup_webhook --delete  # remove webhook for the active bot
    python manage.py setup_webhook --info    # show current webhook state

The --url flag takes priority over the TELEGRAM_WEBHOOK_URL setting and is the
recommended way to register the webhook in dev (called automatically by make dev).
"""
import asyncio
import logging
from django.core.management.base import BaseCommand
from django.conf import settings

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Register / delete / inspect the Telegram bot webhook"

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group()
        group.add_argument("--delete", action="store_true", help="Delete the webhook")
        group.add_argument("--info", action="store_true", help="Show current webhook info")
        # --url is not part of the mutually exclusive group: it is used together
        # with the default "set" action, not with --delete or --info.
        parser.add_argument(
            "--url",
            default="",
            help=(
                "Webhook URL to register (overrides TELEGRAM_WEBHOOK_URL from settings). "
                "Typically passed by scripts/dev_up.sh with the current ngrok URL."
            ),
        )

    def handle(self, *args, **options):
        asyncio.run(self._run(options))

    async def _run(self, options):
        from apps.telegram_bot.bot import get_bot
        bot = get_bot()

        token_hint = settings.TELEGRAM_BOT_TOKEN[:10] + "..."
        self.stdout.write(f"Bot env   : {settings.BOT_ENV}")
        self.stdout.write(f"Bot token : {token_hint}")

        try:
            if options["info"]:
                info = await bot.get_webhook_info()
                self.stdout.write(self.style.SUCCESS(f"Webhook URL    : {info.url or '(not set)'}"))
                self.stdout.write(f"Pending updates: {info.pending_update_count}")
                self.stdout.write(f"Last error     : {info.last_error_message or '—'}")

            elif options["delete"]:
                await bot.delete_webhook(drop_pending_updates=True)
                self.stdout.write(self.style.SUCCESS("Webhook deleted."))

            else:
                # --url flag takes priority over the settings value
                webhook_url = options["url"] or settings.TELEGRAM_WEBHOOK_URL
                if not webhook_url:
                    self.stderr.write(self.style.ERROR(
                        "No webhook URL provided.\n"
                        "  Dev:  use 'make dev' — it passes the ngrok URL automatically.\n"
                        "  Prod: set TELEGRAM_WEBHOOK_URL=https://yourdomain.com/bot/webhook/ in .env"
                    ))
                    return

                await bot.set_webhook(
                    url=webhook_url.rstrip("/") + "/",
                    secret_token=settings.TELEGRAM_WEBHOOK_SECRET or None,
                    drop_pending_updates=True,
                )
                self.stdout.write(self.style.SUCCESS(f"Webhook set: {webhook_url}"))
        finally:
            await bot.session.close()
