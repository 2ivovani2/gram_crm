import datetime as dt

import pytest
from django.core.files.storage import FileSystemStorage
from django.urls import reverse

from apps.control.models import (
    EmployeeReport,
    ReportSubmissionDelivery,
    ReportSubmissionReceipt,
    ReportTemplate,
)
from apps.control.report_media import _commit_report_message, persist_report_message
from apps.control.services import ReportService
from apps.control.tasks import notify_report_submission_task
from apps.control.bot.worker_handlers import cb_submit_report
from apps.users.models import User, UserRole, UserStatus


pytestmark = pytest.mark.django_db


def _user(telegram_id: int, username: str, role=UserRole.WORKER, status=UserStatus.ACTIVE):
    return User.objects.create(
        telegram_id=telegram_id,
        telegram_username=username,
        username=f"tg-{telegram_id}",
        role=role,
        status=status,
        is_active=True,
        is_activated=True,
    )


def _template(worker, creator=None):
    template = ReportTemplate.objects.create(
        name="Daily",
        deadline_time=dt.time(23, 59),
        created_by=creator,
    )
    template.assigned_users.add(worker)
    return template


def test_report_message_is_idempotent_and_visible_in_common_pool():
    worker = _user(92001, "report-worker")
    owner = _user(92002, "template-owner", UserRole.ADMIN)
    another_admin = _user(92003, "another-admin", UserRole.ADMIN)
    template = _template(worker, owner)

    report, created = _commit_report_message(
        user=worker,
        submission_key="telegram:92001:55",
        template=template,
        text="ready",
        file_id="",
        file_type="text",
        original_filename="",
        report_id=None,
        explicit_report_date=None,
        active_report_id=None,
        prepared_media=None,
    )
    replay, replay_created = _commit_report_message(
        user=worker,
        submission_key="telegram:92001:55",
        template=template,
        text="must not be appended",
        file_id="",
        file_type="text",
        original_filename="",
        report_id=None,
        explicit_report_date=None,
        active_report_id=None,
        prepared_media=None,
    )

    assert created is True
    assert replay_created is False
    assert replay.pk == report.pk
    assert EmployeeReport.objects.count() == 1
    assert ReportSubmissionReceipt.objects.count() == 1
    assert ReportService.get_all_pending().filter(pk=report.pk).exists()
    assert not ReportService.get_pending_for_admin(another_admin).filter(pk=report.pk).exists()


def test_completion_enqueues_one_durable_notification(monkeypatch, django_capture_on_commit_callbacks):
    worker = _user(92011, "complete-worker")
    admin = _user(92012, "complete-admin", UserRole.ADMIN)
    template = _template(worker, admin)
    report = ReportService.submit_report(worker, template, text="complete me")
    queued = []
    monkeypatch.setattr(
        "apps.control.tasks.queue_report_submission_notification",
        lambda report_id: queued.append(report_id) or True,
    )

    with django_capture_on_commit_callbacks(execute=True):
        completed = ReportService.complete_submission(report.pk, worker)
    with django_capture_on_commit_callbacks(execute=True):
        ReportService.complete_submission(report.pk, worker)

    completed.refresh_from_db()
    assert completed.submission_completed_at is not None
    assert queued == [report.pk]


def test_submission_delivery_retries_only_unsent_recipients(monkeypatch):
    worker = _user(92021, "delivery-worker")
    admin = _user(92022, "delivery-admin", UserRole.ADMIN)
    template = _template(worker, admin)
    report = ReportService.submit_report(worker, template, text="delivery")
    report.submission_completed_at = report.last_submission_at
    report.save(update_fields=["submission_completed_at"])
    sent_to = []
    monkeypatch.setattr(
        "apps.control.tasks._send_message_sync",
        lambda telegram_id, text, reply_markup=None: sent_to.append(telegram_id) or True,
    )

    first = notify_report_submission_task.run(report.pk)
    second = notify_report_submission_task.run(report.pk)

    assert first["sent"] == 1
    assert second["sent"] == 0
    assert sent_to == [admin.telegram_id]
    delivery = ReportSubmissionDelivery.objects.get(report=report, recipient=admin)
    assert delivery.status == ReportSubmissionDelivery.Status.SENT
    assert delivery.attempts == 1


def test_submission_notification_falls_back_from_inactive_template_owner(monkeypatch):
    worker = _user(92023, "fallback-worker")
    inactive_owner = _user(
        92024,
        "inactive-template-owner",
        UserRole.ADMIN,
        status=UserStatus.INACTIVE,
    )
    active_admin = _user(92025, "fallback-admin", UserRole.ADMIN)
    template = _template(worker, inactive_owner)
    report = ReportService.submit_report(worker, template, text="delivery")
    report.submission_completed_at = report.last_submission_at
    report.save(update_fields=["submission_completed_at"])
    sent_to = []
    monkeypatch.setattr(
        "apps.control.tasks._send_message_sync",
        lambda telegram_id, text, reply_markup=None: sent_to.append(telegram_id) or True,
    )

    result = notify_report_submission_task.run(report.pk)

    assert result["sent"] == 1
    assert sent_to == [active_admin.telegram_id]
    assert not ReportSubmissionDelivery.objects.filter(
        report=report,
        recipient=inactive_owner,
    ).exists()


def test_users_list_defaults_to_active_and_archive_is_separate(client):
    admin = _user(92031, "list-admin", UserRole.ADMIN)
    active = _user(92032, "active-worker")
    archived = _user(92033, "archived-worker", status=UserStatus.INACTIVE)
    client.force_login(admin, backend="django.contrib.auth.backends.ModelBackend")

    active_response = client.get(reverse("control:users"), {"role": UserRole.WORKER})
    archive_response = client.get(
        reverse("control:users"),
        {"role": UserRole.WORKER, "status": UserStatus.INACTIVE},
    )

    assert active_response.status_code == 200
    assert active.telegram_username in active_response.content.decode()
    assert archived.telegram_username not in active_response.content.decode()
    assert archive_response.status_code == 200
    assert archived.telegram_username in archive_response.content.decode()
    assert active.telegram_username not in archive_response.content.decode()


def test_archive_action_is_idempotent_and_preserves_history(client, monkeypatch):
    admin = _user(92041, "archive-admin", UserRole.ADMIN)
    worker = _user(92042, "archive-worker")
    template = _template(worker, admin)
    report = ReportService.submit_report(worker, template, text="history")
    revoked = []
    monkeypatch.setattr(
        "apps.crm.identity.terminate_user_sessions",
        lambda user_id: revoked.append(user_id) or 0,
    )
    client.force_login(admin, backend="django.contrib.auth.backends.ModelBackend")
    url = reverse("control:user_edit", args=[worker.pk])

    assert client.post(url, {"action": "delete"}).status_code == 302
    assert client.post(url, {"action": "delete"}).status_code == 302
    worker.refresh_from_db()

    assert worker.status == UserStatus.INACTIVE
    assert EmployeeReport.objects.filter(pk=report.pk).exists()
    assert revoked == [worker.pk]


@pytest.mark.asyncio
async def test_submit_callback_is_answered_before_database_work(monkeypatch):
    worker = SimpleAsyncUser(pk=92051)
    events = []

    class Message:
        async def edit_text(self, text, reply_markup=None):
            events.append(("edit", text))

    class Callback:
        message = Message()

        async def answer(self, *args, **kwargs):
            events.append(("answer", None))

    class State:
        async def clear(self):
            events.append(("clear", None))

    def load_templates(user):
        assert events[0][0] == "answer"
        return []

    monkeypatch.setattr(ReportService, "get_active_templates_for_user", load_templates)
    await cb_submit_report(Callback(), worker, State())

    assert events[0][0] == "answer"
    assert any(kind == "edit" and "Нет доступных отчётов" in text for kind, text in events)


class SimpleAsyncUser:
    def __init__(self, pk):
        self.pk = pk


@pytest.mark.asyncio
async def test_media_and_report_metadata_commit_together(tmp_path, monkeypatch):
    worker = await User.objects.acreate(
        telegram_id=92061,
        telegram_username="atomic-media",
        username="tg-92061",
        role=UserRole.WORKER,
        status=UserStatus.ACTIVE,
    )
    template = await ReportTemplate.objects.acreate(
        name="Media",
        deadline_time=dt.time(23, 59),
    )
    await template.assigned_users.aadd(worker)
    storage = FileSystemStorage(location=tmp_path)
    monkeypatch.setattr("apps.control.report_media.default_storage", storage)

    document = type(
        "Document",
        (),
        {
            "file_id": "telegram-file",
            "file_unique_id": "unique-file",
            "file_name": "proof.pdf",
            "mime_type": "application/pdf",
            "file_size": 5,
        },
    )()
    message = type(
        "Message",
        (),
        {"content_type": "document", "document": document},
    )()

    class Bot:
        async def download(self, telegram_file, destination):
            destination.write(b"proof")

    report, created = await persist_report_message(
        bot=Bot(),
        message=message,
        user=worker,
        submission_key="telegram:92061:1",
        template=template,
        text="caption",
        file_id=document.file_id,
        file_type="document",
        original_filename=document.file_name,
    )

    assert created is True
    media = await report.media_files.aget()
    assert media.status == "ready"
    assert media.storage_key
    assert storage.exists(media.storage_key)
