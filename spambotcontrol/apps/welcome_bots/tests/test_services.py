from datetime import timedelta
from unittest.mock import patch

from aiogram.types import User
from django.test import TestCase
from django.utils import timezone

from apps.welcome_bots.crypto import TokenDecryptionError
from apps.welcome_bots.models import (
    Channel,
    Contact,
    GreetingDelivery,
    JoinRequest,
    ManagedBot,
    Owner,
    WelcomeMessage,
    WelcomeMessageVersion,
    WelcomeDraft,
)
from apps.welcome_bots.services import (
    append_album_item,
    contact_from_user,
    create_join_request,
    disable_auto_approve,
    enable_auto_approve,
    finalize_album,
    owned_bot,
    schedule_greeting,
    statistics,
)


class WelcomeServiceTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(telegram_id=100, username="owner")
        self.other = Owner.objects.create(telegram_id=200, username="other")
        self.bot = ManagedBot.objects.create(
            owner=self.owner,
            telegram_id=1000,
            username="customer_bot",
            display_name="Customer Bot",
            token_ciphertext="placeholder",
        )
        self.bot.set_token("1000:secret-token-value")
        self.bot.save(update_fields=("token_ciphertext",))
        self.channel = Channel.objects.create(
            bot=self.bot,
            telegram_id=-100123,
            title="Channel",
            is_active=True,
        )
        tg_user = User(id=501, is_bot=False, first_name="Anna", language_code="ru")
        self.contact = contact_from_user(self.bot, tg_user)

    def test_customer_token_is_encrypted_and_round_trips(self):
        self.bot.refresh_from_db()
        self.assertNotIn("secret-token-value", self.bot.token_ciphertext)
        self.assertEqual(self.bot.get_token(), "1000:secret-token-value")
        self.bot.token_ciphertext = "v1:broken"
        with self.assertRaises(TokenDecryptionError):
            self.bot.get_token()

    def test_owner_cannot_access_another_owners_bot(self):
        self.assertEqual(owned_bot(self.owner.id, self.bot.id), self.bot)
        with self.assertRaises(ManagedBot.DoesNotExist):
            owned_bot(self.other.id, self.bot.id)

    @patch("apps.welcome_bots.services._enqueue_delivery")
    def test_greeting_uses_immutable_delay_and_version_snapshot(self, enqueue):
        message = WelcomeMessage.objects.create(bot=self.bot)
        first = WelcomeMessageVersion.objects.create(
            message=message,
            version=1,
            author_telegram_id=self.owner.telegram_id,
            payload={"type": "text", "text": "Hello"},
        )
        message.active_version = first
        message.save(update_fields=("active_version",))
        self.bot.welcome_delay_seconds = 300
        self.bot.save(update_fields=("welcome_delay_seconds",))

        with self.captureOnCommitCallbacks(execute=True):
            delivery = schedule_greeting(self.bot, self.channel, self.contact, "event:1")
        self.bot.welcome_delay_seconds = 0
        self.bot.save(update_fields=("welcome_delay_seconds",))

        delivery.refresh_from_db()
        self.assertEqual(delivery.delay_snapshot_seconds, 300)
        self.assertEqual(delivery.version, first)
        self.assertGreater(delivery.due_at, timezone.now() + timedelta(seconds=290))
        enqueue.assert_called_once_with(delivery.id, 300)
        self.assertEqual(schedule_greeting(self.bot, self.channel, self.contact, "event:1").id, delivery.id)

    @patch("apps.welcome_bots.services._enqueue_delivery")
    def test_adjacent_join_events_do_not_send_duplicate_greetings(self, enqueue):
        message = WelcomeMessage.objects.create(bot=self.bot)
        version = WelcomeMessageVersion.objects.create(
            message=message,
            version=1,
            author_telegram_id=self.owner.telegram_id,
            payload={"type": "text", "text": "Hello"},
        )
        message.active_version = version
        message.save(update_fields=("active_version",))
        first = schedule_greeting(self.bot, self.channel, self.contact, "join-request:10")
        second = schedule_greeting(self.bot, self.channel, self.contact, "chat-member:11")
        self.assertEqual(first.id, second.id)
        self.assertEqual(GreetingDelivery.objects.count(), 1)

    def test_album_is_finalized_in_telegram_message_order(self):
        draft = append_album_item(
            self.bot,
            self.owner,
            "album-1",
            20,
            {"type": "photo", "caption": "second"},
            {"media_type": "photo", "storage_key": "second.jpg", "original_name": "second.jpg", "mime_type": "image/jpeg", "size": 2},
        )
        append_album_item(
            self.bot,
            self.owner,
            "album-1",
            10,
            {"type": "photo", "caption": "first"},
            {"media_type": "photo", "storage_key": "first.jpg", "original_name": "first.jpg", "mime_type": "image/jpeg", "size": 1},
        )
        version = finalize_album(draft.id)
        self.assertEqual([item["caption"] for item in version.payload["items"]], ["first", "second"])
        self.assertEqual(list(version.media.values_list("storage_key", flat=True)), ["first.jpg", "second.jpg"])
        self.assertFalse(WelcomeDraft.objects.filter(pk=draft.id).exists())

    @patch("apps.welcome_bots.services._enqueue_approval")
    def test_auto_approval_queues_accumulated_requests_and_disable_preserves_them(self, enqueue):
        request = JoinRequest.objects.create(
            bot=self.bot,
            channel=self.channel,
            contact=self.contact,
            telegram_update_id=10,
            status=JoinRequest.Status.PENDING,
        )
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(enable_auto_approve(self.bot), 1)
        request.refresh_from_db()
        self.assertEqual(request.status, JoinRequest.Status.SCHEDULED)
        self.assertEqual(request.delay_snapshot_seconds, 0)
        enqueue.assert_called_once_with(request.id, 0)

        self.assertEqual(disable_auto_approve(self.bot), 1)
        request.refresh_from_db()
        self.assertEqual(request.status, JoinRequest.Status.PENDING)
        self.assertIsNone(request.due_at)

    def test_statistics_are_calculated_from_current_rows(self):
        self.contact.delivery_status = Contact.DeliveryStatus.LIVE
        self.contact.save(update_fields=("delivery_status",))
        Contact.objects.create(
            bot=self.bot,
            telegram_id=502,
            first_name="Blocked",
            language_code="en",
            delivery_status=Contact.DeliveryStatus.DEAD,
        )
        data = statistics(self.bot)
        self.assertEqual((data["total"], data["live"], data["dead"]), (2, 1, 1))
        self.assertEqual({x["language_code"] for x in data["languages"]}, {"ru", "en"})


class ModelConstraintTests(TestCase):
    def test_same_telegram_bot_cannot_be_registered_twice(self):
        first = Owner.objects.create(telegram_id=1)
        second = Owner.objects.create(telegram_id=2)
        ManagedBot.objects.create(
            owner=first,
            telegram_id=999,
            display_name="One",
            token_ciphertext="x",
        )
        from django.db import IntegrityError

        with self.assertRaises(IntegrityError):
            with self.atomic():
                ManagedBot.objects.create(
                    owner=second,
                    telegram_id=999,
                    display_name="Two",
                    token_ciphertext="y",
                )

    @staticmethod
    def atomic():
        from django.db import transaction

        return transaction.atomic()
