import datetime as dt
from zoneinfo import ZoneInfo

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.urls import reverse

from apps.control.models import ReportMedia, ReportMediaStatus, ReportTemplate
from apps.control.report_media import message_attachment
from apps.control.services import ReportService
from apps.users.models import User, UserRole, UserStatus


pytestmark = pytest.mark.django_db
MSK = ZoneInfo("Europe/Moscow")


def _user(telegram_id, role=UserRole.WORKER):
    return User.objects.create(
        telegram_id=telegram_id,
        username=f"media-{telegram_id}",
        role=role,
        status=UserStatus.ACTIVE,
    )


def _report(worker):
    template = ReportTemplate.objects.create(name="Media report", deadline_time=dt.time(23, 59))
    template.assigned_users.add(worker)
    return ReportService.submit_report(worker, template, text="report")


def test_media_endpoint_requires_crm_permission_and_checks_report_parent(client, tmp_path, monkeypatch):
    worker = _user(83001)
    admin = _user(83002, UserRole.ADMIN)
    outsider = _user(83003)
    report = _report(worker)

    storage = FileSystemStorage(location=tmp_path)
    monkeypatch.setattr("apps.control.views.default_storage", storage)
    key = storage.save("reports/test/image.jpg", ContentFile(b"image-body"))
    media = ReportMedia.objects.create(
        report=report,
        storage_key=key,
        media_type="photo",
        mime_type="image/jpeg",
        original_filename="proof.jpg",
        file_size=10,
        status=ReportMediaStatus.READY,
    )
    url = reverse("control:report_media", args=[report.pk, media.pk])

    assert client.get(url).status_code == 302
    client.force_login(outsider, backend="django.contrib.auth.backends.ModelBackend")
    assert client.get(url).status_code == 403
    client.force_login(admin, backend="django.contrib.auth.backends.ModelBackend")
    response = client.get(url)
    assert response.status_code == 200
    assert b"".join(response.streaming_content) == b"image-body"
    wrong_parent = reverse("control:report_media", args=[report.pk + 999, media.pk])
    assert client.get(wrong_parent).status_code == 404


def test_media_endpoint_supports_byte_ranges_and_download(client, tmp_path, monkeypatch):
    worker = _user(83011)
    admin = _user(83012, UserRole.ADMIN)
    report = _report(worker)

    storage = FileSystemStorage(location=tmp_path)
    monkeypatch.setattr("apps.control.views.default_storage", storage)
    key = storage.save("reports/test/video.mp4", ContentFile(b"0123456789"))
    media = ReportMedia.objects.create(
        report=report,
        storage_key=key,
        media_type="video",
        mime_type="video/mp4",
        original_filename="clip.mp4",
        file_size=10,
    )
    client.force_login(admin, backend="django.contrib.auth.backends.ModelBackend")
    url = reverse("control:report_media", args=[report.pk, media.pk])
    response = client.get(url, HTTP_RANGE="bytes=2-5")
    assert response.status_code == 206
    assert response["Content-Range"] == "bytes 2-5/10"
    assert b"".join(response.streaming_content) == b"2345"

    download = client.get(f"{url}?download=1")
    assert download.status_code == 200
    assert "attachment" in download["Content-Disposition"]
    download.close()


def test_resubmission_moves_to_new_media_revision(monkeypatch):
    worker = _user(83021)
    admin = _user(83022, UserRole.ADMIN)
    template = ReportTemplate.objects.create(name="Daily", deadline_time=dt.time(23, 0))
    template.assigned_users.add(worker)
    monkeypatch.setattr(
        "apps.control.services.timezone.now",
        lambda: dt.datetime(2026, 8, 16, 20, 0, tzinfo=MSK),
    )
    report = ReportService.submit_report(worker, template, text="v1")
    ReportMedia.objects.create(report=report, revision=1, position=1, original_filename="old.jpg")
    ReportService.reject_report(report, admin, "fix")
    updated = ReportService.submit_report(worker, template, text="v2", report_id=report.pk)
    ReportMedia.objects.create(report=updated, revision=2, position=1, original_filename="new.jpg")

    assert updated.current_revision == 2
    assert list(updated.media_files.filter(revision=updated.current_revision).values_list("original_filename", flat=True)) == ["new.jpg"]
    assert updated.media_files.count() == 2


def test_failed_media_is_a_safe_display_state():
    worker = _user(83031)
    report = _report(worker)
    media = ReportMedia.objects.create(
        report=report,
        status=ReportMediaStatus.FAILED,
        media_type="document",
        original_filename="broken.pdf",
        error_message="Файл недоступен",
    )

    assert not media.storage_key
    assert not media.is_image
    assert not media.is_video


def test_attachment_detection_excludes_audio_and_sanitizes_document_name():
    document = type(
        "TelegramDocument",
        (),
        {
            "file_id": "file-1",
            "file_name": "../../invoice.pdf",
            "mime_type": "application/pdf",
        },
    )()
    message = type(
        "TelegramMessage",
        (),
        {"content_type": "document", "document": document},
    )()
    kind, obj, filename, mime = message_attachment(message)
    assert kind == "document"
    assert obj is document
    assert filename == "invoice.pdf"
    assert mime == "application/pdf"

    audio = type("TelegramMessage", (), {"content_type": "audio"})()
    voice = type("TelegramMessage", (), {"content_type": "voice"})()
    assert message_attachment(audio) is None
    assert message_attachment(voice) is None
