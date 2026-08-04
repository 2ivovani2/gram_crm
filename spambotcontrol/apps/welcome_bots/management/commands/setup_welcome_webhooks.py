from __future__ import annotations

import asyncio

from aiogram import Bot
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Configure the Gramly Welcome interface webhook and refresh customer webhooks"

    def add_arguments(self, parser):
        parser.add_argument("--customers", action="store_true", help="Also refresh all active customer bot webhooks")
        parser.add_argument("--drop-pending", action="store_true", help="Drop updates already queued by Telegram")

    def handle(self, *args, **options):
        if not settings.WELCOME_BOT_TOKEN:
            raise CommandError("WELCOME_BOT_TOKEN is empty")
        if not settings.WELCOME_WEBHOOK_SECRET:
            raise CommandError("WELCOME_WEBHOOK_SECRET is empty")
        asyncio.run(self._configure(options["customers"], options["drop_pending"]))

    async def _configure(self, customers: bool, drop_pending: bool):
        bot = Bot(settings.WELCOME_BOT_TOKEN)
        try:
            info = await bot.get_me()
            await bot.set_webhook(
                settings.WELCOME_WEBHOOK_URL,
                secret_token=settings.WELCOME_WEBHOOK_SECRET,
                allowed_updates=["message", "callback_query"],
                drop_pending_updates=drop_pending,
                max_connections=20,
            )
            self.stdout.write(self.style.SUCCESS(f"Interface webhook configured for @{info.username}"))
        finally:
            await bot.session.close()

        if not customers:
            return
        from asgiref.sync import sync_to_async
        from apps.welcome_bots.models import ManagedBot
        from apps.welcome_bots.telegram_api import configure_customer_webhook

        managed_bots = await sync_to_async(list)(ManagedBot.objects.filter(is_active=True))
        failures = 0
        for managed in managed_bots:
            try:
                await configure_customer_webhook(managed, drop_pending=drop_pending)
                await sync_to_async(ManagedBot.objects.filter(pk=managed.pk).update)(webhook_configured=True)
                self.stdout.write(f"  OK @{managed.username or managed.telegram_id}")
            except Exception as exc:
                failures += 1
                await sync_to_async(ManagedBot.objects.filter(pk=managed.pk).update)(webhook_configured=False)
                self.stderr.write(f"  FAIL @{managed.username or managed.telegram_id}: {exc}")
        if failures:
            raise CommandError(f"Failed to configure {failures} customer webhook(s)")
