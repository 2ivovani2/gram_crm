"""Durable ingestion of report attachments from Telegram into private storage."""
from __future__ import annotations

import io
import logging
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import PurePath
from typing import Optional

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import IntegrityError, transaction
from django.db.models import Max

from apps.control.models import (
    EmployeeReport,
    ReportMedia,
    ReportMediaStatus,
    ReportSubmissionReceipt,
)
from apps.users.models import User

logger = logging.getLogger(__name__)

SUPPORTED_TYPES = {"photo", "video", "document", "animation", "sticker", "video_note"}


class ReportMediaSaveError(ValueError):
    pass


@dataclass(frozen=True)
class PreparedReportMedia:
    telegram_file_id: str
    storage_key: str
    media_type: str
    mime_type: str
    original_filename: str
    file_size: int


def message_attachment(message):
    """Return (kind, Telegram file object, safe filename, MIME) or None."""
    kind = message.content_type
    if kind not in SUPPORTED_TYPES:
        return None
    if kind == "photo" and message.photo:
        obj = message.photo[-1]
        return kind, obj, f"photo-{obj.file_unique_id}.jpg", "image/jpeg"

    obj = getattr(message, kind, None)
    if obj is None:
        return None
    fallback = {
        "video": ("video.mp4", "video/mp4"),
        "animation": ("animation.mp4", "video/mp4"),
        "sticker": ("sticker.webp", "image/webp"),
        "video_note": ("video-note.mp4", "video/mp4"),
        "document": ("document.bin", "application/octet-stream"),
    }
    fallback_name, fallback_mime = fallback[kind]
    filename = PurePath(getattr(obj, "file_name", "") or fallback_name).name
    mime_type = getattr(obj, "mime_type", "") or fallback_mime
    return kind, obj, filename, mime_type


@transaction.atomic
def _create_media(
    *,
    report_id: int,
    revision: int,
    telegram_file_id: str,
    storage_key: str,
    media_type: str,
    mime_type: str,
    original_filename: str,
    file_size: int,
    status: str,
    error_message: str = "",
) -> ReportMedia:
    report = EmployeeReport.objects.select_for_update().get(pk=report_id)
    if report.current_revision != revision:
        raise ReportMediaSaveError("Версия отчёта уже изменилась.")
    position = (
        ReportMedia.objects.filter(report=report, revision=revision)
        .aggregate(value=Max("position"))["value"]
        or 0
    ) + 1
    return ReportMedia.objects.create(
        report=report,
        revision=revision,
        position=position,
        telegram_file_id=telegram_file_id,
        storage_key=storage_key,
        media_type=media_type,
        mime_type=mime_type,
        original_filename=original_filename,
        file_size=file_size,
        status=status,
        error_message=error_message,
    )


async def save_message_attachment(bot, message, report: EmployeeReport) -> ReportMedia | None:
    """Download one supported Telegram attachment and persist a private copy."""
    attachment = message_attachment(message)
    if attachment is None:
        return None
    kind, telegram_file, filename, mime_type = attachment
    limit = settings.REPORT_MEDIA_MAX_BYTES
    declared_size = int(getattr(telegram_file, "file_size", 0) or 0)
    if declared_size and declared_size > limit:
        raise ReportMediaSaveError(
            f"Файл больше допустимых {limit // (1024 * 1024)} МБ."
        )

    storage_key = ""
    try:
        target = io.BytesIO()
        await bot.download(telegram_file, destination=target)
        body = target.getvalue()
        if len(body) > limit:
            raise ReportMediaSaveError(
                f"Файл больше допустимых {limit // (1024 * 1024)} МБ."
            )
        safe_name = PurePath(filename).name
        key = (
            f"reports/{report.pk}/revision-{report.current_revision}/"
            f"{uuid.uuid4().hex}-{safe_name}"
        )
        storage_key = await sync_to_async(default_storage.save)(key, ContentFile(body))
        return await sync_to_async(_create_media)(
            report_id=report.pk,
            revision=report.current_revision,
            telegram_file_id=telegram_file.file_id,
            storage_key=storage_key,
            media_type=kind,
            mime_type=mime_type,
            original_filename=safe_name,
            file_size=len(body),
            status=ReportMediaStatus.READY,
        )
    except ReportMediaSaveError:
        if storage_key:
            await sync_to_async(default_storage.delete)(storage_key)
        raise
    except Exception:
        logger.exception("Failed to persist report media report=%s type=%s", report.pk, kind)
        if storage_key:
            await sync_to_async(default_storage.delete)(storage_key)
        await sync_to_async(_create_media)(
            report_id=report.pk,
            revision=report.current_revision,
            telegram_file_id=getattr(telegram_file, "file_id", ""),
            storage_key="",
            media_type=kind,
            mime_type=mime_type,
            original_filename=filename,
            file_size=declared_size,
            status=ReportMediaStatus.FAILED,
            error_message="Файл не удалось сохранить. Попробуйте отправить его ещё раз.",
        )
        raise ReportMediaSaveError("Файл не удалось сохранить. Попробуйте отправить его ещё раз.")


async def prepare_message_attachment(bot, message) -> PreparedReportMedia | None:
    """Persist Telegram bytes before the report transaction starts."""
    attachment = message_attachment(message)
    if attachment is None:
        return None
    kind, telegram_file, filename, mime_type = attachment
    limit = settings.REPORT_MEDIA_MAX_BYTES
    declared_size = int(getattr(telegram_file, "file_size", 0) or 0)
    if declared_size and declared_size > limit:
        raise ReportMediaSaveError(
            f"Файл больше допустимых {limit // (1024 * 1024)} МБ."
        )

    storage_key = ""
    try:
        target = io.BytesIO()
        await bot.download(telegram_file, destination=target)
        body = target.getvalue()
        if len(body) > limit:
            raise ReportMediaSaveError(
                f"Файл больше допустимых {limit // (1024 * 1024)} МБ."
            )
        safe_name = PurePath(filename).name
        key = f"reports/staged/{uuid.uuid4().hex}-{safe_name}"
        storage_key = await sync_to_async(default_storage.save)(key, ContentFile(body))
        return PreparedReportMedia(
            telegram_file_id=telegram_file.file_id,
            storage_key=storage_key,
            media_type=kind,
            mime_type=mime_type,
            original_filename=safe_name,
            file_size=len(body),
        )
    except ReportMediaSaveError:
        if storage_key:
            await sync_to_async(default_storage.delete)(storage_key)
        raise
    except Exception as exc:
        logger.exception("Failed to stage report media type=%s", kind)
        if storage_key:
            await sync_to_async(default_storage.delete)(storage_key)
        raise ReportMediaSaveError(
            "Файл не удалось сохранить. Попробуйте отправить его ещё раз."
        ) from exc


@transaction.atomic
def _commit_report_message(
    *,
    user: User,
    submission_key: str,
    template,
    text: str,
    file_id: str,
    file_type: str,
    original_filename: str,
    report_id: Optional[int],
    explicit_report_date: Optional[date],
    active_report_id: Optional[int],
    prepared_media: PreparedReportMedia | None,
) -> tuple[EmployeeReport, bool]:
    """Atomically persist report content, media metadata and idempotency receipt."""
    from apps.control.services import ReportService

    receipt = (
        ReportSubmissionReceipt.objects.select_related("report")
        .filter(submission_key=submission_key)
        .first()
    )
    if receipt is not None:
        return receipt.report, False

    if active_report_id:
        current = EmployeeReport.objects.select_related("template").get(
            pk=active_report_id,
            user=user,
        )
        report = ReportService.append_to_current_revision(current, user, text=text)
    else:
        report = ReportService.submit_report(
            user=user,
            template=template,
            text=text,
            file_id=file_id,
            file_type=file_type,
            original_filename=original_filename,
            report_id=report_id,
            explicit_report_date=explicit_report_date,
        )

    if prepared_media is not None:
        _create_media(
            report_id=report.pk,
            revision=report.current_revision,
            telegram_file_id=prepared_media.telegram_file_id,
            storage_key=prepared_media.storage_key,
            media_type=prepared_media.media_type,
            mime_type=prepared_media.mime_type,
            original_filename=prepared_media.original_filename,
            file_size=prepared_media.file_size,
            status=ReportMediaStatus.READY,
        )
    ReportSubmissionReceipt.objects.create(
        submission_key=submission_key,
        report=report,
    )
    return report, True


async def persist_report_message(
    *,
    bot,
    message,
    user: User,
    submission_key: str,
    template=None,
    text: str = "",
    file_id: str = "",
    file_type: str = "text",
    original_filename: str = "",
    report_id: Optional[int] = None,
    explicit_report_date: Optional[date] = None,
    active_report_id: Optional[int] = None,
) -> tuple[EmployeeReport, bool]:
    """Stage bytes, then commit all database records as one unit."""
    existing = await sync_to_async(
        lambda: ReportSubmissionReceipt.objects.select_related("report")
        .filter(submission_key=submission_key)
        .first()
    )()
    if existing is not None:
        return existing.report, False

    prepared = await prepare_message_attachment(bot, message)
    try:
        return await sync_to_async(_commit_report_message)(
            user=user,
            submission_key=submission_key,
            template=template,
            text=text,
            file_id=file_id,
            file_type=file_type,
            original_filename=original_filename,
            report_id=report_id,
            explicit_report_date=explicit_report_date,
            active_report_id=active_report_id,
            prepared_media=prepared,
        )
    except IntegrityError:
        # Concurrent replay: the winning transaction owns the receipt.
        existing = await sync_to_async(
            lambda: ReportSubmissionReceipt.objects.select_related("report").get(
                submission_key=submission_key
            )
        )()
        if prepared and prepared.storage_key:
            await sync_to_async(default_storage.delete)(prepared.storage_key)
        return existing.report, False
    except Exception:
        if prepared and prepared.storage_key:
            await sync_to_async(default_storage.delete)(prepared.storage_key)
        raise
